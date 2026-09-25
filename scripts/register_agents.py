"""Register one local bridge in installed CLIs; preserve existing configs in local backups."""
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    backup = ROOT / "runtime" / "config-backups" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    home = Path.home()
    locations = {
        "codex": Path(os.environ.get("CODEX_HOME", str(home / ".codex"))) / "config.toml",
        "grok": home / ".grok" / "config.toml",
        "antigravity": home / ".gemini" / "config" / "mcp_config.json",
        "antigravity-legacy": home / ".gemini" / "antigravity" / "mcp_config.json",
        "antigravity-cli": home / ".gemini" / "antigravity-cli" / "mcp_config.json",
    }
    for name, path in locations.items():
        if path.exists():
            backup.mkdir(parents=True, exist_ok=True)
            destination = backup / (name + path.suffix)
            shutil.copy2(path, destination)
            destination.chmod(0o600)
    for name, exe_name in {"codex": "codex", "grok": "grok", "antigravity": "agy"}.items():
        exe = shutil.which(exe_name)
        if not exe:
            print(f"{name}: skipped (CLI not installed); rerun this script after installing it")
            continue
        args = [exe, "mcp", "add", "hivemind"]
        if name != "antigravity":
            args.append("--")
        args += [sys.executable, str(ROOT / "hive.py"), "serve"]
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode:
            print(f"{name}: registration failed: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(result.returncode)
        print(f"{name}: HiveMind bridge registered")
    print("Restart existing agent sessions to load the bridge. Other MCP servers are preserved.")


if __name__ == "__main__":
    main()
