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
    parser.add_argument("--configure", action="store_true", help="Open the optional-feature menu again on this device")
    parser.add_argument("--no-prompt", action="store_true", help="Use saved defaults, or core-only on a fresh noninteractive setup")
    parser.add_argument("--personalize", action="store_true", help="Optionally save two explicit cross-project working preferences")
    parser.add_argument("--with-graphify", action="store_true", help="Install and enable optional local code graphs (Python 3.12+)")
    parser.add_argument("--with-semantic", action="store_true", help="Install local semantic memory search (one-time model download)")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise ValueError("Install Python 3.11 or newer, then run this command again")
    config = ROOT / "hive.local.json"
    first_setup = not config.exists()
    existing = json.loads(config.read_text(encoding="utf-8-sig")) if not first_setup else {}
    from hivemind.onboarding import choose_setup
    options = choose_setup(args, existing, first_setup)
    if options["personalize"] and not args.dry_run and not sys.stdin.isatty():
        raise ValueError("Personalization needs an interactive terminal; rerun without --personalize in automation")
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
    if not args.dry_run:
        from hivemind.seed import seed_vault
        seed_vault(ROOT)
    if not config.exists() and not args.dry_run:
        # An inherited cloud URL cannot silently turn a fresh local bundle into a hosted client.
        with config.open("x", encoding="utf-8") as f:
            json.dump({"offline": True}, f)
        subprocess.run([str(target), str(ROOT / "hive.py"), "init"], check=True, capture_output=True)
    if options["persist"] and not args.dry_run:
        from hivemind.store import atomic_write
        latest = json.loads(config.read_text(encoding="utf-8-sig"))
        latest["onboarding_defaults"] = {key: options[key] for key in ("graphify", "semantic")}
        atomic_write(config, json.dumps(latest, indent=2) + "\n")
    command = [str(target), str(ROOT / "hive.py"), "attach", str(args.project.resolve())]
    if args.name:
        command += ["--name", args.name]
    # The direct attach command stays strict. Onboarding enrolls first so one broken
    # client registration cannot prevent the other clients or project setup.
    command.append("--skip-register")
    if args.dry_run:
        command.append("--dry-run")
    attached = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if attached.returncode:
        raise ValueError(attached.stderr.strip() or attached.stdout.strip() or "project enrollment failed")
    enrollment = json.loads(attached.stdout)
    project_id = enrollment["project"]
    if args.dry_run:
        print(f"HiveMind preview for {project_id}: {', '.join(enrollment['files_to_change']) or 'no project file changes'}")
        if options["graphify"]:
            print("Preview: would install isolated Graphify and build a local source-only index.")
        if options["semantic"]:
            print("Preview: would install the local semantic model; Markdown stays authoritative.")
        print("No agent registration, MCP probe, profile prompt or model call was run.")
        return 0
    from hivemind.onboarding import setup_extras
    extras = setup_extras(ROOT, target, project_id, options)
    if args.skip_register:
        agents = {name: {"status": "skipped", "detail": "requested with --skip-register"}
                  for name in ("codex", "grok", "antigravity")}
    else:
        registration = subprocess.run([str(target), str(ROOT / "scripts/register_agents.py"), "--json"],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if not registration.stdout.strip():
            raise ValueError("Agent registration did not return a report: " + registration.stderr.strip())
        agents = json.loads(registration.stdout)
    try:
        probe = subprocess.run([str(target), "-m", "hivemind.onboarding", str(ROOT), project_id],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    except subprocess.TimeoutExpired:
        bridge = {"status": "needs-action", "detail": "MCP probe timed out after 45 seconds"}
    else:
        if probe.returncode:
            bridge = {"status": "needs-action", "detail": "MCP probe failed: " + (probe.stderr.strip() or probe.stdout.strip())[-300:]}
        else:
            count = json.loads(probe.stdout)["tool_count"]
            bridge = {"status": "ready", "detail": f"hive_context succeeded; {count} MCP tools visible"}
    if options["personalize"]:
        from hivemind.onboarding import personalize
        personalize(ROOT)
    print(f"\nHiveMind onboarding - {project_id}")
    print(f"  Project: ready ({len(enrollment['changed_files'])} file changes)")
    for name, result in agents.items():
        print(f"  {name}: {result['status']} ({result['detail']})")
    for name, result in extras.items():
        print(f"  {name}: {result['status']} ({result['detail']})")
    if not args.skip_register and all(result["status"] == "skipped" for result in agents.values()):
        print("  Agents: needs-action (install at least one supported CLI, then rerun setup)")
    print(f"  MCP bridge: {bridge['status']} ({bridge['detail']})")
    print(f"  Vault: {ROOT / 'vault'}")
    if not args.skip_register:
        print("  Next: restart active agent sessions in this project; accept normal trust/MCP prompts.")
    if ((not args.skip_register and all(result["status"] == "skipped" for result in agents.values()))
            or any(result["status"] == "needs-action" for result in agents.values())
            or any(result["status"] == "needs-action" for result in extras.values()) or bridge["status"] != "ready"):
        print("  Rerun the same command after resolving the needs-action items; enrollment is safe to repeat.")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print("HiveMind setup: " + str(exc), file=sys.stderr)
        sys.exit(1)
