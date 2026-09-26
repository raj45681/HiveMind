"""Opt-in, bounded worktree recovery snapshots outside the vault and Git."""
import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .context import validate_project
from .sessions import git_snapshot, validate_id
from .store import utc


MAX_FILES = 2000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_RAW_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_STORE_BYTES = 200 * 1024 * 1024
MAX_UNDO_STORE_BYTES = 50 * 1024 * 1024
SNAP_ID = re.compile(r"SESSION-[a-f0-9]{16}-r(?:0|[1-9][0-9]*)\Z")
UNDO_ID = re.compile(r"UNDO-[a-f0-9]{16}\Z")


def enabled(root, project):
    config_path = Path(root) / "hive.local.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    return project in config.get("recovery_projects", [])


def _store(hive):
    path = (hive.runtime / "recovery").resolve()
    if not path.is_relative_to(hive.root):
        raise ValueError("Recovery storage must stay inside the HiveMind runtime")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name):
    if not isinstance(name, str):
        raise ValueError("Unsafe worktree path")
    name = name.replace("\\", "/")
    if not name or ":" in name or "\0" in name:
        raise ValueError("Unsafe worktree path")
    parts = PurePosixPath(name).parts
    if (not parts or PurePosixPath(name).is_absolute() or any(part in {"", ".", ".."} for part in parts)
            or parts[0] == ".git"):
        raise ValueError("Unsafe worktree path")
    return "/".join(parts)


def _live_path(workspace, name):
    name = _safe_relative(name)
    target = workspace.joinpath(*PurePosixPath(name).parts)
    if not target.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("Worktree path escapes the repository")
    current = target
    while current != workspace:
        if current.is_symlink():
            raise ValueError("Symlink paths cannot be snapshotted or restored")
        current = current.parent
    return target


def _sensitive(name):
    parts = [part.lower() for part in PurePosixPath(name).parts]
    leaf = parts[-1]
    if name.lower() in {"hive.local.json", ".hivemind/local.json"}:
        return True
    if leaf.startswith(".env") or leaf in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}:
        return True
    if Path(leaf).suffix in {".pem", ".key", ".p12", ".pfx", ".kdbx"}:
        return True
    return any(any(word in part for word in ("secret", "credential", "password", "private-key", "access-token")) for part in parts)


def _git_files(workspace):
    result = subprocess.run(["git", "-C", str(workspace), "ls-files", "-z", "--cached",
                             "--others", "--exclude-standard"], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=20, check=True)
    return sorted(set(name for name in result.stdout.decode("utf-8").split("\0") if name))


def _archive_path(hive, ident):
    if not SNAP_ID.fullmatch(ident):
        raise ValueError("Invalid recovery snapshot ID")
    return _store(hive) / (ident + ".zip")


def _undo_path(hive, ident):
    if not UNDO_ID.fullmatch(ident):
        raise ValueError("Invalid recovery undo ID")
    directory = _store(hive) / "undo"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (ident + ".bin")


def _undo_store_bytes(hive):
    directory = _store(hive) / "undo"
    return sum(path.stat().st_size for path in directory.glob("UNDO-*.bin")) if directory.exists() else 0


