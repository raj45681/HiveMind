"""Disposable, model-free acceptance check for the shared MCP handoff path."""
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path


def _git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=15, check=True)


async def _call(client, name, **arguments):
    result = await client.call_tool(name, arguments)
    body = "\n".join(item.text for item in result.content if item.type == "text")
    if result.isError:
        raise ValueError(f"{name}: {body[:300]}")
    return json.loads(body)


@asynccontextmanager
async def _bridge(source, root):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(source / "hive.py"), "--root", str(root), "serve"],
    )
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as client:
                try:
                    await client.initialize()
                except Exception as exc:
                    errlog.seek(0)
                    detail = errlog.read()[-500:].strip()
                    if detail:
                        raise ValueError(f"MCP bridge failed: {detail}") from exc
                    raise
                yield client


async def _exercise(source, root, project):
    checks = []
    path = "03-Projects/verify_a/Amber-Ledger.md"
    other = "03-Projects/verify_b/Amber-Ledger.md"
    content = "# Amber ledger\nSource: synthetic HiveMind verification fixture.\nAmber ledger fixes rotated-token handoffs with a local checkpoint.\n"
    other_content = "# Amber ledger\nSource: synthetic HiveMind verification fixture.\nAmber ledger in the other project uses a different policy.\n"

    async with _bridge(source, root) as client:
        available = {tool.name for tool in (await client.list_tools()).tools}
        required = {"hive_context", "memory_write", "memory_search",
                    "session_start", "session_checkpoint", "session_resume"}
        if not required <= available:
            raise ValueError("MCP bridge is missing: " + ", ".join(sorted(required - available)))
        checks.append("MCP bridge starts with required tools")
        saved = await _call(client, "memory_write", path=path, content=content,
                            expected_revision="new")
        retry = await _call(client, "memory_write", path=path, content=content,
                            expected_revision="new")
        if saved["revision"] != retry["revision"]:
            raise ValueError("Identical memory retry changed the revision")
        await _call(client, "memory_write", path=other, content=other_content,
                    expected_revision="new")
        checks.append("Memory write survives an identical retry")
        started = await _call(client, "session_start", project="verify_a",
                              agent="codex", goal="Hand off the amber ledger fix")
        ident = started["session"]["id"]
        if not started.get("saved"):
            raise ValueError("Session start was not saved")
        checkpoint = await _call(client, "session_checkpoint", ident=ident,
            expected_revision=0,
            checkpoint={"summary": "Amber ledger fix prepared for the next agent.",
                        "status": "needs_handoff",
                        "completed": ["Prepared synthetic fix"],
                        "verification": ["Synthetic verification fixture"],
                        "next_steps": ["Review the live worktree before continuing"]})
        if not checkpoint.get("saved") or checkpoint["session"]["revision"] != 1:
            raise ValueError("Checkpoint was not saved at revision 1")
        checks.append("Codex checkpoint is durable")

    # A second server process represents a newly started client, not a paid agent run.
    async with _bridge(source, root) as client:
        found = await _call(client, "memory_search", query="amber ledger",
                            project="verify_a", limit=3)
        paths = {item["path"] for item in found}
        if path not in paths or other in paths:
            raise ValueError("Project-scoped search lost the note or leaked another project")
        checks.append("Fresh client retrieves the right project memory")
        brief = await _call(client, "hive_context", agent="grok",
                            project="verify_a", query="amber ledger", budget_tokens=1000)
        relevant = {item["path"] for item in brief["relevant"]}
        if path not in relevant or other in relevant:
            raise ValueError("Budgeted Grok brief lost relevant memory or leaked another project")
        if brief["budget"]["estimated_tokens"] > 1000:
            raise ValueError("Grok brief exceeded its estimated-token budget")
        if not brief["session"] or brief["session"]["id"] != ident:
            raise ValueError("Grok brief did not include the latest handoff")
        checks.append("Grok brief includes relevant memory and handoff within 1000 estimated tokens")
        resumed = await _call(client, "session_resume", project="verify_a")
        if resumed["session"]["git_drift"]["status"] != "match":
            raise ValueError("Unchanged worktree did not match the saved checkpoint")
        (project / "README.md").write_text("Changed after handoff\n", encoding="utf-8")
        drifted = await _call(client, "session_resume", project="verify_a")
        if drifted["session"]["git_drift"]["status"] != "changed":
            raise ValueError("Worktree change was not detected after handoff")
        checks.append("Fresh client detects Git drift before trusting the handoff")
    return checks, brief["budget"]["estimated_tokens"]


async def verify(source):
    """Exercise real stdio MCP processes against a disposable vault and Git repo."""
    started = time.monotonic()
    source = Path(source).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="hivemind-verify-") as directory:
            root = Path(directory)
            project = root / "project-a"
            project.mkdir()
            (project / "README.md").write_text("Baseline\n", encoding="utf-8")
            _git(project, "init", "-q")
            _git(project, "add", "README.md")
            _git(project, "-c", "user.name=HiveMind Verify", "-c",
                 "user.email=verify@example.invalid", "commit", "-qm", "baseline")
            (root / "hive.local.json").write_text(json.dumps({"offline": True,
                "projects": {"verify_a": str(project)}}), encoding="utf-8")
            checks, tokens = await asyncio.wait_for(_exercise(source, root, project), timeout=90)
        return {"ok": True, "checks": checks, "estimated_brief_tokens": tokens,
                "model_calls": 0, "seconds": round(time.monotonic() - started, 2),
                "scope": "disposable local MCP processes; vendor model compliance not tested"}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, asyncio.TimeoutError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "model_calls": 0, "seconds": round(time.monotonic() - started, 2)}
