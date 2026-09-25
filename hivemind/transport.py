"""Same operations locally or through one remote authoritative MCP server."""
import json
from contextlib import asynccontextmanager
from pathlib import Path

TOOLS = {
    "hive_context": "context", "memory_search": "search", "note_read": "read_note",
    "memory_write": "write_memory", "task_create": "create_task", "task_get": "get_task",
    "task_list": "list_tasks", "task_claim": "claim", "task_heartbeat": "heartbeat",
    "task_finish": "finish", "message_send": "send", "message_inbox": "inbox", "memory_catalog": "catalog",
}


class Local:
    def __init__(self, root):
        from .store import Hive
        self.hive = Hive(root)

    async def call(self, tool, **kwargs):
        return getattr(self.hive, TOOLS[tool])(**kwargs)


class Remote:
    def __init__(self, session):
        self.session = session

    async def call(self, tool, **kwargs):
        result = await self.session.call_tool(tool, kwargs)
        text = "\n".join(x.text for x in result.content if x.type == "text")
        if result.isError:
            raise ValueError(text)
        return json.loads(text)


@asynccontextmanager
async def backend(root, url="", token="", *, respect_offline=True):
    from .cloud import CloudMemory, Routed, cloud_settings
    if respect_offline and connection(root)[2].get("offline"):
        url, token = "", ""
    memory_url, memory_token = cloud_settings(root)
    cloud = CloudMemory(root, memory_url, memory_token) if memory_url else None
    if not url:
        yield Routed(Local(root), cloud)
        return
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("Remote connections require HTTPS; HTTP is allowed only on loopback")
    if not token:
        raise ValueError("Remote coordinator needs a token; run 'connect' first")
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, timeout=60) as http:
        async with streamable_http_client(url, http_client=http) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield Routed(Remote(session), cloud)


def connection(root):
    import os
    config_path = Path(root) / "hive.local.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    if config.get("offline"):
        return "", "", config
    url = os.getenv("HIVE_URL", config.get("coordinator_url", ""))
    token = os.getenv("HIVE_TOKEN", "")
    token_path = Path(root) / "runtime" / "client-token"
    if not token and url and token_path.exists():
        token = token_path.read_text().strip()
    return url, token, config
