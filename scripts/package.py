"""Portable source/vault bundles; local backups also snapshot SQLite safely."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build_bundle(root, destination, include_state=False):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    names = ("hive.py", "bootstrap.py", "hivemind.cmd", "requirements.txt", "requirements-graphify.txt", "setup.ps1",
             "setup.sh", "install.ps1", "install.sh", "README.md", "AGENTS.md", ".gitignore", ".gitattributes")
    files = [root / name for name in names if (root / name).is_file()]
    folders = ("hivemind", "scripts", "tests", "docs", "examples", "templates")
    for folder in folders + (("vault",) if include_state else ()):
        for path in (root / folder).rglob("*"):
            if not path.is_file() or any(p.startswith(".") or p == "__pycache__" for p in path.relative_to(root).parts):
                continue
            if path.suffix == ".pyc":
                continue
            if not include_state and folder == "vault" and (path.name == "Home.md" or "04-Tasks" in path.parts or "06-Handoffs" in path.parts):
                continue
            files.append(path)
    for path in files:
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Bundle input points outside HiveMind: {path.relative_to(root)}")
    if destination in [p.resolve() for p in files] or destination.is_relative_to(root / "vault"):
        raise ValueError("Save bundles outside the vault and source files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        snapshot = None
        database = root / "runtime/hivemind.db"
        if include_state and database.exists():
            if not database.resolve().is_relative_to(root):
                raise ValueError("Task database points outside HiveMind")
            snapshot = Path(staging) / "hivemind.db"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Task database integrity check failed")
        # Exclusive creation keeps a previous backup intact if the name is reused.
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(files):
                archive.write(path, "HiveMind/" + path.relative_to(root).as_posix())
            if snapshot:
                archive.write(snapshot, "HiveMind/runtime/hivemind.db")
    return {"bundle": str(destination), "files": len(files) + bool(snapshot),
            "task_database": bool(snapshot), "mode": "local" if include_state else "source",
            "excluded": "credentials, device configuration, caches, logs, worktrees, hosted service, Python environment"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build_bundle(ROOT, args.output or ROOT / "dist" / ("HiveMind-local.zip" if args.local else "HiveMind-portable.zip"), args.local))