def _atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def capture(hive, project, session, revision, observed):
    """Capture one Git-visible milestone; failure never pretends to save a snapshot."""
    if not enabled(hive.root, project):
        return None
    validate_id(session)
    ident = f"{session}-r{revision}"
    with hive.connect() as c:
        prior = c.execute("SELECT * FROM recovery_snapshots WHERE id=?", (ident,)).fetchone()
    if prior:
        return {"id": ident, "saved": True, "existing": True, "files": prior["file_count"]}
    if not observed.get("available") or not observed.get("fingerprint_complete"):
        return {"id": ident, "saved": False, "reason": observed.get("reason", "Git state is unverifiable")}
    workspace = Path(observed["workspace"]).resolve()
    store = _store(hive)
    temp = store / (ident + "." + uuid.uuid4().hex + ".tmp")
    files, excluded, raw_bytes = {}, [], 0
    try:
        names = _git_files(workspace)
        if len(names) > MAX_FILES:
            return {"id": ident, "saved": False, "reason": f"More than {MAX_FILES} Git-visible files"}
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                try:
                    if "\\" in name:
                        raise ValueError("Backslash in Git path")
                    _safe_relative(name)
                    if _sensitive(name):
                        excluded.append(name)
                        continue
                    path = _live_path(workspace, name)
                    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                        excluded.append(name)
                        continue
                    data = path.read_bytes()
                    if len(data) > MAX_FILE_BYTES:
                        excluded.append(name)
                        continue
                except (OSError, ValueError):
                    excluded.append(name)
                    continue
                raw_bytes += len(data)
                if raw_bytes > MAX_RAW_BYTES:
                    return {"id": ident, "saved": False, "reason": "Recovery snapshot exceeds the 20 MiB raw-data limit"}
                archive.writestr("files/" + name, data)
                files[name] = {"sha256": _digest(data), "bytes": len(data),
                               "mode": stat.S_IMODE(path.stat().st_mode)}
            manifest = {"id": ident, "session": session, "revision": revision,
                        "project": project, "workspace": str(workspace),
                        "git_dir": observed["git_dir"], "head": observed["head"],
                        "branch": observed["branch"], "created": utc(),
                        "files": files, "excluded": excluded}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        after = git_snapshot(hive.root, project, workspace=workspace)
        if not after.get("available") or any(after.get(key) != observed.get(key) for key in
                                             ("head", "branch", "git_dir", "fingerprint")):
            return {"id": ident, "saved": False, "reason": "Worktree changed while the recovery snapshot was captured"}
        for name, meta in files.items():
            path = _live_path(workspace, name)
            if not path.is_file() or _file_digest(path) != meta["sha256"]:
                return {"id": ident, "saved": False, "reason": "A file changed while the recovery snapshot was captured"}
        size = temp.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            return {"id": ident, "saved": False, "reason": "Recovery archive exceeds the 25 MiB limit"}
        with hive.connect(write=True) as c:
            existing = c.execute("SELECT file_count FROM recovery_snapshots WHERE id=?", (ident,)).fetchone()
            if existing:
                return {"id": ident, "saved": True, "existing": True, "files": existing["file_count"]}
            used = c.execute("SELECT coalesce(sum(archive_bytes),0) FROM recovery_snapshots").fetchone()[0]
            if used + size > MAX_STORE_BYTES:
                return {"id": ident, "saved": False, "reason": "Recovery store reached its 200 MiB limit; prune old snapshots explicitly"}
            destination = _archive_path(hive, ident)
            if destination.exists():
                raise ValueError("Recovery archive exists without a database record; inspect it before retrying")
            os.replace(temp, destination)
            try:
                c.execute("INSERT INTO recovery_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (ident, session, revision, project, str(workspace), observed["git_dir"],
                           observed["head"], observed["branch"], manifest["created"],
                           _file_digest(destination), size, len(files), len(excluded)))
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        return {"id": ident, "saved": True, "files": len(files), "excluded": len(excluded),
                "archive_bytes": size, "partial": bool(excluded)}
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        return {"id": ident, "saved": False, "reason": str(exc)[:250]}
    finally:
        temp.unlink(missing_ok=True)


def list_snapshots(hive, project="", limit=25):
    validate_project(project)
    cap = max(1, min(limit, 100))
    with hive.connect() as c:
        rows = c.execute("""SELECT id,session,revision,project,workspace,head,created,
            archive_bytes,file_count,excluded_count FROM recovery_snapshots
            WHERE (?='' OR project=? COLLATE NOCASE) ORDER BY created DESC,rowid DESC LIMIT ?""",
            (project, project, cap + 1)).fetchall()
        used = c.execute("SELECT coalesce(sum(archive_bytes),0) FROM recovery_snapshots").fetchone()[0]
    return {"project": project or None, "enabled": enabled(hive.root, project) if project else None,
            "snapshots": [dict(row) for row in rows[:cap]], "truncated": len(rows) > cap,
            "store_bytes": used, "store_limit_bytes": MAX_STORE_BYTES}


def _load(hive, ident):
    archive_path = _archive_path(hive, ident)
    with hive.connect() as c:
        row = c.execute("SELECT * FROM recovery_snapshots WHERE id=?", (ident,)).fetchone()
    if row is None or not archive_path.is_file():
        raise ValueError("Recovery snapshot not found or archive missing")
    if _file_digest(archive_path) != row["archive_sha"]:
        raise ValueError("Recovery archive changed; refusing to trust it")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    if any(manifest.get(key) != row[key] for key in ("id", "project", "workspace", "head", "git_dir")):
        raise ValueError("Recovery manifest does not match its record")
    return dict(row), manifest, archive_path


