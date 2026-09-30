"""Actionable local health checks without paid inference or automatic repairs."""
import asyncio
import os
import shlex
import shutil
import socket
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

from .dependencies import local_dependency_status
from .transport import backend


def command(*parts):
    parts = [str(part) for part in parts]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def destination(url):
    if not url:
        return "local"
    parsed = urlsplit(url)
    # Never include embedded credentials or query parameters in diagnostics.
    host = parsed.hostname or "unknown"
    if ":" in host:
        host = "[" + host + "]"
    if parsed.port:
        host += ":" + str(parsed.port)
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


async def inspect(root, source, url, token, config):
    from .cloud import cloud_settings
    from .code_index import installed
    from .semantic import health, ready, interpreter

    checks = []
    cli = [sys.executable, source / "hive.py", "--root", root]
    pins = local_dependency_status(source / "requirements.txt")
    checks.append({"name": "bridge_dependencies", "status": "ok" if pins["ok"] else "failed",
                   "required": True, "detail": "Pinned MCP SDK matches" if pins["ok"] else "Missing or mismatched bridge dependencies",
                   "repair": None if pins["ok"] else command(sys.executable, "-m", "pip", "install", "-r", source / "requirements.txt")})
    profile = config.get("tool_profile", "full")
    valid_profile = profile in {"full", "memory"}
    checks.append({"name": "tool_profile", "status": "ok" if valid_profile else "failed", "required": True,
                   "detail": "MCP tool profile: " + str(profile) if valid_profile else "Unknown MCP tool profile",
                   "repair": None if valid_profile else "Set tool_profile to full or memory in hive.local.json"})
    task_access = "failed"
    try:
        async with asyncio.timeout(20):
            async with backend(root, url, token) as api:
                task_access = "ok" if isinstance(await api.call("task_list", limit=1), list) else "unexpected"
        detail = "Task coordinator responds" if task_access == "ok" else "Task coordinator returned an unexpected response"
    except Exception as exc:
        # Exception strings may contain remote URLs/credentials; report the type only.
        detail = f"Coordinator check failed ({type(exc).__name__})"
        if url and not token:
            detail += "; coordinator authentication is missing"
    checks.append({"name": "coordinator", "status": "ok" if task_access == "ok" else "failed",
                   "required": True, "detail": detail,
                   "repair": None if task_access == "ok" else
                   ("Check coordinator URL, authentication and connectivity; reconnect with hive.py connect --url YOUR_HTTPS_URL"
                    if url else command(*cli, "init"))})

    semantic_status = health(root)
    semantic_selected = (bool(config.get("onboarding_defaults", {}).get("semantic"))
                         or interpreter(root).exists() or ready(root) or semantic_status["status"] != "not_installed")
    semantic_check = ("ok" if semantic_status["status"] == "ready" else
                      "warning" if semantic_selected else "optional")
    checks.append({"name": "semantic_memory", "status": semantic_check, "required": False,
                   "detail": semantic_status["detail"] if semantic_selected else "Optional semantic recall is not installed",
                   "repair": command(*cli, "semantic-setup") if semantic_check == "warning" and not url else None})
    projects = config.get("graphify_projects", [])
    graph_installed = installed(root) if projects else False
    graph_status = "ok" if graph_installed else "warning" if projects else "optional"
    checks.append({"name": "code_index", "status": graph_status, "required": False,
                   "detail": "Graphify is available" if graph_installed else
                             "Enabled Graphify is unavailable" if projects else "Optional Graphify is not enabled",
                   "repair": command(*cli, "code-setup", projects[0]) if projects and not graph_installed and not url else None})
    agents = {name: shutil.which(exe) for name, exe in
              {"codex": "codex", "grok": "grok", "antigravity": "agy"}.items()}
    checks.append({"name": "agent_clients", "status": "ok" if any(agents.values()) else "optional",
                   "required": False, "detail": "Detected clients: " + ", ".join(name for name, path in agents.items() if path)
                   if any(agents.values()) else "No recognized CLI; generic stdio MCP clients remain supported",
                   "repair": None})
    ok = not any(check["required"] and check["status"] == "failed" for check in checks)
    return {"ok": ok, "checks": checks, "coordinator": destination(url),
            "machine": config.get("machine", socket.gethostname()),
            "memory": destination(cloud_settings(root)[0] or url), "agents": agents,
            "task_access": task_access, "code_index": {"enabled_projects": projects, "installed": graph_installed},
            "semantic_memory": {**semantic_status, "installed": semantic_status["status"] == "ready",
                                "authority": destination(url)},
            "bridge_dependencies": pins, "tool_profile": config.get("tool_profile", "full"), "model_calls": 0}


def human(report):
    lines = ["HiveMind health: " + ("OK" if report["ok"] else "FAILED"), ""]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['name']}: {check['detail']}")
        if check["repair"]:
            lines.append("  Repair: " + check["repair"])
    lines.extend(["", "Required checks determine the exit code. Optional features can remain disabled."])
    return "\n".join(lines)
