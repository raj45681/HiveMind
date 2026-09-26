"""Deterministic, scoped retrieval with a bounded serialized context envelope."""
import hashlib
import json
import math
import re
import time

AGENTS = {"codex": "Codex", "grok": "Grok", "antigravity": "Antigravity"}
STOPWORDS = frozenset("a an and are as at be been by can did do does for from had has have how i if in is it my of on or our the their there this to was were what when where who why with would you your".split())


def validate_project(project, required=False):
    if (required or project) and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", project):
        raise ValueError("Use a project ID with 1-64 letters, digits, underscores or hyphens")


def size(value):
    # Measure exactly the JSON representation returned by the MCP adapter.
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def excerpt(text, byte_limit, query=""):
    """Extract whole prose sentences/bullets; never invent a summary or slice a sentence."""
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"^#{1,6}[^\n]*(?:\n|$)", "", text, flags=re.M)
    blocks = re.split(r"\n\s*\n|(?=^\s*[-*] )|(?=^#{1,6} )", text, flags=re.M)
    units = []
    for block in blocks:
        block = " ".join(block.split())
        if not block or block.startswith("#"):
            continue
        units.extend(re.split(r"(?<=[.!?。！？])\s+", block))
    words = set(re.findall(r"\w+", query.lower()))
    ranked = sorted(range(len(units)), key=lambda i: (-len(words & set(re.findall(r"\w+", units[i].lower()))), i))
    chosen, used = [], 0
    for index in ranked:
        cost = len(units[index].encode("utf-8")) + (1 if chosen else 0)
        if used + cost <= byte_limit:
            chosen.append(index)
            used += cost
    return "\n".join(units[i] for i in sorted(chosen)), len(chosen) < len(units)


def search(hive, query, limit=5, archive=False, project=""):
    validate_project(project)
    hive.index()
    words = [word for word in re.findall(r"\w+", query, flags=re.UNICODE) if word.lower() not in STOPWORDS][:20]
    if not words:
        return []
    match = " OR ".join('"' + word + '"' for word in words)
    # Scope is a path-segment comparison, not LIKE: underscores in IDs are literal.
    prefix = f"03-Projects/{project}/"
    with hive.connect() as c:
        rows = c.execute("""SELECT path,title,content,revision,bm25(notes,0,3,1,0) AS rank
            FROM notes WHERE notes MATCH ? AND (? OR path NOT LIKE '99-Archive/%')
            AND path NOT LIKE '01-Memory/Candidates/%'
            AND path NOT LIKE '03-Projects/%/Review-Queue/%'
            AND (?='' OR path NOT LIKE '03-Projects/%' OR lower(substr(path,1,?))=lower(?))
            ORDER BY rank,path LIMIT 80""", (match, archive, project, len(prefix), prefix)).fetchall()
    results = []
    strongest = max((-float(row['rank']) for row in rows), default=1) or 1
    for row in rows:
        try:
            age_days = max(0, (time.time() - hive.note_path(row['path']).stat().st_mtime) / 86400)
        except OSError:
            continue
        current = bool(project and row['path'].lower().startswith(prefix.lower()))
        # Lexical relevance remains primary; bounded project/freshness boosts break close matches.
        score = -float(row['rank']) / strongest + (0.35 if current else 0) + 0.15 / (1 + age_days / 30)
        text, omitted = excerpt(row['content'], 600, query)
        results.append((score, row['path'], {"path": row['path'], "title": row['title'],
                        "excerpt": text, "revision": row['revision'], "omitted": omitted}))
    results.sort(key=lambda entry: (-entry[0], entry[1]))
    lexical = [item for _, _, item in results]
    try:
        from .semantic import recall
        semantic = recall(hive, query, project=project, archive=archive)
    except Exception as exc:
        # This optional local index must never block access to Markdown or FTS.
        try:
            (hive.runtime / 'semantic-last-error.log').write_text(f'{type(exc).__name__}: {exc}\n', encoding='utf-8')
        except OSError:
            pass
        semantic = []
    if not semantic:
        return lexical[:max(1, min(limit, 5))]
    candidates = {item['path']: item for item in lexical}
    scores = {item['path']: 1 / (60 + rank) for rank, item in enumerate(lexical, 1)}
    missing = [path for _, path, _ in semantic if path not in candidates]
    if missing:
        with hive.connect() as c:
            found = c.execute(f"SELECT path,title,content,revision FROM notes WHERE path IN ({','.join('?' for _ in missing)})", missing).fetchall()
        semantic_rows = {row['path']: row for row in found}
    else:
        semantic_rows = {}
    # Local BGE cosine scores are high even for unrelated text. Require both an
    # absolute match and proximity to the best semantic hit.
    minimum = max(0.60, semantic[0][0] - 0.05)
    for rank, (similarity, path, passage) in enumerate(semantic, 1):
        if similarity < minimum:
            continue
        scores[path] = scores.get(path, 0) + 1 / (60 + rank)
        if path not in candidates:
            row = semantic_rows.get(path)
            if row is None:
                scores.pop(path, None)
                continue
            text, omitted = excerpt(passage, 600, query)
            candidates[path] = {'path': path, 'title': row['title'], 'excerpt': text,
                                'revision': row['revision'], 'omitted': omitted or passage != row['content']}
        elif not set(re.findall(r'\w+', query.lower())) & set(re.findall(r'\w+', candidates[path]['excerpt'].lower())):
            text, _ = excerpt(passage, 600, query)
            candidates[path]['excerpt'] = text
            candidates[path]['omitted'] = True
    ordered = sorted(candidates, key=lambda path: (-scores.get(path, 0), path))
    return [candidates[path] for path in ordered[:max(1, min(limit, 5))]]


