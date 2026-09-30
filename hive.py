"""Portable CLI entry point. No agent/model runs unless 'run' is explicitly used."""
import argparse
import asyncio
import getpass
import json
import os
import re
import shutil
import socket
import sys
from pathlib import Path

from hivemind.store import Hive, atomic_write
from hivemind.transport import backend, connection

ROOT = Path(__file__).resolve().parent


def parser():
    p = argparse.ArgumentParser(description="HiveMind — Obsidian memory and agent coordination")
    p.add_argument("--root", type=Path, default=ROOT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Initialize local coordinator and note index")
    s = sub.add_parser("doctor", help="Check local tools and coordinator access without model usage")
    s.add_argument("--human", action="store_true", help="Show a readable summary and repair commands instead of JSON")
    sub.add_parser("verify", help="Run a disposable end-to-end MCP memory and handoff check without model usage")
    s = sub.add_parser("benchmark", help="Measure retrieval and tool schema sizes in a disposable synthetic vault")
    s.add_argument("--distractors", type=int, default=200, help="Synthetic background notes (0-2000)")
    s.add_argument("--semantic", action="store_true", help="Reuse an installed local model cache; never download")
    s.add_argument("--check", action="store_true", help="Exit nonzero when required retrieval, scope or budget checks fail")
    sub.add_parser("status", help="List tasks from the selected coordinator")
    sub.add_parser("index", help="Fully reconcile the local Markdown search index")
    sub.add_parser("semantic-setup", help="Install the optional local semantic memory model")
    sub.add_parser("export", help="Refresh generated Obsidian task views")
    s = sub.add_parser("code-setup", help="Install optional isolated Graphify and index an enrolled project")
    s.add_argument("project")
    s = sub.add_parser("code-index", help="Refresh local code graph without model calls")
    s.add_argument("project")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("code-query", help="Query a bounded local code graph; refresh changed source automatically")
    s.add_argument("project")
    s.add_argument("query")
    s.add_argument("--budget", type=int, default=1000)
    s = sub.add_parser("context", help="Read a budgeted, project-aware brief without inference")
    s.add_argument("project")
    s.add_argument("--agent", default="generic", help="Lowercase harness ID, e.g. codex or cursor")
    s.add_argument("--query", default="")
    s.add_argument("--budget", type=int)
    s.add_argument('--task', default='', help='Include task files, dependency results and linked memory')
    s.add_argument('--file', action='append', default=[], help='Project-relative affected file; repeat for more files')
    s = sub.add_parser('learn', help='Save structured scoped learning from a JSON file')
    s.add_argument('file', type=Path)
    s = sub.add_parser('memory-relate', help='Record replacement or conflict between current memory notes')
    s.add_argument('relation', choices=('supersedes', 'conflicts', 'resolve'))
    s.add_argument('path')
    s.add_argument('related')
    s.add_argument('--revision', required=True)
    s.add_argument('--related-revision', required=True)
    s.add_argument('--source', required=True)
    s = sub.add_parser('consolidate', help='Find duplicate learned notes and optionally stage a merge proposal')
    s.add_argument('project', nargs='?', default='')
    s.add_argument('--path', action='append', default=[])
    s.add_argument('--summary', default='')
    s.add_argument('--stage', action='store_true')
    s = sub.add_parser('learning-review', help='Explicitly accept or reject an outcome/consolidation proposal')
    s.add_argument('path')
    s.add_argument('--revision', required=True)
    decision = s.add_mutually_exclusive_group(required=True)
    decision.add_argument('--accept', action='store_true')
    decision.add_argument('--reject', action='store_true')
    s.add_argument('--content-file', type=Path, help='Optional reviewer-authored replacement content')
    s = sub.add_parser('goal-create', help='Save a reviewable goal plan from JSON; never starts agents')
    s.add_argument('file', type=Path)
    s = sub.add_parser('goal-show', help='Show a goal dependency graph and execution limits')
    s.add_argument('goal')
    for command in ('goal-activate', 'goal-integrated', 'goal-release'):
        s = sub.add_parser(command, help='Review-controlled goal state change')
        s.add_argument('goal')
        s.add_argument('--revision', required=True, type=int)
        if command == 'goal-integrated':
            s.add_argument('--key', required=True)
        if command != 'goal-activate':
            s.add_argument('--source', required=True)
    s = sub.add_parser('goal-run', help='Preview a goal or explicitly execute ready tasks within its limits')
    s.add_argument('goal')
    s.add_argument('--execute', action='store_true', help='Uses configured agent accounts')
    s.add_argument('--max-tasks', type=int)
    s.add_argument('--max-seconds', type=int)
    s.add_argument('--max-parallel', type=int)
    s = sub.add_parser("context-budget", help="Set the default estimated-token budget (512-8192)")
    s.add_argument("tokens", type=int)
    s = sub.add_parser("session-start", help="Start a durable session without launching an agent")
    s.add_argument("project")
    s.add_argument("--agent", default="codex", help="Lowercase harness ID, e.g. codex or cursor")
    s.add_argument("--goal", required=True)
    s = sub.add_parser("checkpoint", help="Save structured JSON using the last session revision")
    s.add_argument("session")
    s.add_argument("file", type=Path)
    s.add_argument("--revision", type=int, required=True)
    s = sub.add_parser("resume", help="Read a project's most recent session; does not launch agents")
    s.add_argument("project")
    s.add_argument("--session", default="")
    s = sub.add_parser("session-run", help="Explicitly launch an interactive agent with durable exit capture; uses its account")
    s.add_argument("project")
    s.add_argument("agent", help="Lowercase harness ID; pass its CLI arguments after --")
    s.add_argument("agent_args", nargs=argparse.REMAINDER)
    s = sub.add_parser("client-info", help="Show generic stdio MCP connection details for an enrolled project")
    s.add_argument("project")
    s.add_argument("--profile", choices=("full", "memory"), help="Override the device's default tool profile for this client")
    sub.add_parser("offline", help="Use only this folder; ignore hosted URLs and preserve existing remote data")
    s = sub.add_parser("backup", help="Create a portable local bundle with Markdown and a consistent task database snapshot")
    s.add_argument("file", type=Path)
    s = sub.add_parser("search", help="Search bounded memory excerpts")
    s.add_argument("value")
    s.add_argument("--project", default="")
    s.add_argument("--handoffs", action="store_true", help="Include stored task, checkpoint and message handoffs")
    s = sub.add_parser("read", help="Read a note or saved revision")
    s.add_argument("value")
    s.add_argument("--revision", default="")
    s.add_argument("--history", action="store_true")
    s = sub.add_parser("history", help="List saved revisions for a local Markdown note")
    s.add_argument("path")
    s = sub.add_parser("diff", help="Show a bounded diff between a saved revision and current note")
    s.add_argument("path")
    s.add_argument("old_revision")
    s.add_argument("new_revision", nargs="?", default="current")
    s = sub.add_parser("restore", help="Restore an agent-writable note with a current-revision check")
    s.add_argument("path")
    s.add_argument("revision")
    s.add_argument("--expected-revision", required=True)
    s = sub.add_parser("memory-audit", help="Read-only checks for malformed or duplicate local memory")
    s.add_argument("--project", default="")
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("candidate-inbox", help="List inferred preferences awaiting explicit review")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--project", default="")
    s = sub.add_parser("review-dashboard", help="Generate an Obsidian page for memory awaiting review")
    s.add_argument("--project", default="")
    s.add_argument("--stale-days", type=int, default=180)
    s.add_argument("--limit", type=int, default=25)
    for command in ("candidate-approve", "candidate-reject", "procedure-archive", "procedure-unarchive"):
        s = sub.add_parser(command, help="Revision-checked local memory review")
        s.add_argument("path")
        s.add_argument("--expected-revision", required=True)
    s = sub.add_parser("procedure-report", help="Read-only procedure age, recorded use and duplicate report")
    s.add_argument("--project", default="")
    s.add_argument("--stale-days", type=int, default=180)
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("procedure-used", help="Record one verified application of a procedure")
    s.add_argument("path")
    s.add_argument("--expected-revision", required=True)
    s.add_argument("--source", required=True)
    s.add_argument("--evidence", required=True)
    s = sub.add_parser("review-checkpoint", help="Bounded opt-in checkpoint review packet, without an agent call")
    s.add_argument("session")
    s.add_argument("--budget", type=int, default=1000)
    s.add_argument("--stage", action="store_true", help="Save a draft in the project review queue")
    for command in ("recovery-enable", "recovery-disable"):
        s = sub.add_parser(command, help="Opt a mapped Git project into or out of private milestone snapshots")
        s.add_argument("project")
    s = sub.add_parser("recovery-list", help="List private milestone recovery snapshots")
    s.add_argument("--project", default="")
    s.add_argument("--limit", type=int, default=25)
    s = sub.add_parser("recovery-diff", help="Preview changed files or a bounded single-file diff")
    s.add_argument("snapshot")
    s.add_argument("--path", default="")
    s = sub.add_parser("recovery-restore", help="Restore one previewed file if its current hash still matches")
    s.add_argument("snapshot")
    s.add_argument("path")
    s.add_argument("--expected-current", required=True)
    s = sub.add_parser("recovery-undo", help="Undo a restore if the file has not changed since")
    s.add_argument("undo")
    s.add_argument("--expected-current", required=True)
    s = sub.add_parser("recovery-prune", help="Remove old private snapshots for one project")
    s.add_argument("project")
    s.add_argument("--keep", type=int, default=10)
    s.add_argument("--drop-undos", action="store_true", help="Also remove this project's restore undo records")
    s = sub.add_parser("handoff-search", help="Search stored task, checkpoint and message handoffs")
    s.add_argument("query")
    s.add_argument("--project", default="")
    s.add_argument("--limit", type=int, default=5)
    s = sub.add_parser("create", help="Queue a task from a JSON file")
    s.add_argument("file", type=Path)
    s = sub.add_parser("run", help="Execute ONE task using the assigned CLI/account; consumes agent usage")
    s.add_argument("task")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("requeue", help="Requeue blocked/failed task after inspecting its old worker/worktree")
    s.add_argument("task")
    s = sub.add_parser("project-add", help="Map a shared project ID to a local repository")
    s.add_argument("name")
    s.add_argument("path", type=Path)
    s = sub.add_parser("attach", help="Enable automatic HiveMind workflow in a project, preserving existing rules")
    s.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    s.add_argument("--name", default="", help="Shared project ID; defaults to the directory name or existing enrollment")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--skip-register", action="store_true", help="Use existing MCP registration without changing CLI configuration")
    s = sub.add_parser("connect", help="Join a coordinator; token is entered privately")
    s.add_argument("--url", required=True)
    s.add_argument("--token-file", type=Path)
    sub.add_parser("disconnect", help="Switch this device back to its own local coordinator")
    s = sub.add_parser("cloud-connect", help="Connect this device to private online memory; key entered privately")
    s.add_argument("--url", required=True)
    s.add_argument("--token-file", type=Path)
    sub.add_parser("cloud-disconnect", help="Disconnect cloud memory without deleting stored data")
    s = sub.add_parser("enroll-cloud", help="Connect to the bundle's default cloud once per device")
    s.add_argument("--url", default="")
    sub.add_parser("memory-import", help="Copy vault notes to cloud, preserving existing conflicting notes")
    s = sub.add_parser("memory-sync", help="Pull cloud notes into Obsidian without overwriting local edits")
    s.add_argument("--push", action="store_true", help="Also push edits to previously mirrored notes using revision checks")
    sub.add_parser("memory-flush", help="Retry queued memory updates without invoking an agent")
    s = sub.add_parser("memory-outbox", help="Inspect locally pending/conflicting updates")
    s.add_argument("--discard", default="", help="Discard one explicitly selected local outbox entry by ID")
    s = sub.add_parser("serve", help="MCP via stdio (default) or authenticated loopback HTTP")
    s.add_argument("--http", action="store_true")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--hostname", default="", help="Private HTTPS hostname used by your reverse proxy")
    s.add_argument("--profile", choices=("full", "memory"), help="Override the device's default MCP tool profile")
    return p


def save_config(root, config):
    atomic_write(root / "hive.local.json", json.dumps(config, indent=2) + "\n")


async def execute(args):
    root = args.root.resolve()
    if args.cmd == "verify":
        from hivemind.verify import verify
        return await verify(ROOT)
    if args.cmd == "benchmark":
        from hivemind.benchmark import run
        return await run(root, args.distractors, args.semantic)
    url, token, config = connection(root)
    if args.cmd == "doctor":
        from hivemind.diagnostics import inspect
        return await inspect(root, ROOT, url, token, config)
    if args.cmd == 'goal-run':
        if url:
            raise ValueError('Run goal execution on the local task authority')
        from hivemind.goals import run
        return await run(Hive(root), args.goal, config, args.execute, args.max_tasks, args.max_seconds, args.max_parallel)
    if args.cmd == "client-info":
        from hivemind.context import validate_project
        from hivemind.sessions import project_path
        validate_project(args.project, required=True)
        project = project_path(root, args.project)
        if not project or not project.is_dir():
            raise ValueError("Enroll or map this project on this device first")
        workflow = project / "AGENTS.md"
        if not workflow.is_file():
            raise ValueError("Project has no AGENTS.md workflow; enroll it with hive.py attach first")
        venv_python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python = venv_python if venv_python.is_file() else Path(sys.executable)
        profile = args.profile or config.get("tool_profile", "full")
        if profile not in ("full", "memory"):
            raise ValueError("Invalid tool_profile in hive.local.json")
        serve_args = [str(root / "hive.py"), "serve"]
        if profile != "full" or args.profile:
            serve_args += ["--profile", profile]
        return {"project": args.project, "agent_id_example": "cursor",
                "mcp": {"name": "hivemind", "transport": "stdio", "command": str(python),
                        "args": serve_args, "tool_profile": profile},
                "workflow_file": str(workflow),
                "note": "Configure this stdio server in your client's MCP settings and make its project instructions load AGENTS.md. Restart the client, then call hive_context with this project ID. Client-specific trust and MCP prompts still apply."}
    if args.cmd in {"history", "diff", "restore", "memory-audit", "handoff-search",
                    "candidate-inbox", "candidate-approve", "candidate-reject", "procedure-report",
                    "procedure-archive", "procedure-unarchive", "procedure-used", "review-checkpoint", "review-dashboard",
                    "recovery-enable", "recovery-disable", "recovery-list", "recovery-diff",
                    "recovery-restore", "recovery-undo", "recovery-prune"}:
        if url:
            raise ValueError("Run local memory inspection on the device holding the HiveMind vault")
        hive = Hive(root)
        if args.cmd == "history":
            return hive.note_history(args.path)
        if args.cmd == "diff":
            hive.index()
            return hive.note_diff(args.path, args.old_revision, args.new_revision)
        if args.cmd == "restore":
            return hive.restore_note(args.path, args.revision, args.expected_revision)
        if args.cmd == "memory-audit":
            return hive.memory_audit(args.project, args.limit)
        if args.cmd == "handoff-search":
            return hive.handoff_search(args.query, args.project, args.limit)
        if args.cmd.startswith("recovery-"):
            from hivemind import recovery
            from hivemind.context import validate_project
            if args.cmd in {"recovery-enable", "recovery-disable"}:
                validate_project(args.project, required=True)
                if args.cmd == "recovery-enable":
                    from hivemind.sessions import git_snapshot
                    observed = git_snapshot(root, args.project)
                    if not observed.get("available"):
                        raise ValueError(observed.get("reason", "Mapped Git project unavailable"))
                projects = set(config.get("recovery_projects", []))
                if args.cmd == "recovery-enable":
                    projects.add(args.project)
                else:
                    projects.discard(args.project)
                config["recovery_projects"] = sorted(projects)
                save_config(root, config)
                return {"project": args.project, "enabled": args.cmd == "recovery-enable",
                        "note": "New sessions and checkpoints use this setting; existing snapshots remain available"}
            if args.cmd == "recovery-list":
                return recovery.list_snapshots(hive, args.project, args.limit)
            if args.cmd == "recovery-diff":
                return recovery.preview(hive, args.snapshot, args.path)
            if args.cmd == "recovery-restore":
                return recovery.restore_file(hive, args.snapshot, args.path, args.expected_current)
            if args.cmd == "recovery-undo":
                return recovery.undo_restore(hive, args.undo, args.expected_current)
            return recovery.prune(hive, args.project, args.keep, args.drop_undos)
        from hivemind import learning_ops
        if args.cmd == "review-dashboard":
            from hivemind.review import render
            return render(hive, args.project, args.stale_days, args.limit)
        if args.cmd == "candidate-inbox":
            return learning_ops.candidate_inbox(hive, args.limit, args.project)
        if args.cmd == "candidate-approve":
            return learning_ops.candidate_approve(hive, args.path, args.expected_revision)
        if args.cmd == "candidate-reject":
            return learning_ops.candidate_reject(hive, args.path, args.expected_revision)
        if args.cmd == "procedure-report":
            return learning_ops.procedure_report(hive, args.project, args.stale_days, args.limit)
        if args.cmd == "procedure-used":
            return learning_ops.procedure_used(hive, args.path, args.expected_revision, args.source, args.evidence)
        if args.cmd == "procedure-archive":
            return learning_ops.procedure_archive(hive, args.path, args.expected_revision)
        if args.cmd == "procedure-unarchive":
            return learning_ops.procedure_unarchive(hive, args.path, args.expected_revision)
        return learning_ops.review_checkpoint(hive, args.session, args.budget, args.stage)
    if args.cmd == "semantic-setup":
        if url:
            raise ValueError("Install semantic recall on the coordinator device that holds the vault")
        from hivemind.semantic import setup
        return setup(root)
    if args.cmd.startswith("code-"):
        from hivemind.code_index import enable, operate
        if args.cmd == "code-setup":
            return enable(root, args.project)
        return operate(root, args.project, query=getattr(args, "query", ""),
                       budget_tokens=getattr(args, "budget", 1000),
                       force=getattr(args, "force", False), build_only=args.cmd == "code-index")
    if args.cmd == "context-budget":
        if url:
            raise ValueError("Set the default on the coordinator device, or pass context --budget for this call")
        if not 512 <= args.tokens <= 8192:
            raise ValueError("Context budget must be 512-8192 estimated tokens")
        config["context_budget_tokens"] = args.tokens
        save_config(root, config)
        return {"context_budget_tokens": args.tokens, "scope": "local authority; per-call overrides remain available"}
    if args.cmd == "offline":
        previous = root / "runtime" / "connection-before-offline.json"
        if not config.get("offline"):
            atomic_write(previous, json.dumps(config, indent=2) + "\n")
        config["offline"] = True
        config.pop("memory_url", None)
        config.pop("coordinator_url", None)
        save_config(root, config)
        return {"mode": "local-only", "folder": str(root), "memory": str(root / "vault"),
                "note": "Cloud data is preserved. This folder is now the authority; no automatic cloud synchronization."}
    if args.cmd == "backup":
        from scripts.package import build_bundle
        return build_bundle(root, args.file.resolve(), include_state=True)
    if args.cmd == "enroll-cloud":
        if config.get("offline") and not args.url:
            return {"memory": "local", "enrollment": "offline mode; no cloud connection"}
        defaults = root / "cloud-defaults.json"
        wanted = args.url or os.getenv("HIVE_MEMORY_URL", "") or (json.loads(defaults.read_text()).get("memory_url", "") if defaults.exists() else "")
        if not wanted or config.get("memory_url", "").rstrip("/") == wanted.rstrip("/"):
            return {"memory": config.get("memory_url", "local"), "enrollment": "unchanged"}
        args.cmd, args.url, args.token_file = "cloud-connect", wanted, None
    if args.cmd == "cloud-connect":
        from hivemind.cloud import CloudMemory
        key = args.token_file.read_text().strip() if args.token_file else getpass.getpass("Private cloud connection key (hidden): ")
        cloud = CloudMemory(root, args.url, key)
        await cloud.request("hive_context", {})
        path = root / "runtime" / "cloud-token"
        atomic_write(path, key)
        path.chmod(0o600)
        config["memory_url"] = cloud.url
        config["offline"] = False
        save_config(root, config)
        return {"memory_connected": cloud.url, "note": "Future Hive tool calls use online memory. Existing task execution settings are preserved."}
    if args.cmd == "cloud-disconnect":
        config.pop("memory_url", None)
        save_config(root, config)
        return {"memory": "local", "note": "Cloud data and device outbox remain intact."}
    if args.cmd.startswith("memory-"):
        from hivemind.cloud import CloudMemory, cloud_settings
        from hivemind.sync import import_vault, mirror
        memory_url, memory_key = cloud_settings(root)
        if not memory_url:
            raise ValueError("Connect to cloud memory first using cloud-connect")
        cloud = CloudMemory(root, memory_url, memory_key)
        if args.cmd == "memory-import":
            return await import_vault(root, cloud)
        if args.cmd == "memory-sync":
            return await mirror(root, cloud, args.push)
        if args.cmd == "memory-flush":
            return {"outbox": await cloud.flush(limit=100)}
        if args.cmd == "memory-outbox":
            with cloud.connect() as c:
                if args.discard:
                    c.execute("DELETE FROM outbox WHERE id=? AND endpoint=?", (args.discard, cloud.endpoint))
                rows = c.execute("SELECT id,args,status,error,created FROM outbox WHERE endpoint=?", (cloud.endpoint,)).fetchall()
            return [{"id": r["id"], "path": json.loads(r["args"]).get("path"), "status": r["status"], "error": r["error"], "created": r["created"]} for r in rows]
    if args.cmd == "attach":
        from hivemind.project import attach
        return await attach(root, args.path, args.name, args.skip_register, args.dry_run)
    if args.cmd == "connect":
        token = args.token_file.read_text().strip() if args.token_file else getpass.getpass("Coordinator token (hidden): ")
        async with backend(root, args.url, token, respect_offline=False) as api:
            await api.call("task_list", limit=1)
        path = root / "runtime" / "client-token"
        atomic_write(path, token)
        path.chmod(0o600)
        config["coordinator_url"] = args.url
        config["offline"] = False
        save_config(root, config)
        return {"connected": args.url, "note": "Restart agent sessions to reload their HiveMind MCP bridge."}
    if args.cmd == "disconnect":
        config.pop("coordinator_url", None)
        save_config(root, config)
        return {"mode": "local", "note": "Remote data remains on the coordinator."}
    if args.cmd == "project-add":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", args.name) or not args.path.is_dir():
            raise ValueError("Use a simple project ID and an existing directory")
        config.setdefault("projects", {})[args.name] = str(args.path.resolve())
        save_config(root, config)
        return {"project": args.name, "path": str(args.path.resolve())}
    if args.cmd in {"init", "index", "export", "requeue"}:
        if url:
            raise ValueError("This operation is local to the authority. Run it on the coordinator device.")
        hive = Hive(root)
        if args.cmd == "init":
            from hivemind.seed import seed_vault
            seed_vault(root)
            config.setdefault("machine", socket.gethostname())
            config.setdefault("projects", {"hivemind": str(root)})
            save_config(root, config)
            hive.export()
            return hive.index()
        return {"index": lambda: hive.index(force=True), "export": hive.export,
                "requeue": lambda: hive.requeue(args.task)}[args.cmd]()
    async with backend(root, url, token) as api:
        if args.cmd == 'learn':
            return await api.call('memory_learn', **json.loads(args.file.read_text(encoding='utf-8-sig')))
        if args.cmd == 'memory-relate':
            return await api.call('memory_relate', path=args.path, related=args.related, relation=args.relation,
                                  expected_revision=args.revision, related_revision=args.related_revision, source=args.source)
        if args.cmd == 'consolidate':
            return await api.call('memory_consolidate', project=args.project, paths=args.path, summary=args.summary, stage=args.stage)
        if args.cmd == 'learning-review':
            content = args.content_file.read_text(encoding='utf-8') if args.content_file else ''
            return await api.call('learning_review', path=args.path, expected_revision=args.revision, accept=args.accept, content=content)
        if args.cmd == 'goal-create':
            return await api.call('goal_create', spec=json.loads(args.file.read_text(encoding='utf-8-sig')))
        if args.cmd == 'goal-show':
            return await api.call('goal_status', ident=args.goal)
        if args.cmd in {'goal-activate', 'goal-integrated', 'goal-release'}:
            action = {'goal-activate': 'activate', 'goal-integrated': 'integrate', 'goal-release': 'release'}[args.cmd]
            return await api.call('goal_control', ident=args.goal, action=action, expected_revision=args.revision,
                                  key=getattr(args, 'key', ''), source=getattr(args, 'source', ''))
        if args.cmd == "context":
            return await api.call("hive_context", agent=args.agent, project=args.project,
                                  query=args.query, budget_tokens=args.budget, task_id=args.task, files=args.file)
        if args.cmd == "session-start":
            return await api.call("session_start", project=args.project, agent=args.agent, goal=args.goal)
        if args.cmd == "checkpoint":
            return await api.call("session_checkpoint", ident=args.session,
                                  checkpoint=json.loads(args.file.read_text(encoding="utf-8-sig")), expected_revision=args.revision)
        if args.cmd == "resume":
            return await api.call("session_resume", project=args.project, ident=args.session)
        if args.cmd == "session-run":
            from hivemind.session_runner import run_session
            return await run_session(root, api, args.project, args.agent, args.agent_args, config)
        if args.cmd == "status":
            return await api.call("task_list")
        if args.cmd == "search":
            return await api.call("memory_search", query=args.value, project=args.project,
                                  include_handoffs=args.handoffs)
        if args.cmd == "read":
            return await api.call("note_read", path=args.value, revision=args.revision,
                                  include_history=args.history)
        if args.cmd == "create":
            return await api.call("task_create", spec=json.loads(args.file.read_text(encoding="utf-8-sig")))
        if args.cmd == "run":
            from hivemind.worker import run_task
            return await run_task(root, api, args.task, config, args.dry_run)


def main():
    args = parser().parse_args()
    if args.cmd == "serve":
        from hivemind.server import BearerAuth, build_server, server_token
        url, token, config = connection(args.root)
        if args.http and url:
            raise ValueError("A joined device cannot become a second authority; use stdio bridge")
        profile = args.profile or config.get("tool_profile", "full")
        server = build_server(args.root, url, token, args.hostname, profile)
        if args.http:
            import uvicorn
            app = BearerAuth(server.streamable_http_app(), server_token(args.root))
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
        else:
            server.run(transport="stdio")
        return
    result = asyncio.run(execute(args))
    if args.cmd == "doctor" and args.human:
        from hivemind.diagnostics import human
        print(human(result))
    elif args.cmd == "code-query":
        from hivemind.code_index import compact
        print(compact(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.cmd == "session-run" and result.get("exit_code"):
        sys.exit(1)
    if args.cmd == "verify" and not result["ok"]:
        sys.exit(1)
    if args.cmd == "doctor" and not result["ok"]:
        sys.exit(1)
    if args.cmd == "benchmark" and args.check and not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print("HiveMind: " + str(exc), file=sys.stderr)
        sys.exit(1)
