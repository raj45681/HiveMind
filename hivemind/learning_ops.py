"""Explicit, local review and maintenance of learned notes. No model calls."""
import hashlib
import json
import os
import re
import time
import uuid

from .context import validate_project
from .sessions import validate_id
from .store import atomic_write, utc


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _fields(text):
    return dict(re.findall(r"(?m)^([A-Za-z]+):\s*(.*)$", text))


def _candidate_path(hive, path):
    target = hive.note_path(path)
    name = target.relative_to(hive.vault).as_posix()
    if not name.startswith("01-Memory/Candidates/"):
        raise ValueError("Choose a note from the candidate inbox")
    return target, name


def candidate_inbox(hive, limit=25, project=""):
    validate_project(project)
    folder = hive.vault / "01-Memory" / "Candidates"
    items = []
    for path in folder.rglob("*.md") if folder.exists() else []:
        name = path.relative_to(hive.vault).as_posix()
        scope = name.split("/")[2] if len(name.split("/")) > 3 else ""
        if project and scope.casefold() not in {project.casefold(), "cross-project"}:
            continue
        try:
            raw = hive.note_path(name).read_bytes()
            data = _fields(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        items.append({"path": name, "revision": _revision(raw),
                      "project": data.get("Project"), "source": data.get("Source"),
                      "summary": raw.decode("utf-8").split("Verification / applicability:", 1)[0][-400:]})
    items.sort(key=lambda item: item["path"])
    cap = max(1, min(limit, 100))
    return {"candidates": items[:cap], "total": len(items), "truncated": len(items) > cap,
            "note": "Only an explicit approval promotes an inferred preference."}


def _move(hive, source_name, destination_name, expected_revision):
    source, destination = hive.note_path(source_name), hive.note_path(destination_name)
    with hive.connect(write=True) as c:
        raw = source.read_bytes()
        if _revision(raw) != expected_revision:
            raise ValueError("Note changed; read the current revision before moving it")
        if destination.exists():
            raise ValueError("Destination already exists; inspect it before retrying")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        try:
            c.execute("DELETE FROM notes WHERE path=?", (source_name,))
            hive._index_note(c, destination)
        except BaseException:
            os.replace(destination, source)
            raise
    return {"path": destination_name, "revision": expected_revision, "saved": True}


def candidate_reject(hive, path, expected_revision):
    _, name = _candidate_path(hive, path)
    result = _move(hive, name, _candidate_archive(name), expected_revision)
    return {**result, "decision": "rejected", "recoverable": True}


def _candidate_archive(name):
    return "99-Archive/" + name.removesuffix(".md") + "-" + uuid.uuid4().hex[:12] + ".md"


def candidate_approve(hive, path, expected_revision):
    source, name = _candidate_path(hive, path)
    raw = source.read_bytes()
    if _revision(raw) != expected_revision:
        raise ValueError("Candidate changed; read its current revision before approval")
    body = raw.decode("utf-8")
    meta = _fields(body)
    if meta.get("Kind") != "preference" or meta.get("Basis") != "observation":
        raise ValueError("Only inferred preference candidates can be approved here")
    project = meta.get("Project", "")
    if project == "cross-project":
        destination_name = "01-Memory/User/Learned/" + source.name
    else:
        validate_project(project, required=True)
        destination_name = f"03-Projects/{project}/Preferences/{source.name}"
    destination = hive.note_path(destination_name)
    archive_name = _candidate_archive(name)
    archive = hive.note_path(archive_name)
    promoted = body.replace("Basis: observation", "Basis: user-approved", 1)
    promoted = promoted.replace("\nSource: ", f"\nApproved: {utc()}\nSource: ", 1)
    with hive.connect(write=True) as c:
        if _revision(source.read_bytes()) != expected_revision:
            raise ValueError("Candidate changed; read its current revision before approval")
        if destination.exists() or archive.exists():
            raise ValueError("Approved or archived note already exists; inspect before retrying")
        atomic_write(destination, promoted)
        try:
            archive.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, archive)
            c.execute("DELETE FROM notes WHERE path=?", (name,))
            hive._index_note(c, destination)
            hive._index_note(c, archive)
        except BaseException:
            if archive.exists() and not source.exists():
                os.replace(archive, source)
            destination.unlink(missing_ok=True)
            raise
    return {"decision": "approved", "path": destination_name,
            "revision": _revision(promoted.encode("utf-8")), "candidate_archived": archive_name,
            "saved": True}


def _procedure_path(hive, path, archived=False):
    target = hive.note_path(path)
    name = target.relative_to(hive.vault).as_posix()
    active = name.removeprefix("99-Archive/") if archived else name
    if archived and active == name:
        raise ValueError("Choose an archived procedure")
    if not (re.fullmatch(r"01-Memory/Procedures/[^/]+\.md", active) or
            re.fullmatch(r"03-Projects/[^/]+/Procedures/[^/]+\.md", active)):
        raise ValueError("Choose a learned procedure note")
    return name, active


def procedure_archive(hive, path, expected_revision):
    name, _ = _procedure_path(hive, path)
    return _move(hive, name, "99-Archive/" + name, expected_revision)


def procedure_unarchive(hive, path, expected_revision):
    name, active = _procedure_path(hive, path, archived=True)
    return _move(hive, name, active, expected_revision)


