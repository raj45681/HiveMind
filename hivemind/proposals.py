"""Reviewable, source-linked consolidation and lessons. No inference or promotion."""
import hashlib
import json
import re

from .memory import annotate, apply_batch, eligible, metadata, fields
from .store import atomic_write, utc


def stage(hive, payload):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    ident = 'PROPOSAL-' + digest
    name = f"03-Projects/{payload['project']}/Review-Queue/{ident}.md"
    target = hive.note_path(name)
    content = (f"# {payload['kind'].title()} proposal\n\nStatus: draft\nKind: {payload['kind']}\nSource: {payload['source']}\n\n"
               "Inspect the original sources and evidence. This draft does not establish truth or authorize work.\n\n"
               "The source record includes the proposed content. Use learning-review --content-file to supply a revised version.\n\n"
               "## Source record\n\n```json\n"
               + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n```\n")
    if len(content) + len('accepted') - len('draft') > 8000:
        raise ValueError('Proposal exceeds review size limit; split or shorten its source notes')
    if not target.exists():
        # Exclusive creation preserves reviewer edits and concurrent retries.
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('x', encoding='utf-8', newline='') as output:
                output.write(content)
        except FileExistsError:
            pass
    note = hive.read_note(name, limit=100)
    return {'id': ident, 'path': name, 'revision': note['revision'], 'saved': True, 'model_calls': 0}


def load(hive, path, expected_revision):
    note = hive.read_note(path, limit=8000)
    if '/Review-Queue/PROPOSAL-' not in note['path'] or note['revision'] != expected_revision:
        raise ValueError("Read the current proposal revision first")
    # Source records can be larger than the normal note_read excerpt.
    raw = hive.note_path(path).read_text(encoding='utf-8')
    if len(raw) > 30000:
        raise ValueError("Proposal is too large")
    blocks = re.findall(r'```json\n(.*?)\n```', raw, re.S)
    if len(blocks) != 1:
        raise ValueError("Proposal source record is missing or ambiguous")
    payload = json.loads(blocks[0])
    if payload.get('kind') not in {'consolidation', 'outcome'}:
        raise ValueError("Unknown proposal kind")
    return note, raw, payload


def review(hive, path, expected_revision, accept=False, content=''):
    note, raw, payload = load(hive, path, expected_revision)
    if fields(raw).get('Status') != 'draft':
        raise ValueError("This proposal has already been reviewed")
    project = payload['project']
    if note['path'].split('/')[1] != project:
        raise ValueError("Proposal project does not match its folder")
    target = payload['target']
    scope = payload.get('scope', project)
    valid_target = target.startswith(f'03-Projects/{project}/') if scope else target.startswith('01-Memory/') and not target.startswith('01-Memory/Candidates/')
    if not valid_target or '/Review-Queue/' in target or '/Sessions/' in target:
        raise ValueError("Accepted learning must stay in its project")
    revisions = {}
    with hive.connect(write=True) as c:
        updates = {}
        if accept:
            incoming = content or payload['content']
            declared = fields(incoming).get('Project', scope)
            declared = '' if declared == 'cross-project' else declared
            if declared.casefold() != scope.casefold():
                raise ValueError("Accepted content belongs to another project")
            if payload['kind'] == 'outcome':
                task = c.execute('SELECT status,result FROM tasks WHERE id=?', (payload['task'],)).fetchone()
                if not task or task['status'] != 'done' or hashlib.sha256(task['result'].encode()).hexdigest() != payload['result_revision']:
                    raise ValueError("Task result changed; inspect the latest outcome")
            updates[target] = ('new', incoming)
            for source in payload.get('notes', []):
                old = hive.read_note(source['path'], limit=8000)
                if old['revision'] != source['revision'] or old['total_chars'] > 8000:
                    raise ValueError("A source note changed; regenerate the consolidation proposal")
                if metadata(source['path'], old['text'])['scope'].casefold() != scope.casefold():
                    raise ValueError("Consolidation must preserve project scope")
                updates[source['path']] = (source['revision'], annotate(old['text'],
                    {'State': 'superseded', 'ReplacedBy': target, 'RelationshipSource': note['path']}))
        status = 'accepted' if accept else 'rejected'
        updated = raw.replace('Status: draft', 'Status: ' + status, 1)
        updates[note['path']] = (expected_revision, updated)
        # Proposal records retain the originals; normal memory is capped separately.
        if len(updated) > 8000:
            raise ValueError("Proposal exceeds review size limit; split the source set")
        revisions = apply_batch(hive, c, updates)
    return {'saved': True, 'decision': status, 'target': target if accept else None, 'revisions': revisions}


def outcome(hive, task):
    result = task['result']
    if task['status'] != 'done' or not result or not result['verification']:
        return None
    project, ident = task['spec']['project'], task['id']
    evidence, used = [], 0
    for item in result['verification']:
        if used + len(item) > 1800:
            break
        evidence.append(item)
        used += len(item)
    body = (f"# Lesson from {task['spec']['title']}\n\nKind: solution\nBasis: reviewed-evidence\nProject: {project}\n"
            f"Recorded: {task['updated']}\nSource: task {ident}\nFiles: {json.dumps(task['spec'].get('files', [])[:5])}\n\n"
            + result['summary'] + "\n\n## Reported verification\n\n"
            + '\n'.join('- ' + item for item in evidence)
            + f"\n\nOmitted verification entries: {len(result['verification']) - len(evidence)}. Read task {ident} for full evidence."
            + "\n\nAgent-reported evidence; inspect artifacts and applicability before accepting.\n")
    payload = {'kind': 'outcome', 'project': project, 'task': ident, 'source': 'task ' + ident,
               'result_revision': hashlib.sha256(json.dumps(result, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(),
               'target': f'03-Projects/{project}/Solutions/outcome-{ident.lower()}.md', 'notes': [], 'content': body}
    # Match the exact persisted Pydantic serialization used by finish().
    with hive.connect() as c:
        saved = c.execute('SELECT result FROM tasks WHERE id=?', (ident,)).fetchone()
    payload['result_revision'] = hashlib.sha256(saved['result'].encode()).hexdigest()
    return stage(hive, payload)


def consolidate(hive, project, paths=None, summary='', stage_proposals=False):
    from .context import validate_project, excerpt
    validate_project(project)
    if len(summary) > 2000:
        raise ValueError("Keep the consolidation summary within 2000 characters")
    hive.index()
    with hive.connect() as c:
        records = c.execute("SELECT n.path,n.content,n.revision,m.topic FROM notes n JOIN memory_metadata m ON m.path=n.path "
                            "WHERE lower(m.scope)=lower(?) AND m.state='active' ORDER BY n.path LIMIT 500", (project,)).fetchall()
    usable = {row['path']: row for row in records if eligible(row['path'], metadata(row['path'], row['content']), project)
              and fields(row['content']).get('Kind') in {'solution', 'procedure', 'decision'}}
    groups = []
    if paths:
        if not 2 <= len(paths) <= 5 or len(set(paths)) != len(paths) or any(name not in usable for name in paths):
            raise ValueError("Choose 2-5 distinct active learned notes from this project")
        groups = [paths]
    else:
        used = set()
        for name, row in usable.items():
            if name in used:
                continue
            words = set(re.findall(r'\w+', excerpt(row['content'], 2400)[0].casefold()))
            group = [name]
            for other, candidate in usable.items():
                if other == name or other in used:
                    continue
                terms = set(re.findall(r'\w+', excerpt(candidate['content'], 2400)[0].casefold()))
                same_claim = metadata(name, row['content'])['claim'] == metadata(other, candidate['content'])['claim']
                if words and same_claim and len(words & terms) / max(1, len(words | terms)) >= .8:
                    group.append(other)
                if len(group) == 5:
                    break
            if len(group) > 1:
                groups.append(group)
                used.update(group)
            if len(groups) == 10:
                break
    proposals = []
    for group in groups:
        sources = [{'path': name, 'revision': usable[name]['revision']} for name in group]
        key = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()[:16]
        first = usable[group[0]]['content']
        kind = fields(first).get('Kind', 'solution')
        folder = {'procedure': 'Procedures', 'decision': 'Decisions'}.get(kind, 'Solutions')
        content = f"# Consolidated {kind}\n\nKind: {kind}\nBasis: reviewed-evidence\nProject: {project}\nSource: consolidation {key}\n\n"
        if kind == 'procedure':
            # Keep trigger, steps, applicability, and evidence verbatim.
            content = first.replace(first.splitlines()[0], f'# Consolidated {kind}', 1)
        else:
            content += (summary or '\n\n'.join(excerpt(usable[name]['content'], 700)[0] for name in group)) + '\n'
        content = annotate(content, {'Files': sorted({name for path in group for name in metadata(path, usable[path]['content'])['files']})[:20],
                                     'Topic': metadata(group[0], first)['topic'], 'Claim': metadata(group[0], first)['claim']})
        content += '\n\n## Original sources\n\n' + '\n'.join('- ' + item['path'] + ' @ ' + item['revision'] for item in sources) + '\n'
        if not project:
            content = content.replace('Project: \n', 'Project: cross-project\n', 1)
        target = f'03-Projects/{project}/{folder}/consolidated-{key}.md' if project else f'01-Memory/{folder}/consolidated-{key}.md'
        payload = {'kind': 'consolidation', 'project': project or 'cross-project', 'scope': project,
                   'source': 'consolidation ' + key, 'target': target, 'notes': sources, 'content': content}
        proposals.append(stage(hive, payload) if stage_proposals else {'sources': sources, 'target': payload['target'], 'content': content})
    return {'project': project, 'groups': proposals, 'staged': stage_proposals, 'scanned': len(records),
            'scan_limited': len(records) == 500, 'model_calls': 0}