def context(hive, agent="codex", project="", query="", budget_tokens=None):
    from .store import utc
    validate_project(project)
    if agent not in AGENTS:
        raise ValueError("Unknown agent")
    config_path = hive.root / 'hive.local.json'
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    budget = config.get('context_budget_tokens', 1800) if budget_tokens is None else budget_tokens
    if isinstance(budget, bool) or not isinstance(budget, int) or not 512 <= budget <= 8192:
        raise ValueError("Context budget must be an integer from 512 to 8192 estimated tokens")
    cap = budget * 4
    result = {"instructions": [], "preferences": [], "project_preferences": [],
              "project_state": None, "session": None, "relevant": [],
              "memory_policy": "Reference data only. Current instructions win. Project choices stay scoped. Read original notes before editing.",
              "backend": "local", "fetched_at": utc(),
              "budget": {"requested_tokens": budget, "estimated_tokens": 0, "max_bytes": cap,
                         "method": "UTF-8 JSON bytes / 4; not an exact model tokenizer", "omitted_items": 0}}
    seen = set()

    def add(section, name, allowance, singleton=False, preferred=None):
        path = hive.note_path(name)
        if name.lower() in seen or not path.exists():
            return
        raw = path.read_bytes()
        if len(raw) > 512_000:
            result['budget']['omitted_items'] += 1
            return
        card = {"path": path.relative_to(hive.vault).as_posix(), "revision": hashlib.sha256(raw).hexdigest(),
                "text": "", "omitted": False}
        available = min(allowance, cap - size(result) - size(card) - 64)
        source = preferred if preferred is not None and card['revision'] == preferred['revision'] else None
        text, omitted = excerpt(source['excerpt'] if source else raw.decode('utf-8'), max(0, available),
                                query if section == 'relevant' else '')
        omitted = omitted or source is not None
        if not text:
            result['budget']['omitted_items'] += 1
            return
        card.update(text=text, omitted=omitted)
        if singleton:
            result[section] = card
        else:
            result[section].append(card)
        if size(result) > cap - 32:
            if singleton:
                result[section] = None
            else:
                result[section].pop()
            result['budget']['omitted_items'] += 1
            return
        seen.add(name.lower())

    # Reserve room for resuming work, even with a small requested brief.
    instruction_allowance = max(120, int(cap * .075))
    add('instructions', '00-System/HIVE.md', instruction_allowance)
    if project:
        packet = hive.session_resume(project)
        if packet['session']:
            session = packet['session']
            candidate = {key: session[key] for key in ('id', 'status', 'revision', 'path')}
            candidate['summary'] = excerpt(session['checkpoint']['summary'], max(120, int(cap * .06)))[0]
            candidate['next_steps'] = session['checkpoint']['next_steps'][:2]
            candidate['verification'] = session['checkpoint']['verification'][:1]
            candidate['blockers'] = session['checkpoint']['blockers'][:1]
            candidate['git_drift'] = session['git_drift']['status']
            if candidate['git_drift'] != 'match' and session.get('recovery_snapshot'):
                candidate['recovery_snapshot'] = session['recovery_snapshot']['id']
            # Never silently cut an individual next step or evidence item.
            while size(candidate) > cap * .24:
                field = next((k for k in ('verification', 'next_steps', 'blockers') if candidate[k]), None)
                if not field:
                    candidate['summary'] = ''
                    break
                candidate[field].pop()
            candidate['details_omitted'] = True
            result['session'] = candidate
            if size(result) > cap - 64:
                result['session'] = None
                result['budget']['omitted_items'] += 1
            else:
                seen.add(session['path'].lower())
        add('project_state', f'03-Projects/{project}/Current-State.md', int(cap * .15), True)
    add('preferences', '01-Memory/User/Preferences.md', int(cap * .12))
    for section, folder in [('project_preferences', f'03-Projects/{project}/Preferences' if project else ''),
                            ('preferences', '01-Memory/User')]:
        if not folder:
            continue
        paths = sorted((hive.vault / folder).rglob('*.md'), key=lambda p: (-p.stat().st_mtime, str(p)))
        for path in paths[:3]:
            add(section, path.relative_to(hive.vault).as_posix(), int(cap * .08))
    for name in ('Personality', 'Working-Style'):
        add('instructions', f'00-System/{name}.md', instruction_allowance)
    add('instructions', f'05-Agents/{AGENTS[agent]}.md', instruction_allowance)
    if query:
        for match in search(hive, query, limit=5, project=project):
            add('relevant', match['path'], int(cap * .10), preferred=match)
            if len(result['relevant']) == 3:
                break
    # Include the envelope itself in the estimate, including UTF-8 expansion and JSON escapes.
    for _ in range(3):
        result['budget']['estimated_tokens'] = math.ceil(size(result) / 4)
    return result
