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
            """)

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

    def read_note(self, path, offset=0, limit=4000):
        target = self.note_path(path)
        if target.stat().st_size > 512_000:
            raise ValueError("Note is too large; split or archive it before retrieval")
        data = target.read_bytes()
        raw = data.decode("utf-8")
        offset, limit = max(0, offset), max(100, min(limit, 8000))
        return {"path": target.relative_to(self.vault).as_posix(),
                "revision": hashlib.sha256(data).hexdigest(),
                "text": raw[offset:offset + limit], "total_chars": len(raw),
                "next_offset": offset + limit if offset + limit < len(raw) else None}

    def write_memory(self, path, content, expected_revision="new"):
        target = self.note_path(path)
        if target.relative_to(self.vault).parts[0] not in {"01-Memory", "02-Decisions", "03-Projects"}:
            raise ValueError("Agent memory writes belong in 01-Memory, 02-Decisions or 03-Projects")
        if not content.strip() or len(content) > 8000:
            raise ValueError("Memory must contain 1–8000 characters; split longer notes")
        with self.connect(write=True) as c:
            current = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else "new"
            if current != expected_revision and current != hashlib.sha256(content.encode()).hexdigest():
                raise ValueError("Note changed; read the current revision before updating")
            atomic_write(target, content)
            self._index_note(c, target)
        return {"path": path, "revision": hashlib.sha256(content.encode()).hexdigest()}

    def _index_note(self, c, path):
        raw = path.read_bytes().decode("utf-8")
        name = path.relative_to(self.vault).as_posix()
        title = next((line[2:] for line in raw.splitlines() if line.startswith("# ")), path.stem)
        c.execute("DELETE FROM notes WHERE path=?", (name,))
        c.execute("INSERT INTO notes VALUES (?,?,?,?)", (name, title, raw, hashlib.sha256(raw.encode()).hexdigest()))

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
                    revision = hashlib.sha256(path.read_bytes()).hexdigest()
                    if existing.get(name) != revision:
                        self._index_note(c, path)
                    count += 1
                except (OSError, UnicodeError, ValueError):
                    continue
            for missing in existing.keys() - seen:
                c.execute("DELETE FROM notes WHERE path=?", (missing,))
        return {"indexed_notes": count}

    def search(self, query, limit=5, archive=False, project=""):
        from .context import search
        return search(self, query, limit, archive, project)

    def context(self, agent="codex", project="", query="", budget_tokens=None):
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

    def _decode(self, row, private=False):
        if row is None:
            raise ValueError("Task not found")
        result = dict(row)
        result["spec"] = json.loads(result["spec"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        if not private:
            result.pop("claim_token", None)
        return result

    def get_task(self, ident):
        with self.connect() as c:
            return self._decode(c.execute("SELECT * FROM tasks WHERE id=?", (ident,)).fetchone())

    def list_tasks(self, status="", limit=25):
        with self.connect() as c:
            rows = c.execute("SELECT * FROM tasks WHERE (?='' OR status=?) ORDER BY created DESC,id LIMIT ?",
                             (status, status, max(1, min(limit, 50))))
            return [{k: v for k, v in self._decode(row).items() if k not in {"result", "spec"}} |
                    {"title": json.loads(row["spec"])["title"], "project": json.loads(row["spec"])["project"]}
                    for row in rows]

    def _expire(self, c):
        # An expired worker may still be editing. Never automatically run its task twice.
        for row in c.execute("SELECT id FROM tasks WHERE status='running' AND lease < ?", (time.time(),)).fetchall():
            c.execute("UPDATE tasks SET status='blocked',claim_token=NULL,updated=? WHERE id=?", (utc(), row["id"]))
            self._event(c, row["id"], "lease_expired", "Inspect worker/worktree, then explicitly requeue")

    def claim(self, worker, agent, ident="", machine="", lease_seconds=120):
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
        table = ["# HiveMind", "", "Shared memory for Codex, Grok Build, and Antigravity.", "",
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