def procedure_used(hive, path, expected_revision, source, evidence):
    name, _ = _procedure_path(hive, path)
    if (not isinstance(source, str) or not isinstance(evidence, str) or
            not source.strip() or len(source) > 500 or not evidence.strip() or len(evidence) > 1500):
        raise ValueError("Record a concise source and concrete verification evidence")
    with hive.connect(write=True) as c:
        raw = hive.note_path(name).read_bytes()
        if _revision(raw) != expected_revision:
            raise ValueError("Procedure changed; read the current revision before recording use")
        fields = _fields(raw.decode("utf-8"))
        if fields.get("Kind") != "procedure" or fields.get("Basis") != "verified-result":
            raise ValueError("Only verified procedure notes can record an application")
        c.execute("INSERT INTO procedure_uses(path,revision,source,evidence,used) VALUES(?,?,?,?,?)",
                  (name, expected_revision, source.strip(), evidence.strip(), utc()))
    return {"path": name, "revision": expected_revision, "recorded": True}


def procedure_report(hive, project="", stale_days=180, limit=50, stale_only=False):
    validate_project(project)
    if not 1 <= stale_days <= 3650:
        raise ValueError("Stale days must be 1-3650")
    folders = [hive.vault / "01-Memory" / "Procedures"]
    projects = hive.vault / "03-Projects"
    if project:
        folders.append(projects / project / "Procedures")
    elif projects.exists():
        folders.extend(path / "Procedures" for path in projects.iterdir() if path.is_dir())
    paths = [path for folder in folders if folder.exists() for path in folder.glob("*.md")]
    with hive.connect() as c:
        usage = {row["path"]: dict(row) for row in c.execute(
            "SELECT path,count(*) AS uses,max(used) AS last_used FROM procedure_uses GROUP BY path")}
    records, duplicates, fingerprints = [], [], {}
    for path in sorted(paths):
        name = path.relative_to(hive.vault).as_posix()
        try:
            path = hive.note_path(name)
            if path.stat().st_size > 512_000:
                continue
            raw = path.read_bytes()
            body = raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        section = body.split("## Steps", 1)[-1].split("Applicability:", 1)[0]
        fingerprint = hashlib.sha256(re.sub(r"\s+", " ", section.strip().lower()).encode()).hexdigest()
        if section.strip() and fingerprint in fingerprints:
            duplicates.append({"path": name, "same_steps_as": fingerprints[fingerprint]})
        else:
            fingerprints[fingerprint] = name
        days = int(max(0, time.time() - path.stat().st_mtime) / 86400)
        item = {"path": name, "revision": _revision(raw), "age_days": days,
                "uses": usage.get(name, {}).get("uses", 0),
                "last_used": usage.get(name, {}).get("last_used"),
                "stale": days >= stale_days}
        records.append(item)
    stale_count = sum(item["stale"] for item in records)
    displayed = [item for item in records if item["stale"]] if stale_only else records
    cap = max(1, min(limit, 100))
    return {"project": project or None, "procedure_count": len(records), "stale_days": stale_days,
            "stale_count": stale_count, "procedures": displayed[:cap], "duplicates": duplicates[:cap],
            "truncated": len(displayed) > cap or len(duplicates) > cap, "read_only": True,
            "note": "Uses are explicit verified application records; unrecorded applications are unknown. Duplicates mean identical normalized steps only."}


def review_checkpoint(hive, ident, budget=1000, stage=False):
    validate_id(ident)
    if not 512 <= budget <= 4096:
        raise ValueError("Review budget must be 512-4096 estimated tokens")
    with hive.connect() as c:
        row = c.execute("""SELECT s.project,s.goal,s.revision,c.payload,c.created
            FROM sessions s JOIN checkpoints c ON c.session=s.id AND c.revision=s.revision
            WHERE s.id=?""", (ident,)).fetchone()
    if row is None:
        raise ValueError("Session not found")
    data = json.loads(row["payload"])
    packet = {"session": ident, "project": row["project"], "revision": row["revision"],
              "goal": row["goal"], "status": data["status"], "summary": data["summary"],
              "completed": data["completed"], "verification": data["verification"],
              "blockers": data["blockers"], "next_steps": data["next_steps"],
              "source": row["created"], "model_calls": 0,
              "review_prompt": "Look for a reusable verified procedure or an explicitly stated preference. Treat this agent-reported checkpoint as evidence to inspect, not proof. Use memory_learn only after verification; never auto-promote a preference."}
    byte_cap = min(budget * 4, 5500) if stage else budget * 4
    omitted = 0
    while len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) > byte_cap - 64:
        field = next((key for key in ("next_steps", "blockers", "completed", "verification") if packet[key]), None)
        if field:
            packet[field].pop()
            omitted += 1
        elif len(packet["summary"]) > 200:
            packet["summary"] = packet["summary"][:200]
            omitted += 1
        elif len(packet["goal"]) > 150:
            packet["goal"] = packet["goal"][:150]
            omitted += 1
        else:
            break
    packet["omitted_items"] = omitted
    packet["estimated_tokens"] = (len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) + 3) // 4
    if stage:
        path = f"03-Projects/{row['project']}/Review-Queue/{ident}-r{row['revision']}.md"
        existing = hive.note_path(path)
        if existing.exists():
            packet["staged"] = {"path": path, "revision": _revision(existing.read_bytes()),
                                "saved": True, "existing": True}
            return packet
        content = (f"# Review {ident} revision {row['revision']}\n\nStatus: draft\nSource: session checkpoint {ident} revision {row['revision']}\nRecorded: {utc()}\n\n"
                   "Agent-reported checkpoint; inspect source and verification before promoting anything.\n\n"
                   "```json\n" + json.dumps(packet, ensure_ascii=False) + "\n```\n")
        packet["staged"] = hive.write_memory(path, content)
    return packet