def _current(workspace, name):
    target = _live_path(workspace, name)
    if not target.exists():
        return target, "missing", None
    if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Current file is not a regular file within the 2 MiB limit")
    data = target.read_bytes()
    return target, _digest(data), data


def _live_identity(hive, row):
    current = git_snapshot(hive.root, row["project"], workspace=row["workspace"])
    if not current.get("available"):
        return False, current.get("reason", "Worktree unavailable")
    if any(current.get(key) != row[key] for key in ("git_dir", "head", "branch")):
        return False, "Worktree identity, HEAD or branch changed since capture"
    return True, "Worktree identity and HEAD match"


def preview(hive, ident, path="", limit=100):
    row, manifest, archive_path = _load(hive, ident)
    workspace = Path(row["workspace"])
    allowed, reason = _live_identity(hive, row)
    if path:
        path = _safe_relative(path)
        if path not in manifest["files"]:
            raise ValueError("File was not captured in this recovery snapshot")
        target, current_sha, current = _current(workspace, path)
        saved_meta = manifest["files"][path]
        with zipfile.ZipFile(archive_path) as archive:
            saved = archive.read("files/" + path)
        if _digest(saved) != saved_meta["sha256"]:
            raise ValueError("Captured file hash does not match its manifest")
        diff = ""
        if len(saved) <= 200_000 and (current is None or len(current) <= 200_000):
            try:
                before = saved.decode("utf-8").splitlines(keepends=True)
                after = (current or b"").decode("utf-8").splitlines(keepends=True)
                diff = "".join(difflib.unified_diff(before, after, fromfile="snapshot/" + path,
                                                     tofile="current/" + path))[:12000]
            except UnicodeDecodeError:
                pass
        return {"snapshot": ident, "path": path, "saved_sha256": saved_meta["sha256"],
                "current_sha256": current_sha, "changed": current_sha != saved_meta["sha256"],
                "diff": diff, "diff_omitted": not diff and current_sha != saved_meta["sha256"],
                "restore_allowed": allowed, "reason": reason}
    entries = []
    for name, meta in sorted(manifest["files"].items()):
        try:
            _, current_sha, _ = _current(workspace, name)
            status = "unchanged" if current_sha == meta["sha256"] else "changed"
        except (OSError, ValueError):
            current_sha, status = None, "unsafe_or_oversized"
        if status != "unchanged":
            entries.append({"path": name, "status": status, "saved_sha256": meta["sha256"],
                            "current_sha256": current_sha})
    cap = max(1, min(limit, 100))
    return {"snapshot": ident, "project": row["project"], "workspace": row["workspace"],
            "captured_files": row["file_count"], "excluded_files": row["excluded_count"],
            "changed_count": len(entries), "changed": entries[:cap], "truncated": len(entries) > cap,
            "restore_allowed": allowed, "reason": reason,
            "scope": "Captured files only; Git index, new files, ignored files and external state are untouched"}


