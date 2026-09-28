"""Small, model-free checks and optional profile capture for project onboarding."""
import asyncio
import argparse
import json
import subprocess
import sys
from pathlib import Path


def choose_setup(args, config, first_setup, *, interactive=None, input_fn=input, output=sys.stdout):
    """Choose optional local features once; flags remain useful in scripts."""
    interactive = sys.stdin.isatty() if interactive is None else interactive
    defaults = config.get("onboarding_defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("Invalid saved onboarding defaults in hive.local.json")
    graphify = bool(defaults.get("graphify"))
    semantic = bool(defaults.get("semantic"))
    other_client = bool(getattr(args, "other_client", False))
    explicit = args.with_graphify or args.with_semantic or args.personalize or other_client
    if args.configure:
        if args.dry_run or args.no_prompt or explicit:
            raise ValueError("Use --configure by itself; it opens the setup menu")
        if not interactive:
            raise ValueError("--configure needs an interactive terminal")
    show_menu = (args.configure or (first_setup and not args.no_prompt and not explicit and not args.dry_run and interactive))
    personalize = bool(args.personalize)
    if show_menu:
        previous = (int(graphify), int(semantic))
        default_choice = {(0, 0): "0", (0, 1): "1", (1, 0): "2", (1, 1): "3"}[previous]
        print("\nChoose your HiveMind setup (local, no paid agent call):", file=output)
        print("  0  Core memory + MCP only", file=output)
        print("  1  Core + semantic recall (local model download)", file=output)
        print("  2  Core + Graphify code graphs (Python 3.12+)", file=output)
        print("  3  Core + semantic recall + Graphify", file=output)
        print("  4  All of the above + two working-style questions", file=output)
        print("  5  Other MCP client + core memory (manual client connection)", file=output)
        while True:
            try:
                choice = input_fn(f"Choose 0-5 [default {default_choice}]: ").strip() or default_choice
            except EOFError:
                choice = default_choice
            if choice in {"0", "1", "2", "3", "4", "5"}:
                break
            print("Please choose 0, 1, 2, 3, 4 or 5.", file=output)
        graphify = choice in {"2", "3", "4"}
        semantic = choice in {"1", "3", "4"}
        personalize = choice == "4"
        other_client = choice == "5"
    else:
        graphify = graphify or args.with_graphify
        semantic = semantic or args.with_semantic
    return {"graphify": graphify, "semantic": semantic, "personalize": personalize,
            "other_client": other_client,
            "persist": bool(first_setup or args.configure), "menu_shown": show_menu}


def setup_extras(root, python, project, options):
    """Install selected extras independently and inspect their actual CLI results."""
    root = Path(root).resolve()
    extras = {}
    if options["semantic"]:
        from .semantic import health as semantic_health
        initial_health = semantic_health(root)
        if initial_health["status"] == "ready":
            extras["Semantic"] = {"status": "ready", "detail": "offline model probe passed"}
        else:
            print("Preparing local semantic recall; this may download the isolated model...", flush=True)
            try:
                result = subprocess.run([str(python), str(root / "hive.py"), "semantic-setup"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
                payload = json.loads(result.stdout) if result.returncode == 0 else {}
                if not isinstance(payload, dict):
                    raise ValueError("Semantic setup returned an unexpected result")
                verified = semantic_health(root) if result.returncode == 0 and payload.get("installed") else initial_health
                if result.returncode == 0 and payload.get("installed") and verified["status"] == "ready":
                    extras["Semantic"] = {"status": "ready", "detail": "offline model probe passed"}
                else:
                    detail = verified["detail"] if result.returncode == 0 else (result.stderr.strip() or result.stdout.strip() or "Semantic setup did not confirm installation")
                    extras["Semantic"] = {"status": "needs-action", "detail": detail[-300:]}
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                extras["Semantic"] = {"status": "needs-action", "detail": str(exc)[-300:]}
    else:
        extras["Semantic"] = {"status": "skipped", "detail": "not selected"}
    if options["graphify"]:
        from .code_index import installed as graphify_installed
        config = json.loads((root / "hive.local.json").read_text(encoding="utf-8-sig"))
        if project in config.get("graphify_projects", []) and graphify_installed(root):
            extras["Graphify"] = {"status": "ready", "detail": "already enabled for this project"}
        else:
            print("Preparing optional Graphify code indexing; first setup downloads an isolated environment...", flush=True)
            try:
                result = subprocess.run([str(python), str(root / "hive.py"), "code-setup", project],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
                payload = json.loads(result.stdout) if result.returncode == 0 else {}
                if not isinstance(payload, dict):
                    raise ValueError("Graphify setup returned an unexpected result")
                if result.returncode == 0 and payload.get("status") in {"ready", "empty"}:
                    detail = "enabled; no supported source files yet" if payload["status"] == "empty" else "local code index ready"
                    extras["Graphify"] = {"status": "ready", "detail": detail}
                else:
                    detail = payload.get("reason") or result.stderr.strip() or result.stdout.strip() or "Graphify setup did not confirm readiness"
                    extras["Graphify"] = {"status": "needs-action", "detail": detail[-300:]}
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                extras["Graphify"] = {"status": "needs-action", "detail": str(exc)[-300:]}
    else:
        extras["Graphify"] = {"status": "skipped", "detail": "not selected"}
    return extras


async def smoke_test(root, python, project):
    """Exercise the same stdio MCP command registered with the agent CLIs."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = Path(root).resolve()
    params = StdioServerParameters(command=str(python), args=[str(root / "hive.py"), "serve"])

    async def check():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                needed = {"hive_context", "memory_search", "session_checkpoint"}
                if not needed <= tools:
                    raise ValueError("MCP bridge did not expose required tools")
                result = await client.call_tool("hive_context", {"project": project, "budget_tokens": 512})
                if result.isError:
                    raise ValueError("hive_context returned an MCP error")
                payload = json.loads(result.content[0].text)
                if not isinstance(payload, dict):
                    raise ValueError("hive_context returned an unexpected result")
                return len(tools)

    return await asyncio.wait_for(check(), timeout=30)


def personalize(root, input_fn=input, output=sys.stdout):
    """Save only answers the user explicitly enters; use stable shared keys."""
    from .learning import learning_note
    from .store import Hive

    hive = Hive(root)
    prompts = (
        ("communication-style", "How should agents communicate with you?"),
        ("workflow-style", "What workflow or verification habits should carry across projects?"),
    )
    saved = []
    for key, prompt in prompts:
        try:
            answer = input_fn(f"{prompt} (Enter to skip)\n> ").strip()
        except EOFError:
            break
        if not answer:
            continue
        path, content = learning_note("preference", key, answer, "User answer during optional HiveMind onboarding",
                                      basis="user-stated")
        try:
            current = hive.read_note(path)
            revision = current["revision"]
        except FileNotFoundError:
            revision = "new"
        hive.write_memory(path, content, revision)
        saved.append(key)
    print(f"Saved {len(saved)} explicit shared preference(s).", file=output)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("project")
    args = parser.parse_args()
    print(json.dumps({"tool_count": asyncio.run(smoke_test(args.root, sys.executable, args.project))}))


if __name__ == "__main__":
    main()
