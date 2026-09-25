"""Authenticated cloud memory with a durable outbox and explicitly stale offline reads."""
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
import httpx

MEMORY_TOOLS = {"hive_context", "memory_search", "note_read", "memory_write", "memory_catalog"}


def cloud_settings(root):
    path = Path(root) / "hive.local.json"
    config = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if config.get("offline"):
        return "", ""
    url = os.getenv("HIVE_MEMORY_URL", config.get("memory_url", ""))
    token = os.getenv("HIVE_MEMORY_TOKEN", "")
    token_path = Path(root) / "runtime" / "cloud-token"
    if url and not token and token_path.exists():
        token = token_path.read_text().strip()
    return url, token


class CloudMemory:
    def __init__(self, root, url, token, http=None):
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise ValueError("Cloud memory requires HTTPS (HTTP is allowed only on loopback)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use a site URL without credentials, query parameters or fragments")
        if not token:
            raise ValueError("Cloud memory needs a connection key; run cloud-connect")
        self.root, self.url, self.token, self.http = Path(root), url.rstrip("/"), token, http
        self.endpoint = self.url if self.url.endswith("/api/hive") else self.url + "/api/hive"
        (self.root / "runtime").mkdir(parents=True, exist_ok=True)
        self.db = self.root / "runtime" / "cloud-sync.db"
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS outbox (
                  id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, args TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
                  created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS cache (
                  id TEXT PRIMARY KEY, response TEXT NOT NULL, fetched TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    async def request(self, tool, args, owner=False):
        async def send(http):
            response = await http.post(self.endpoint,
                headers={"Authorization": "Bearer " + self.token, "OAI-Sites-Authorization": "Bearer " + self.token},
                json={"tool": tool, "args": args, "owner": owner})
            if response.status_code >= 500 or response.status_code == 429:
                raise httpx.HTTPStatusError("Cloud memory temporarily unavailable", request=response.request, response=response)
            if response.status_code in {301,302,303,307,308,401,403}:
                raise ValueError("Cloud access denied. Check this device's connection key.")
            try:
                data = response.json()
            except ValueError:
                raise ValueError("Cloud returned a non-JSON response; check the site URL") from None
            if not response.is_success:
                raise ValueError(data.get("error", "Cloud request rejected"))
            return data["result"]
        if self.http:
            return await send(self.http)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
            return await send(http)

    def counts(self):
        with self.connect() as c:
            return {r["status"]: r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM outbox WHERE endpoint=? GROUP BY status", (self.endpoint,))}

    async def flush(self, limit=10):
        with self.connect() as c:
            rows = c.execute("SELECT id,args FROM outbox WHERE endpoint=? AND status='pending' ORDER BY created,rowid LIMIT ?", (self.endpoint, limit)).fetchall()
        for row in rows:
            try:
                await self.request("memory_write", json.loads(row["args"]))
            except httpx.HTTPError:
                break
            except ValueError as exc:
                with self.connect() as c:
                    c.execute("UPDATE outbox SET status='conflict',error=? WHERE id=?", (str(exc), row["id"]))
            else:
                with self.connect() as c:
                    c.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
        return self.counts()

    async def call(self, tool, **args):
        await self.flush()
        encoded = json.dumps(args, sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256((self.endpoint + tool + encoded).encode()).hexdigest()
        if tool == "memory_write":
            with self.connect() as c:
                c.execute("INSERT OR IGNORE INTO outbox(id,endpoint,args) VALUES(?,?,?)", (key, self.endpoint, encoded))
            try:
                result = await self.request(tool, args)
            except httpx.HTTPError:
                return {"path": args.get("path"), "saved": False, "queued": True, "sync": self.counts(),
                        "warning": "Stored on this device for retry on the next Hive call. Other devices cannot see it yet."}
            except ValueError as exc:
                with self.connect() as c:
                    c.execute("UPDATE outbox SET status='conflict',error=? WHERE id=?", (str(exc), key))
                raise
            with self.connect() as c:
                c.execute("DELETE FROM outbox WHERE id=?", (key,))
            return {**result, "sync": self.counts()}
        try:
            result = await self.request(tool, args)
        except httpx.HTTPError:
            with self.connect() as c:
                row = c.execute("SELECT response,fetched FROM cache WHERE id=?", (key,)).fetchone()
            if not row:
                raise ValueError("Cloud memory offline; no cached result is available. Pending writes are preserved.") from None
            result = json.loads(row["response"])
            warning = {"offline": True, "stale": True, "fetched": row["fetched"], "outbox": self.counts()}
            return {**result, "sync": warning} if isinstance(result, dict) else {"results": result, "sync": warning}
        with self.connect() as c:
            c.execute("INSERT INTO cache(id,response) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET response=excluded.response,fetched=CURRENT_TIMESTAMP", (key, json.dumps(result, ensure_ascii=False)))
        return result


class Routed:
    def __init__(self, authority, cloud=None):
        self.authority, self.cloud = authority, cloud

    async def call(self, tool, **args):
        if tool == "memory_learn":
            from .learning import learning_note
            revision = args.pop("expected_revision", "new")
            path, content = learning_note(**args)
            return await self.call("memory_write", path=path, content=content, expected_revision=revision)
        if self.cloud and tool in MEMORY_TOOLS:
            return await self.cloud.call(tool, **args)
        return await self.authority.call(tool, **args)
