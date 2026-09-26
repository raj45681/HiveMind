"""Register one local bridge per installed CLI without coupling their results."""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = {"codex": "codex", "grok": "grok", "antigravity": "agy"}


def config_locations(home):
    return {
        "codex": [Path(os.environ.get("CODEX_HOME", str(home / ".codex"))) / "config.toml"],
        "grok": [home / ".grok" / "config.toml"],
        "antigravity": [home / ".gemini" / "config" / "mcp_config.json",
                        home / ".gemini" / "antigravity" / "mcp_config.json",
                        home / ".gemini" / "antigravity-cli" / "mcp_config.json"],
    }


def register_agents(root=ROOT, python=sys.executable):
    root = Path(root).resolve()
    backup = root / "runtime" / "config-backups" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    locations = config_locations(Path.home())
    results = {}
    for name, exe_name in AGENTS.items():
        exe = shutil.which(exe_name)
        if not exe:
            results[name] = {"status": "skipped", "detail": "CLI not on PATH"}
            continue
        try:
            for index, path in enumerate(locations[name]):
                if path.exists():
                    backup.mkdir(parents=True, exist_ok=True)
                    destination = backup / f"{name}-{index}{path.suffix}"
                    shutil.copy2(path, destination)
                    destination.chmod(0o600)
            args = [exe, "mcp", "add", "hivemind"]
            if name != "antigravity":
                args.append("--")
            args += [str(python), str(root / "hive.py"), "serve"]
            added = subprocess.run(args, capture_output=True, text=True, timeout=30)
            if added.returncode:
                detail = added.stderr.strip() or added.stdout.strip() or f"exit {added.returncode}"
                results[name] = {"status": "needs-action", "detail": f"registration failed: {detail[:500]}"}
                continue
            check = [exe, "mcp", "get", "hivemind"] if name == "codex" else [exe, "mcp", "list"]
            listed = subprocess.run(check, capture_output=True, text=True, timeout=30)
            if listed.returncode or "hivemind" not in listed.stdout.lower():
                results[name] = {"status": "needs-action", "detail": "CLI did not confirm hivemind; run its MCP list command"}
            else:
                results[name] = {"status": "ready", "detail": "registered and listed; restart active sessions"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[name] = {"status": "needs-action", "detail": str(exc)[:500]}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable per-agent results")
    args = parser.parse_args()
    results = register_agents()
    if args.json:
        print(json.dumps(results))
    else:
        for name, result in results.items():
            print(f"{name}: {result['status']} ({result['detail']})")
        print("Restart existing agent sessions to load the bridge. Other MCP servers are preserved.")
    return int(any(result["status"] == "needs-action" for result in results.values()))


if __name__ == "__main__":
    sys.exit(main())
