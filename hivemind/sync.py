"""Explicit vault import and conflict-aware Obsidian mirroring; no model calls."""
import hashlib
import json
from pathlib import Path
from .store import Hive, atomic_write


async def import_vault(root, cloud):
    hive = Hive(root)
    config_path = Path(root) / "hive.local.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    projects = {x.casefold(): x for x in config.get("projects", {})}
    report = {"imported": 0, "conflicts": [], "skipped": []}
    for path in sorted(hive.vault.rglob("*.md")):
        name = path.relative_to(hive.vault).as_posix()
        if any(p.startswith(".") for p in path.relative_to(hive.vault).parts):
            continue
        hive.note_path(name)  # Includes containment and symlink validation.
        content = path.read_bytes().decode("utf-8")
        if len(content) > 8000:
            report["skipped"].append(name)
            continue
        parts = name.split("/")
        if len(parts) > 2 and parts[0] == "03-Projects":
            parts[1] = projects.get(parts[1].casefold(), parts[1])
            name = "/".join(parts)
        try:
            await cloud.request("memory_write", {"path": name, "content": content, "expected_revision": "new"}, owner=True)
            report["imported"] += 1
        except ValueError as exc:
            if "Note changed" not in str(exc):
                raise
            report["conflicts"].append(name)
    return report


async def mirror(root, cloud, push=False):
    hive = Hive(root)
    state_path = Path(root) / "runtime" / "mirror-revisions.json"
    all_state = json.loads(state_path.read_text()) if state_path.exists() else {}
    state = all_state.setdefault(cloud.endpoint, {})
    report = {"pulled": 0, "pushed": 0, "conflicts": [], "local_only": []}
    remote = {}; after = ""
    while True:
        page = await cloud.request("memory_catalog", {"after": after, "limit": 100})
        remote.update({n["path"]: n for n in page["notes"]})
        after = page["next_cursor"]
        if not after:
            break
    for name, meta in remote.items():
        path = hive.note_path(name)
        local = path.read_bytes() if path.exists() else None
        local_rev = hashlib.sha256(local).hexdigest() if local is not None else None
        baseline = state.get(name)
        if local_rev == meta["revision"]:
            state[name] = local_rev
            continue
        if local is not None and local_rev != baseline:
            if push and baseline and meta["revision"] == baseline:
                try:
                    saved = await cloud.request("memory_write", {"path": name, "content": local.decode("utf-8"), "expected_revision": baseline}, owner=True)
                except ValueError as exc:
                    if "Note changed" not in str(exc):
                        raise
                    report["conflicts"].append(name)
                else:
                    state[name] = saved["revision"]; report["pushed"] += 1
            else:
                report["conflicts"].append(name)
            continue
        note = await cloud.request("note_read", {"path": name, "limit": 8000})
        if note["next_offset"] is not None:
            report["conflicts"].append(name)
            continue
        # Don't overwrite an Obsidian edit that appeared while the network call ran.
        if (path.read_bytes() if path.exists() else None) != local:
            report["conflicts"].append(name)
            continue
        atomic_write(path, note["text"])
        state[name] = note["revision"]; report["pulled"] += 1
    remote_paths = {hive.note_path(name) for name in remote}
    for path in hive.vault.rglob("*.md"):
        name = path.relative_to(hive.vault).as_posix()
        if path.resolve() not in remote_paths and not any(p.startswith(".") for p in path.relative_to(hive.vault).parts):
            report["local_only"].append(name)
    atomic_write(state_path, json.dumps(all_state, indent=2))
    return report
