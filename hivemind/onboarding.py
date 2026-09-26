"""Small, model-free checks and optional profile capture for project onboarding."""
import asyncio
import argparse
import json
import sys
from pathlib import Path


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
        answer = input_fn(f"{prompt} (Enter to skip)\n> ").strip()
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
