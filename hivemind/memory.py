"""Markdown memory metadata, explicit relationships, and scoped retrieval."""
import hashlib
import json
from pathlib import PurePosixPath
import re

FIELDS = {"Topic", "Claim", "State", "Files", "ReplacedBy", "Conflicts", "RelationshipSource"}


def file_paths(values):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("Supply at most 20 project-relative file paths")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 250 or "\\" in value or ":" in value:
            raise ValueError("Files must be portable project-relative paths")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or any(part.startswith(".") for part in path.parts):
            raise ValueError("Files must stay inside the project and exclude hidden paths")
        if str(path) not in result:
            result.append(str(path))
    return result


def fields(text):
    # Metadata is the first prose block after the title, not arbitrary body/code.
    head = text.lstrip()
    if head.startswith("# "):
        head = head.split("\n", 1)[1] if "\n" in head else ""
    head = head.lstrip("\r\n").split("\n\n", 1)[0]
    return dict(re.findall(r"(?m)^([A-Za-z]+):[ \t]*(.*)$", head[:5000]))


def metadata(path, text):
    data = fields(text)
    active = path.removeprefix("99-Archive/")
    parts = active.split("/")
    scope = parts[1] if parts[0] == "03-Projects" and len(parts) > 2 else data.get("Project", "")
    if scope == "cross-project":
        scope = ""
    state = data.get("State", "active")
    if state not in {"active", "superseded", "disputed"}:
        state = "disputed"  # Unknown states must never silently become trusted advice.
    def items(key):
        try:
            value = json.loads(data.get(key, "[]"))
            return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []
        except ValueError:
            return []
    return {"scope": scope, "state": state, "topic": data.get("Topic", ""),
            "claim": data.get("Claim", ""), "files": items("Files")[:20],
            "replaces": data.get("ReplacedBy", ""), "conflicts": items("Conflicts")[:10]}


def indexed(c, path, revision, text):
    meta = metadata(path, text)
    c.execute("INSERT OR REPLACE INTO memory_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
              (path, revision, meta["scope"], meta["state"], meta["topic"], meta["claim"],
               json.dumps(meta["files"]), meta["replaces"], json.dumps(meta["conflicts"]), "1"))


def eligible(path, meta, project="", archive=False):
    name = path.removeprefix("99-Archive/")
    if (path.startswith("99-Archive/") and not archive or name.startswith("01-Memory/Candidates/")
            or "/Review-Queue/" in name or "/Sessions/" in name):
        return False
    if project and meta["scope"] and meta["scope"].casefold() != project.casefold():
        return False
    return archive or meta["state"] != "superseded"


def annotate(text, changes):
    if any(key not in FIELDS for key in changes):
        raise ValueError("Unsupported memory metadata field")
    lines = text.splitlines()
    start = 1 if lines and lines[0].startswith("# ") else 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = start
    while end < len(lines) and re.match(r"^[A-Za-z]+:", lines[end]):
        end += 1
    block = [line for line in lines[start:end] if line.split(":", 1)[0] not in changes]
    block.extend(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value}"
                 for key, value in changes.items())
    return "\n".join(lines[:start] + block + ([""] if end == start else []) + lines[end:]) + "\n"


def apply_batch(hive, c, updates):
    """CAS all inputs before writing, preserve versions, restore our writes on failure."""
    originals, written = {}, {}
    for name, (revision, content) in updates.items():
        target = hive.note_path(name)
        if (target.relative_to(hive.vault).as_posix() != name
                or target.relative_to(hive.vault).parts[0] not in {"01-Memory", "02-Decisions", "03-Projects"}):
            raise ValueError("Only agent-writable memory can be updated")
        if not content.strip() or len(content) > 8000:
            raise ValueError("Updated memory must contain 1-8000 characters")
        prior = target.read_bytes() if target.exists() else None
        current = hashlib.sha256(prior).hexdigest() if prior is not None else "new"
        if current != revision:
            raise ValueError("Memory changed; read current revisions before retrying")
        originals[name] = prior
    from .store import atomic_write
    try:
        for name, (_, content) in updates.items():
            target = hive.note_path(name)
            if originals[name] is not None:
                hive._remember_version(c, name, originals[name])
            atomic_write(target, content)
            written[name] = content.encode("utf-8")
            hive._index_note(c, target)
    except BaseException:
        for name, incoming in written.items():
            target = hive.note_path(name)
            if target.exists() and target.read_bytes() == incoming:
                if originals[name] is None:
                    target.unlink()
                else:
                    atomic_write(target, originals[name].decode("utf-8"))
        raise
    return {name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, (_, content) in updates.items()}


def relate(hive, path, related, relation, expected_revision, related_revision, source):
    if relation not in {"supersedes", "conflicts", "resolve"} or path == related:
        raise ValueError("Choose supersedes, conflicts, or resolve between distinct notes")
    if not isinstance(source, str) or not source.strip() or len(source) > 500 or "\n" in source:
        raise ValueError("Supply a concise single-line relationship source")
    first, second = hive.read_note(path, limit=8000), hive.read_note(related, limit=8000)
    if first['path'] != path or second['path'] != related:
        raise ValueError("Use canonical note paths")
    if first["revision"] != expected_revision or second["revision"] != related_revision:
        raise ValueError("Memory changed; read both current revisions")
    if first["total_chars"] > 8000 or second["total_chars"] > 8000:
        raise ValueError("Split oversized notes before adding relationships")
    a, b = metadata(path, first["text"]), metadata(related, second["text"])
    if a["scope"].casefold() != b["scope"].casefold():
        raise ValueError("Relationships must remain within the same memory scope")
    if relation == "supersedes":
        # path is the current replacement; related is the older note.
        if a["state"] == "superseded":
            raise ValueError("A superseded note cannot replace another note")
        changes = {related: (related_revision, annotate(second["text"],
                    {"State": "superseded", "ReplacedBy": path, "RelationshipSource": source}))}
    else:
        if a["state"] == "superseded" or b["state"] == "superseded":
            raise ValueError("Inspect the current replacement before changing a conflict")
        left, right = set(a["conflicts"]), set(b["conflicts"])
        if relation == "conflicts":
            left.add(related)
            right.add(path)
        else:
            left.discard(related)
            right.discard(path)
        changes = {path: (expected_revision, annotate(first["text"],
                   {"Conflicts": sorted(left), "State": "disputed" if left else "active", "RelationshipSource": source})),
                   related: (related_revision, annotate(second["text"],
                   {"Conflicts": sorted(right), "State": "disputed" if right else "active", "RelationshipSource": source}))}
    with hive.connect(write=True) as c:
        # Also guard the read-only replacement against a concurrent external edit.
        if hive.read_note(path, limit=100)["revision"] != expected_revision:
            raise ValueError("Replacement note changed")
        saved = apply_batch(hive, c, changes)
    return {"saved": True, "relation": relation, "revisions": saved, "source": source}
