"""One-command local installation using Python's standard library; no hosted service."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Set up this project's shared local HiveMind folder")
    parser.add_argument("project", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--name", default="")
    parser.add_argument("--skip-register", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--with-graphify", action="store_true", help="Install and enable optional local code graphs (Python 3.12+)")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise ValueError("Install Python 3.11 or newer, then run this command again")
    target = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not target.exists():
        if args.dry_run:
            raise ValueError("A preview needs the initial Python environment; run setup once first")
        print("Preparing the local Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    ready = subprocess.run([str(target), "-c", "import mcp; import pydantic"], capture_output=True)
    if ready.returncode:
        if args.dry_run:
            raise ValueError("Dependencies are missing; run setup once before previewing")
        print("Installing the local bridge dependencies (one-time internet access)...", flush=True)
        subprocess.run([str(target), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    config = ROOT / "hive.local.json"
    if not args.dry_run:
        from hivemind.seed import seed_vault
        seed_vault(ROOT)
    if not config.exists() and not args.dry_run:
        # An inherited cloud URL cannot silently turn a fresh local bundle into a hosted client.
        with config.open("x", encoding="utf-8") as f:
            json.dump({"offline": True}, f)
        subprocess.run([str(target), str(ROOT / "hive.py"), "init"], check=True)
    command = [str(target), str(ROOT / "hive.py"), "attach", str(args.project.resolve())]
    if args.name:
        command += ["--name", args.name]
    if args.skip_register:
        command.append("--skip-register")
    if args.dry_run:
        command.append("--dry-run")
    subprocess.run(command, check=True)
    if args.with_graphify:
        if args.dry_run:
            print("Preview: would install isolated Graphify and build a local source-only index.")
        else:
            print("Preparing optional Graphify code indexing; first setup downloads an isolated environment...", flush=True)
            manifest = args.project.resolve() / ".hivemind/project.json"
            project_id = json.loads(manifest.read_text(encoding="utf-8"))["project"]
            subprocess.run([str(target), str(ROOT / "hive.py"), "code-setup", project_id], check=True)
    print(f"Shared HiveMind folder: {ROOT}\nObsidian vault: {ROOT / 'vault'}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("HiveMind setup: " + str(exc), file=sys.stderr)
        sys.exit(1)
