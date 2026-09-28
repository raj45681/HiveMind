import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import ROUTES, TaskResult, TaskSpec


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8", newline="")
        for attempt in range(6):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


class Hive:
    """One authoritative runtime DB. Markdown is authoritative for memory."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.vault = (self.root / "vault").resolve()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.vault.mkdir(parents=True, exist_ok=True)
        self.db = self.runtime / "hivemind.db"
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL, agent TEXT NOT NULL,
                    status TEXT NOT NULL, owner TEXT, claim_token TEXT, lease REAL,
                    attempts INTEGER NOT NULL DEFAULT 0, result TEXT, created TEXT NOT NULL,
                    updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, task TEXT, event TEXT, detail TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT, recipient TEXT,
                    task TEXT, body TEXT, created TEXT);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, agent TEXT NOT NULL, goal TEXT NOT NULL,
                    created TEXT NOT NULL, updated_at REAL NOT NULL, baseline TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    session TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    snapshot TEXT NOT NULL, created TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(session,revision));
                CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(
                    path UNINDEXED, title, content, revision UNINDEXED);
                CREATE TABLE IF NOT EXISTS note_versions (
                    path TEXT NOT NULL, revision TEXT NOT NULL, content BLOB NOT NULL,
                    recorded TEXT NOT NULL, PRIMARY KEY(path, revision));
                CREATE INDEX IF NOT EXISTS note_versions_recent ON note_versions(path, recorded DESC);
                CREATE TABLE IF NOT EXISTS procedure_uses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, revision TEXT NOT NULL,
                    source TEXT NOT NULL, evidence TEXT NOT NULL, used TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS procedure_uses_path ON procedure_uses(path, used DESC);
                CREATE TABLE IF NOT EXISTS recovery_snapshots (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, revision INTEGER NOT NULL,
                    project TEXT NOT NULL, workspace TEXT NOT NULL, git_dir TEXT NOT NULL,
                    head TEXT NOT NULL, branch TEXT NOT NULL, created TEXT NOT NULL,
                    archive_sha TEXT NOT NULL, archive_bytes INTEGER NOT NULL,
                    file_count INTEGER NOT NULL, excluded_count INTEGER NOT NULL,
                    UNIQUE(session,revision));
                CREATE INDEX IF NOT EXISTS recovery_project_recent ON recovery_snapshots(project,created DESC);
                CREATE TABLE IF NOT EXISTS recovery_undos (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, project TEXT NOT NULL,
                    workspace TEXT NOT NULL, git_dir TEXT NOT NULL, head TEXT NOT NULL, branch TEXT NOT NULL,
                    path TEXT NOT NULL, prior_sha TEXT NOT NULL, prior_mode INTEGER,
                    restored_sha TEXT NOT NULL,
                    created TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
            """)
            if "prior_mode" not in {row[1] for row in c.execute("PRAGMA table_info(recovery_undos)")}:
                c.execute("ALTER TABLE recovery_undos ADD COLUMN prior_mode INTEGER")

    @contextmanager
    def connect(self, write=False):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA busy_timeout=30000")
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def note_path(self, relative):
        name = Path(relative)
        if name.is_absolute() or ".." in name.parts or any(p.startswith(".") for p in name.parts):
            raise ValueError("Use a relative Markdown path inside the vault")
        target = (self.vault / name).resolve()
        if not target.is_relative_to(self.vault) or target.suffix.lower() != ".md" or ":" in relative:
            raise ValueError("Only Markdown inside this vault is available")
        return target

    def read_note(self, path, offset=0, limit=4000, revision="", include_history=False):
        target = self.note_path(path)
        name = target.relative_to(self.vault).as_posix()
        if revision:
            with self.connect() as c:
                row = c.execute("SELECT content FROM note_versions WHERE path=? AND revision=?", (name, revision)).fetchone()
            if row is None:
                raise ValueError("Note revision not found")
            data = bytes(row["content"])
        else:
            if target.stat().st_size > 512_000:
                raise ValueError("Note is too large; split or archive it before retrieval")
            data = target.read_bytes()
        if len(data) > 512_000:
            raise ValueError("Note revision is too large for retrieval")
        raw = data.decode("utf-8")
        offset, limit = max(0, offset), max(100, min(limit, 8000))
        result = {"path": name,
                "revision": hashlib.sha256(data).hexdigest(),
                "text": raw[offset:offset + limit], "total_chars": len(raw),
                "next_offset": offset + limit if offset + limit < len(raw) else None}
        if include_history:
            result["history"] = self.note_history(name)["versions"]
        return result

    def note_history(self, path, limit=20):
        target = self.note_path(path)
        name = target.relative_to(self.vault).as_posix()
        self.index()  # Capture recent edits made directly in Obsidian before listing versions.
        with self.connect() as c:
            rows = c.execute("SELECT revision,recorded,length(content) AS bytes FROM note_versions "
                             "WHERE path=? ORDER BY recorded DESC,rowid DESC LIMIT ?",
                             (name, max(1, min(limit, 50)))).fetchall()
        return {"path": name, "versions": [dict(row) for row in rows]}

    def note_diff(self, path, old_revision, new_revision="current"):
        import difflib
        target = self.note_path(path)
        name = target.relative_to(self.vault).as_posix()
        def content(revision):
            if revision == "current":
                return target.read_bytes()
            with self.connect() as c:
                row = c.execute("SELECT content FROM note_versions WHERE path=? AND revision=?", (name, revision)).fetchone()
            if row is None:
                raise ValueError("Note revision not found")
            return bytes(row["content"])
        before, after = content(old_revision), content(new_revision)
        lines = difflib.unified_diff(before.decode("utf-8").splitlines(keepends=True),
                                     after.decode("utf-8").splitlines(keepends=True),
                                     fromfile=old_revision, tofile=new_revision)
        output, truncated = "", False
        for line in lines:
            if len(output) + len(line) > 12000:
                truncated = True
                break
            output += line
        return {"path": name, "diff": output, "truncated": truncated}

    def restore_note(self, path, revision, expected_revision):
        target = self.note_path(path)
        name = target.relative_to(self.vault).as_posix()
        with self.connect() as c:
            row = c.execute("SELECT content FROM note_versions WHERE path=? AND revision=?", (name, revision)).fetchone()
        if row is None:
            raise ValueError("Note revision not found")
        return self.write_memory(name, bytes(row["content"]).decode("utf-8"), expected_revision)

    def write_memory(self, path, content, expected_revision="new"):
        target = self.note_path(path)
        if target.relative_to(self.vault).parts[0] not in {"01-Memory", "02-Decisions", "03-Projects"}:
            raise ValueError("Agent memory writes belong in 01-Memory, 02-Decisions or 03-Projects")
        if not content.strip() or len(content) > 8000:
            raise ValueError("Memory must contain 1–8000 characters; split longer notes")
        with self.connect(write=True) as c:
            previous = target.read_bytes() if target.exists() else None
            current = hashlib.sha256(previous).hexdigest() if previous is not None else "new"
            incoming = content.encode("utf-8")
            incoming_revision = hashlib.sha256(incoming).hexdigest()
            if current != expected_revision and current != incoming_revision:
                raise ValueError("Note changed; read the current revision before updating")
            if previous == incoming:
                indexed = c.execute("SELECT revision FROM notes WHERE path=?",
                                    (target.relative_to(self.vault).as_posix(),)).fetchone()
                if indexed is None or indexed["revision"] != incoming_revision:
                    self._index_note(c, target)
                return {"path": path, "revision": incoming_revision}
            if previous is not None:
                self._remember_version(c, target.relative_to(self.vault).as_posix(), previous)
            atomic_write(target, content)
            self._index_note(c, target)
        return {"path": path, "revision": incoming_revision}

    def _index_note(self, c, path):
        data = path.read_bytes()
        raw = data.decode("utf-8")
        name = path.relative_to(self.vault).as_posix()
        self._remember_version(c, name, data)
        title = next((line[2:] for line in raw.splitlines() if line.startswith("# ")), path.stem)
        c.execute("DELETE FROM notes WHERE path=?", (name,))
        c.execute("INSERT INTO notes VALUES (?,?,?,?)", (name, title, raw, hashlib.sha256(data).hexdigest()))

    def _remember_version(self, c, name, data):
        if name.split("/", 1)[0] not in {"00-System", "01-Memory", "02-Decisions", "03-Projects"} or "/Sessions/" in name:
            return  # Generated task/dashboard/session views have their own source records.
        c.execute("INSERT OR IGNORE INTO note_versions VALUES (?,?,?,?)",
                  (name, hashlib.sha256(data).hexdigest(), data, utc()))

    def index(self):
        count = 0
        with self.connect(write=True) as c:
            existing = {r["path"]: r["revision"] for r in c.execute("SELECT path,revision FROM notes")}
            seen = set()
            for path in self.vault.rglob("*.md"):
                relative = path.relative_to(self.vault)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                try:
                    path = self.note_path(relative.as_posix())
                    if path.stat().st_size > 512_000:
                        continue
                    name = path.relative_to(self.vault).as_posix()
                    seen.add(name)
                    data = path.read_bytes()
                    revision = hashlib.sha256(data).hexdigest()
                    if not c.execute("SELECT 1 FROM note_versions WHERE path=? AND revision=?", (name, revision)).fetchone():
                        # Existing installations may have an FTS copy but no version table yet.
                        old = c.execute("SELECT content,revision FROM notes WHERE path=?", (name,)).fetchone()
                        if old:
                            old_data = old["content"].encode("utf-8")
                            if hashlib.sha256(old_data).hexdigest() == old["revision"]:
                                self._remember_version(c, name, old_data)
                    if existing.get(name) != revision:
                        self._index_note(c, path)
                    else:
                        self._remember_version(c, name, data)
                    count += 1
                except (OSError, UnicodeError, ValueError):
                    continue
            for missing in existing.keys() - seen:
                c.execute("DELETE FROM notes WHERE path=?", (missing,))
        return {"indexed_notes": count}

    def search(self, query, limit=5, archive=False, project="", include_handoffs=False):
        from .context import search
        notes = search(self, query, limit, archive, project)
        if not include_handoffs:
            return notes
        from .memory_ops import search_handoffs
        handoffs = search_handoffs(self, query, project, limit)
        cap = max(1, min(limit, 5))
        selected = handoffs[:min(2, cap)]
        checkpoint = next((item for item in handoffs if item.get("source_type") == "checkpoint"), None)
        if checkpoint and checkpoint not in selected:
            selected = selected[:1] + [checkpoint] if cap > 1 else [checkpoint]
        paths = {item.get("path") for item in selected}
        return (selected + [item for item in notes if item.get("path") not in paths])[:cap]

    def handoff_search(self, query, project="", limit=5):
        from .memory_ops import search_handoffs
        return search_handoffs(self, query, project, limit)

    def memory_audit(self, project="", limit=50):
        from .memory_ops import audit
        return audit(self, project, limit)

    def context(self, agent="generic", project="", query="", budget_tokens=None):
        from .context import context
        return context(self, agent, project, query, budget_tokens)

    def session_start(self, project, agent, goal, session_id="", workspace=None):
        from .sessions import start
        return start(self, project, agent, goal, session_id, workspace)

    def session_checkpoint(self, ident, checkpoint, expected_revision, workspace=None):
        from .sessions import checkpoint as save
        return save(self, ident, checkpoint, expected_revision, workspace)

    def session_resume(self, project, ident=""):
        from .sessions import resume
        return resume(self, project, ident)

    def catalog(self, after="", limit=100):
        self.index()
        limit = max(1, min(limit, 100))
        with self.connect() as c:
            notes = [dict(r) for r in c.execute("SELECT path,title,revision FROM notes WHERE path>? ORDER BY path LIMIT ?", (after, limit))]
        return {"notes": notes, "next_cursor": notes[-1]["path"] if len(notes) == limit else None}

    def create_task(self, spec):
        spec = TaskSpec.model_validate(spec)
        if any(len(s) > 800 for s in spec.acceptance):
            raise ValueError("Keep each acceptance criterion below 800 characters")
        for path in spec.memory:
            self.note_path(path)
        ident = "TASK-" + uuid.uuid4().hex[:12]
        with self.connect(write=True) as c:
            for dep in spec.depends_on:
                if not c.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone():
                    raise ValueError("Unknown dependency: " + dep)
            c.execute("INSERT INTO tasks(id,spec,agent,status,created,updated) VALUES(?,?,?,?,?,?)",
                      (ident, spec.model_dump_json(), spec.agent or ROUTES[spec.kind], "pending", utc(), utc()))
            self._event(c, ident, "created")
            self._export(c)
        return self.get_task(ident)

    def _event(self, c, ident, event, detail=""):
        c.execute("INSERT INTO events(task,event,detail,created) VALUES(?,?,?,?)", (ident, event, detail, utc()))

    def _decode(self, row, private=False, now=None):
        if row is None:
            raise ValueError("Task not found")
        result = dict(row)
        # Reads should expose an expired lease without taking a write lock.
        # The next write still records the durable expiry event via _expire.
        if result['status'] == 'running' and result['lease'] is not None and result['lease'] < (time.time() if now is None else now):
            result['status'] = 'blocked'
        result["spec"] = json.loads(result["spec"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        if not private:
            result.pop("claim_token", None)
        return result

    def get_task(self, ident):
        with self.connect() as c:
            return self._decode(c.execute("SELECT * FROM tasks WHERE id=?", (ident,)).fetchone())

    def list_tasks(self, status="", limit=25):
        now = time.time()
        with self.connect() as c:
            rows = c.execute("""SELECT * FROM tasks WHERE (?='' OR
                CASE WHEN status='running' AND lease < ? THEN 'blocked' ELSE status END=?)
                ORDER BY created DESC,id LIMIT ?""",
                             (status, now, status, max(1, min(limit, 50))))
            return [{k: v for k, v in self._decode(row, now=now).items() if k not in {"result", "spec"}} |
                    {"title": json.loads(row["spec"])["title"], "project": json.loads(row["spec"])["project"]}
                    for row in rows]

    def _expire(self, c):
        # An expired worker may still be editing. Never automatically run its task twice.
        for row in c.execute("SELECT id FROM tasks WHERE status='running' AND lease < ?", (time.time(),)).fetchall():
            c.execute("UPDATE tasks SET status='blocked',claim_token=NULL,updated=? WHERE id=?", (utc(), row["id"]))
            self._event(c, row["id"], "lease_expired", "Inspect worker/worktree, then explicitly requeue")

    def claim(self, worker, agent, ident="", machine="", lease_seconds=120):
        from .context import validate_agent
        validate_agent(agent)
        if not re.fullmatch(r"[a-zA-Z0-9_.:@-]{1,100}", worker):
            raise ValueError("Worker ID must be 1–100 letters, digits, or ._:@-")
        lease_seconds = max(30, min(lease_seconds, 3600))
        with self.connect(write=True) as c:
            self._expire(c)
            rows = c.execute("SELECT * FROM tasks WHERE status='pending' AND agent=? AND (?='' OR id=?) ORDER BY created,id",
                             (agent, ident, ident)).fetchall()
            for row in rows:
                spec = json.loads(row["spec"])
                if spec["machine"] not in {"any", machine}:
                    continue
                if any(c.execute("SELECT status FROM tasks WHERE id=?", (dep,)).fetchone()[0] != "done"
                       for dep in spec["depends_on"]):
                    continue
                token = secrets.token_urlsafe(24)
                c.execute("UPDATE tasks SET status='running',owner=?,claim_token=?,lease=?,attempts=attempts+1,updated=? WHERE id=?",
                          (worker, token, time.time() + lease_seconds, utc(), row["id"]))
                self._event(c, row["id"], "claimed", worker)
                self._export(c)
                return self._decode(c.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone(), True)
            self._export(c)
            return None

    def _owned(self, c, ident, token):
        row = c.execute("SELECT * FROM tasks WHERE id=?", (ident,)).fetchone()
        if not row or row["status"] != "running" or not token or not secrets.compare_digest(row["claim_token"] or "", token):
            raise ValueError("Task is not owned by this claim")
        if row["lease"] < time.time():
            raise ValueError("Claim expired; inspect the task before retrying")
        return row

    def heartbeat(self, ident, token):
        with self.connect(write=True) as c:
            self._owned(c, ident, token)
            c.execute("UPDATE tasks SET lease=? WHERE id=?", (time.time() + 120, ident))
        return {"renewed": True, "lease_seconds": 120}

    def finish(self, ident, token, result):
        result = TaskResult.model_validate(result)
        if result.status == "done" and not result.verification:
            raise ValueError("Done tasks need verification evidence")
        if any(len(x) > 800 for group in (result.artifacts, result.verification, result.unresolved) for x in group):
            raise ValueError("Keep result entries below 800 characters; link large artifacts")
        with self.connect(write=True) as c:
            self._owned(c, ident, token)
            c.execute("UPDATE tasks SET status=?,result=?,claim_token=NULL,lease=NULL,updated=? WHERE id=?",
                      (result.status, result.model_dump_json(), utc(), ident))
            self._event(c, ident, result.status, result.summary)
            self._export(c)
        return {"id": ident, "status": result.status}

    def requeue(self, ident):
        with self.connect(write=True) as c:
            self._expire(c)
            row = c.execute("SELECT status FROM tasks WHERE id=?", (ident,)).fetchone()
            if not row or row[0] not in {"blocked", "failed"}:
                raise ValueError("Only blocked or failed tasks can be explicitly requeued")
            c.execute("UPDATE tasks SET status='pending',owner=NULL,claim_token=NULL,lease=NULL,updated=? WHERE id=?", (utc(), ident))
            self._event(c, ident, "requeued")
            self._export(c)
        return {"id": ident, "status": "pending"}

    def send(self, sender, recipient, body, task=""):
        if not sender or not recipient or max(len(sender), len(recipient)) > 100 or not 1 <= len(body) <= 1600:
            raise ValueError("Provide sender/recipient IDs and a 1–1600 character message")
        with self.connect(write=True) as c:
            if task and not c.execute("SELECT 1 FROM tasks WHERE id=?", (task,)).fetchone():
                raise ValueError("Unknown task")
            result = c.execute("INSERT INTO messages(sender,recipient,task,body,created) VALUES(?,?,?,?,?)",
                               (sender, recipient, task, body, utc()))
            return {"id": result.lastrowid}

    def inbox(self, recipient, after=0):
        with self.connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM messages WHERE recipient=? AND id>? ORDER BY id LIMIT 10",
                                             (recipient, after))]
            return {"messages": rows, "next_cursor": rows[-1]["id"] if rows else after}

    def export(self):
        with self.connect(write=True) as c:
            self._expire(c)
            self._export(c)
        return {"dashboard": "vault/Home.md"}

    def _export(self, c):
        rows = c.execute("SELECT * FROM tasks ORDER BY created DESC,id").fetchall()
        table = ["# HiveMind", "", "Shared memory for connected MCP agent harnesses.", "",
                 "[[START|Start here]] · [[00-System/HIVE|Working agreement]] · [[00-System/Working-Style|Working style]]", "",
                 "## Tasks", "", "Generated from the coordinator. Use Hive tools to change task state.", "",
                 "| Task | Agent | Status | Worker |", "| --- | --- | --- | --- |"]
        for row in rows:
            task = self._decode(row)
            spec, ident = task["spec"], task["id"]
            title = spec["title"].replace("|", "-").replace("\n", " ")
            table.append(f"| [[04-Tasks/{ident}|{title}]] | {row['agent']} | {row['status']} | {row['owner'] or '—'} |")
            frontmatter = "\n".join(f"{k}: {json.dumps(v)}" for k, v in {
                "id": ident, "status": row["status"], "agent": row["agent"], "project": spec["project"],
                "worker": row["owner"] or "", "updated": row["updated"], "depends_on": spec["depends_on"]}.items())
            note = f"---\n{frontmatter}\n---\n\n# {spec['title']}\n\n{spec['objective']}\n\n## Acceptance\n\n"
            note += "\n".join("- " + x for x in spec["acceptance"])
            note += "\n\n## Memory\n\n" + "\n".join(f"- [[{Path(x).with_suffix('').as_posix()}]]" for x in spec["memory"])
            if task["result"]:
                result = task["result"]
                handoff = f"# {ident}: {spec['title']}\n\n{result['summary']}\n"
                for key in ("artifacts", "verification", "unresolved"):
                    handoff += f"\n## {key.title()}\n\n" + "\n".join("- " + x for x in result[key]) + "\n"
                atomic_write(self.vault / "06-Handoffs" / f"{ident}.md", handoff)
                note += f"\n\n## Result\n\n[[06-Handoffs/{ident}|Read handoff]]\n"
            atomic_write(self.vault / "04-Tasks" / f"{ident}.md", note + "\n")
        if not rows:
            table.append("| No tasks yet | — | — | — |")
        table += ["", "## Memory", "", "[[01-Memory/User/Preferences|Your preferences]] · [[03-Projects/HiveMind/Current-State|Current state]]", "",
                  "Memory remains plain Markdown. Search returns small excerpts; archives are excluded by default.", "",
                  f"Last refreshed: {utc()}"]
        atomic_write(self.vault / "Home.md", "\n".join(table) + "\n")