def restore_file(hive, ident, path, expected_current):
    row, manifest, archive_path = _load(hive, ident)
    path = _safe_relative(path)
    if path not in manifest["files"]:
        raise ValueError("File was not captured in this recovery snapshot")
    allowed, reason = _live_identity(hive, row)
    if not allowed:
        raise ValueError(reason)
    workspace = Path(row["workspace"])
    target, current_sha, prior = _current(workspace, path)
    if current_sha != expected_current:
        raise ValueError("File changed since preview; inspect it again before restoring")
    saved_meta = manifest["files"][path]
    with zipfile.ZipFile(archive_path) as archive:
        saved = archive.read("files/" + path)
    if _digest(saved) != saved_meta["sha256"]:
        raise ValueError("Captured file hash does not match its manifest")
    if current_sha == saved_meta["sha256"]:
        return {"path": path, "restored": False, "reason": "File already matches the snapshot"}
    prior_mode = stat.S_IMODE(target.stat().st_mode) if prior is not None else None
    undo_id = "UNDO-" + uuid.uuid4().hex[:16]
    undo_path = _undo_path(hive, undo_id)
    if _undo_store_bytes(hive) + len(prior or b"") > MAX_UNDO_STORE_BYTES:
        raise ValueError("Undo store reached its 50 MiB limit; prune old undo records explicitly")
    wrote = False
    try:
        _atomic_bytes(undo_path, prior or b"")
        _, checked_sha, _ = _current(workspace, path)
        if checked_sha != expected_current:
            raise ValueError("File changed since preview; inspect it again before restoring")
        _atomic_bytes(target, saved)
        wrote = True
        if os.name != "nt":
            target.chmod(saved_meta["mode"])
        with hive.connect(write=True) as c:
            c.execute("""INSERT INTO recovery_undos
                (id,snapshot,project,workspace,git_dir,head,branch,path,prior_sha,prior_mode,
                 restored_sha,created,undone) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                      (undo_id, ident, row["project"], row["workspace"], row["git_dir"], row["head"],
                       row["branch"], path, current_sha, prior_mode, saved_meta["sha256"], utc()))
    except BaseException:
        if wrote and target.is_file() and _file_digest(target) == saved_meta["sha256"]:
            if prior is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_bytes(target, prior)
                if os.name != "nt":
                    target.chmod(prior_mode)
        undo_path.unlink(missing_ok=True)
        raise
    return {"snapshot": ident, "path": path, "restored": True,
            "revision": saved_meta["sha256"], "undo_id": undo_id,
            "note": "Only this worktree file changed; Git HEAD and index were not modified"}


def undo_restore(hive, ident, expected_current):
    if not UNDO_ID.fullmatch(ident):
        raise ValueError("Invalid recovery undo ID")
    with hive.connect() as c:
        row = c.execute("SELECT * FROM recovery_undos WHERE id=?", (ident,)).fetchone()
    if row is None or row["undone"]:
        raise ValueError("Undo record not found or already used")
    allowed, reason = _live_identity(hive, row)
    if not allowed:
        raise ValueError(reason)
    workspace = Path(row["workspace"])
    target, current_sha, _ = _current(workspace, row["path"])
    if current_sha != expected_current or current_sha != row["restored_sha"]:
        raise ValueError("File changed after restore; inspect before undoing")
    prior = _undo_path(hive, ident).read_bytes()
    if row["prior_sha"] != "missing" and _digest(prior) != row["prior_sha"]:
        raise ValueError("Undo data changed; refusing to trust it")
    with hive.connect(write=True) as c:
        latest = c.execute("SELECT undone FROM recovery_undos WHERE id=?", (ident,)).fetchone()
        if latest is None or latest["undone"]:
            raise ValueError("Undo already used")
        _, checked_sha, _ = _current(workspace, row["path"])
        if checked_sha != row["restored_sha"]:
            raise ValueError("File changed after restore; inspect before undoing")
        if row["prior_sha"] == "missing":
            target.unlink()
        else:
            _atomic_bytes(target, prior)
            if os.name != "nt" and row["prior_mode"] is not None:
                target.chmod(row["prior_mode"])
        c.execute("UPDATE recovery_undos SET undone=1 WHERE id=?", (ident,))
    return {"undo_id": ident, "path": row["path"], "undone": True,
            "current_sha256": row["prior_sha"]}


def prune(hive, project, keep=10, drop_undos=False):
    validate_project(project, required=True)
    if not 1 <= keep <= 100:
        raise ValueError("Keep must be 1-100 snapshots")
    moved = []
    try:
        with hive.connect(write=True) as c:
            rows = c.execute("SELECT id,archive_bytes FROM recovery_snapshots WHERE project=? COLLATE NOCASE "
                             "ORDER BY created DESC,rowid DESC", (project,)).fetchall()
            old = rows[keep:]
            for row in old:
                path = _archive_path(hive, row["id"])
                if path.exists():
                    staged = path.with_name(path.name + ".prune-" + uuid.uuid4().hex)
                    os.replace(path, staged)
                    moved.append((path, staged))
                c.execute("DELETE FROM recovery_snapshots WHERE id=?", (row["id"],))
            undos = []
            if drop_undos:
                undos = c.execute("SELECT id FROM recovery_undos WHERE project=? COLLATE NOCASE",
                                  (project,)).fetchall()
                for row in undos:
                    path = _undo_path(hive, row["id"])
                    if path.exists():
                        staged = path.with_name(path.name + ".prune-" + uuid.uuid4().hex)
                        os.replace(path, staged)
                        moved.append((path, staged))
                c.execute("DELETE FROM recovery_undos WHERE project=? COLLATE NOCASE", (project,))
    except BaseException:
        for path, staged in reversed(moved):
            if staged.exists():
                os.replace(staged, path)
        raise
    for _, staged in moved:
        staged.unlink(missing_ok=True)
    return {"project": project, "removed": len(old), "freed_bytes": sum(row["archive_bytes"] for row in old),
            "kept": min(len(rows), keep), "undo_records_removed": len(undos),
            "note": "Undo records are kept unless --drop-undos is set"}
