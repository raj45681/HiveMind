"""Local, deterministic memory inspection. No model calls or vault mutations."""
import hashlib
import json
import re

from .context import STOPWORDS, excerpt, validate_project


def search_handoffs(hive, query, project="", limit=5):
    """Search stored checkpoint, task-result and targeted-message text on demand."""
    validate_project(project)
    terms = list(dict.fromkeys(term for term in re.findall(r"\w+", query.lower()) if term not in STOPWORDS))[:12]
    if not terms:
        return []
    candidates = []
    def contains(*columns):
        return "(" + " OR ".join(f"instr(lower({column}),?)>0" for column in columns for _ in terms) + ")"
    def values(*columns):
        return tuple(term for _ in columns for term in terms)
    with hive.connect() as c:
        sessions = c.execute(f"""SELECT s.id,s.project,s.goal,c.revision,c.payload,c.created
            FROM sessions s JOIN checkpoints c ON c.session=s.id AND c.revision=s.revision
            WHERE (?='' OR s.project=? COLLATE NOCASE) AND {contains('s.goal', 'c.payload')}
            ORDER BY s.updated_at DESC LIMIT 1000""", (project, project, *values('s.goal', 'c.payload'))).fetchall()
        tasks = c.execute(f"""SELECT id,spec,result,updated FROM tasks WHERE result IS NOT NULL
            AND (?='' OR json_extract(spec,'$.project')=? COLLATE NOCASE)
            AND {contains('spec', 'result')}
            ORDER BY updated DESC LIMIT 1000""", (project, project, *values('spec', 'result'))).fetchall()
        messages = c.execute(f"""SELECT m.id,m.sender,m.recipient,m.body,m.created,m.task,t.spec
            FROM messages m LEFT JOIN tasks t ON t.id=m.task
            WHERE (?='' OR json_extract(t.spec,'$.project')=? COLLATE NOCASE)
            AND {contains('m.body')}
            ORDER BY m.id DESC LIMIT 1000""", (project, project, *values('m.body'))).fetchall()

    for row in sessions:
        data = json.loads(row["payload"])
        body = "\n".join([row["goal"], data["summary"],
                          *(item for field in ("completed", "verification", "blockers", "next_steps")
                            for item in data.get(field, []))])
        score = sum(term in body.lower() for term in terms)
        if score:
            snippet, omitted = excerpt(body, 500, query)
            candidates.append((score, row["created"], {"source_type": "checkpoint", "id": row["id"],
                "project": row["project"], "revision": row["revision"], "excerpt": snippet,
                "omitted": omitted, "path": f"03-Projects/{row['project']}/Sessions/{row['id']}.md"}))
    for row in tasks:
        spec, result = json.loads(row["spec"]), json.loads(row["result"])
        body = "\n".join([spec["title"], result["summary"],
                          *(item for field in ("artifacts", "verification", "unresolved")
                            for item in result.get(field, []))])
        score = sum(term in body.lower() for term in terms)
        if score:
            snippet, omitted = excerpt(body, 500, query)
            candidates.append((score, row["updated"], {"source_type": "task_handoff", "id": row["id"],
                "project": spec["project"], "excerpt": snippet, "omitted": omitted,
                "path": f"06-Handoffs/{row['id']}.md"}))
    for row in messages:
        body = row["body"]
        score = sum(term in body.lower() for term in terms)
        if score:
            snippet, omitted = excerpt(body, 500, query)
            candidates.append((score, row["created"], {"source_type": "message", "id": row["id"],
                "project": json.loads(row["spec"])["project"] if row["spec"] else "",
                "sender": row["sender"], "recipient": row["recipient"], "task": row["task"],
                "excerpt": snippet, "omitted": omitted}))
    # Equal lexical scores prefer the latest handoff.
    candidates.sort(key=lambda item: item[1], reverse=True)
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[2] for item in candidates[:max(1, min(limit, 5))]]


def audit(hive, project="", limit=50):
    """Flag mechanical memory-quality problems; never infer that prose is false."""
    validate_project(project)
    limit = max(1, min(limit, 100))
    issues, fingerprints, checked = [], {}, 0
    for path in sorted(hive.vault.rglob("*.md")):
        relative = path.relative_to(hive.vault).as_posix()
        parts = relative.split("/")
        if parts[0] not in {"01-Memory", "02-Decisions", "03-Projects"} or any(p.startswith(".") for p in parts):
            continue
        if project and parts[0] == "03-Projects" and (len(parts) < 2 or parts[1].lower() != project.lower()):
            continue
        if "/Sessions/" in relative:
            continue  # Generated checkpoint views are audited through their source records.
        try:
            target = hive.note_path(relative)
            raw = target.read_bytes()
            body = raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            issues.append({"path": relative, "code": "unreadable", "detail": "Cannot read this Markdown note safely"})
            continue
        checked += 1
        if not body.strip():
            issues.append({"path": relative, "code": "empty", "detail": "Note has no content"})
            continue
        if len(body) > 8000:
            issues.append({"path": relative, "code": "oversized", "detail": "Exceeds the 8000-character agent-write limit"})
        if parts[0] == "01-Memory" and len(parts) > 1 and parts[1] != "User":
            fingerprint = hashlib.sha256(body.strip().encode("utf-8")).hexdigest()
            if fingerprint in fingerprints:
                issues.append({"path": relative, "code": "duplicate", "detail": f"Same content as {fingerprints[fingerprint]}"})
            else:
                fingerprints[fingerprint] = relative
        if re.search(r"(?m)^Kind:\s*", body):
            fields = dict((key.lower(), value.strip()) for key, value in
                          re.findall(r"(?m)^(Kind|Basis|Project|Recorded|Source):\s*(.*)$", body))
            missing = [key for key in ("kind", "basis", "project", "recorded", "source") if not fields.get(key)]
            if missing:
                issues.append({"path": relative, "code": "missing_metadata", "detail": "Missing " + ", ".join(missing)})
            if fields.get("kind") == "solution" and (fields.get("basis") != "verified-result" or
                                                       not re.search(r"(?m)^Verification / applicability:\s*\n\S", body)):
                issues.append({"path": relative, "code": "unverified_solution", "detail": "Solution lacks verified basis or evidence"})
            if parts[0] == "03-Projects" and len(parts) > 1 and fields.get("project", "").lower() not in {"", parts[1].lower()}:
                issues.append({"path": relative, "code": "project_mismatch", "detail": "Project metadata differs from note folder"})
    return {"project": project or None, "checked_notes": checked, "issue_count": len(issues),
            "issues": issues[:limit], "truncated": len(issues) > limit, "read_only": True}
