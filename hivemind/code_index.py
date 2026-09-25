"""Optional, bounded local code graphs; independent of the memory authority."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import venv

from .context import validate_project
from .store import atomic_write
from .transport import connection

VERSION = "0.9.67"
POLICY = 1
SOURCE_ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h",
              ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".lua", ".sh", ".ps1"}
SKIP = {"runtime", "vault", "node_modules", "vendor", "dist", "build", "target", "venv",
        "__pycache__", "graphify-out", "site-packages"}
MAX_FILE = 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 2000
MAX_INDEX = 64 * 1024 * 1024


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def runtime_path(root, relative):
    root = Path(root).resolve()
    path = root / "runtime" / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("Code index runtime path points outside HiveMind")
    return path


def interpreter(root):
    return runtime_path(root, "tools/graphify") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def adapter_environment():
    # Ignore upstream output/backend overrides inherited from a user's shell.
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GRAPHIFY_")}


@contextmanager
def locked(path):
    """OS-owned lock releases even after a killed process; never delete lock files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def installed(root):
    python = interpreter(root)
    if not python.exists():
        return False
    try:
        result = subprocess.run([str(python), "-I", "-X", "utf8", "-c",
            "from importlib.metadata import version; from graphify.extract import extract; "
            "from graphify.serve import _query_graph_text; print(version('graphifyy'))"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            env=adapter_environment())
        if result.returncode:
            atomic_write(runtime_path(root, "graphify-dependency-check.log"), result.stderr[-8000:])
        return result.returncode == 0 and result.stdout.strip() == VERSION
    except (OSError, subprocess.TimeoutExpired) as exc:
        try:
            atomic_write(runtime_path(root, "graphify-dependency-check.log"), type(exc).__name__)
        except OSError:
            pass
        return False


def install(root):
    """Explicit installer only; MCP queries never pip-install dependencies."""
    if installed(root):
        return {"installed": True, "version": VERSION, "changed": False}
    if sys.version_info < (3, 12):
        return {"installed": False, "reason": "Optional Graphify setup requires Python 3.12+; memory is ready."}
    lock = runtime_path(root, "tools/graphify-install.lock")
    log = runtime_path(root, "graphify-install.log")
    try:
        with locked(lock):
            python = interpreter(root)
            if not python.exists():
                venv.EnvBuilder(with_pip=True).create(python.parent.parent)
            with log.open("w", encoding="utf-8") as output:
                result = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "--only-binary=:all:", "-r", str(SOURCE_ROOT / "requirements-graphify.txt")],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, timeout=600)
            if result.returncode or not installed(root):
                return {"installed": False, "reason": "Graphify installation did not pass its check.", "log": str(log)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"installed": False, "reason": type(exc).__name__ + ": Graphify setup unavailable; retry setup.", "log": str(log)}
    return {"installed": True, "version": VERSION, "changed": True}


def project_path(root, project):
    validate_project(project)
    config = connection(root)[2]
    mapped = config.get("projects", {}).get(project)
    if not mapped:
        raise ValueError("Project is not enrolled on this device; run its HiveMind installer")
    path = Path(mapped).resolve()
    if not path.is_dir() or path == Path(path.anchor):
        raise ValueError("Project directory is unavailable on this device")
    return path, config


def allowed(path, root_is_project=False):
    return (path.suffix.lower() in EXTENSIONS and
            not any(part.startswith(".") or part.lower() in SKIP for part in path.parts) and
            not (root_is_project and path.parts[0].lower() == "cloud"))


def sources(root, project):
    """Content fingerprints catch edits, deletions, branch changes and new files."""
    try:
        result = subprocess.run(["git", "-C", str(project), "ls-files", "-c", "-o", "--exclude-standard", "-z", "--", "."],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0:
        names = sorted(set(os.fsdecode(result.stdout).split("\0")) - {""})
        mode = "git-visible source files"
    else:
        names = []
        for directory, dirs, files in os.walk(project, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP
                       and not (Path(directory) / d).is_symlink()
                       and not getattr(Path(directory) / d, "is_junction", lambda: False)()]
            names.extend((Path(directory) / f).relative_to(project).as_posix() for f in files)
        names.sort()
        mode = "source allowlist (no Git ignore rules)"
    files, skipped, total = {}, 0, 0
    digest = hashlib.sha256(f"{VERSION}:{POLICY}:{project}".encode())
    for name in names:
        relative = Path(name)
        if not allowed(relative, Path(root).resolve() == project):
            continue
        path = project / relative
        if (relative.is_absolute() or ".." in relative.parts or path.is_symlink() or
                path.resolve() != path.absolute() or not path.is_file()):
            skipped += 1
            continue
        if path.stat().st_size > MAX_FILE:
            skipped += 1
            continue
        data = path.read_bytes()
        if len(data) > MAX_FILE or b"\x00" in data:
            skipped += 1
            continue
        total += len(data)
        if len(files) >= MAX_FILES or total > MAX_TOTAL:
            raise ValueError("Project exceeds the pilot limit (2000 source files / 20 MiB); use native code search")
        name = relative.as_posix()
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
        files[name] = data
    return files, digest.hexdigest(), skipped, mode


def run_adapter(root, payload):
    result = subprocess.run([str(interpreter(root)), "-I", "-X", "utf8", str(SOURCE_ROOT / "scripts/graphify_bridge.py")],
        input=compact(payload), capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=runtime_path(root, "code-index"), env=adapter_environment())
    if result.returncode:
        log = runtime_path(root, "graphify-last-error.log")
        atomic_write(log, result.stderr[-8000:])
        raise ValueError("Graphify could not complete the operation; see runtime/graphify-last-error.log")
    return json.loads(result.stdout)


def fallback(reason, status="unavailable"):
    return {"status": status, "reason": reason, "fallback": "Use native file/symbol search; HiveMind memory remains available.",
            "model_calls": 0}


def operate(root, project, query="", budget_tokens=1000, force=False, build_only=False):
    """One optional MCP operation; refresh only when the source fingerprint changes."""
    if not isinstance(budget_tokens, int) or not 256 <= budget_tokens <= 2000:
        return fallback("Budget must be 256-2000 estimated tokens", "invalid")
    if not build_only and (not query.strip() or len(query) > 400):
        return fallback("Provide a code question or symbol name of 1-400 characters", "invalid")
    try:
        path, config = project_path(root, project)
        if project not in config.get("graphify_projects", []):
            return fallback("Graphify is not enabled for this project; rerun setup with --with-graphify", "disabled")
        if not installed(root):
            return fallback("Graphify is missing or its version differs; rerun setup with --with-graphify")
        key = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        folder = runtime_path(root, "code-index/" + key)
        folder.mkdir(parents=True, exist_ok=True)
        with locked(folder / "index.lock"):
            files, revision, skipped, selection = sources(root, path)
            if not files:
                return fallback("No supported source files found; memory-only project remains usable", "empty")
            index = folder / "index.json"
            data = {}
            if index.exists() and index.stat().st_size <= MAX_INDEX:
                try:
                    data = json.loads(index.read_text(encoding="utf-8"))
                    if (not isinstance(data, dict) or not isinstance(data.get("graph"), dict)
                            or not isinstance(data["graph"].get("nodes"), list)
                            or not isinstance(data["graph"].get("links"), list)
                            or not isinstance(data.get("nodes"), int)
                            or not isinstance(data.get("edges"), int)):
                        data = {}
                except (ValueError, UnicodeError):
                    data = {}
            refreshed = force or data.get("revision") != revision
            if refreshed:
                # A source-only snapshot prevents resolvers from scanning private notes,
                # symlink targets, node_modules or configuration outside this corpus.
                with tempfile.TemporaryDirectory(prefix="build-", dir=folder) as staging:
                    staging = Path(staging)
                    source = staging / "source"
                    for name, content in files.items():
                        target = source / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                    built = run_adapter(root, {"operation": "build", "source": str(source),
                                              "files": list(files), "cache": str(staging / "cache")})
                data = {**built, "revision": revision, "version": VERSION, "built_at": int(time.time())}
                encoded = compact(data)
                if len(encoded.encode("utf-8")) > MAX_INDEX:
                    raise ValueError("Graph exceeds the 64 MiB pilot limit; use native code search")
                atomic_write(index, encoded)
            result = {"status": "ready", "project": project, "engine": "graphify " + VERSION,
                      "files": len(files), "skipped_source_files": skipped, "selection": selection,
                      "nodes": data["nodes"], "edges": data["edges"], "refreshed": refreshed,
                      "model_calls": 0}
            if build_only:
                return result
            output = run_adapter(root, {"operation": "query", "index": str(index), "query": query,
                                        "budget": budget_tokens})
            result.update({"text": "", "omitted_lines": 0,
                           "note": "Partial static graph; inspect current source before editing. Excluded files are not indexed.",
                           "budget": {"estimated_tokens": 0, "max_bytes": budget_tokens * 4}})
            lines = output["lines"] or ["No matching nodes; try a symbol name or native search."]
            kept = []
            for line in lines:
                result["text"] = "\n".join(kept + [line])
                result["omitted_lines"] = len(lines) - len(kept) - 1
                # Reserve room for the final estimated-token digit count.
                if len(compact(result).encode("utf-8")) + 16 > budget_tokens * 4:
                    break
                kept.append(line)
            result["text"] = "\n".join(kept)
            result["omitted_lines"] = len(lines) - len(kept)
            result["budget"]["estimated_tokens"] = (len(compact(result).encode("utf-8")) + 3) // 4 + 1
            return result
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        # Never return an old graph as current when refresh fails.
        return fallback(str(exc)[:240] if not isinstance(exc, OSError) else
                        "Code index is busy or inaccessible; retry later or use native search")


def enable(root, project):
    project_path(root, project)
    setup = install(root)
    if not setup["installed"]:
        return fallback(setup["reason"] + (" Log: " + setup["log"] if setup.get("log") else ""))
    # Record only on this device. Rebuild on a new device using the same one-line flag.
    config = connection(root)[2]
    config["graphify_projects"] = sorted(set(config.get("graphify_projects", [])) | {project})
    atomic_write(Path(root) / "hive.local.json", json.dumps(config, indent=2) + "\n")
    return operate(root, project, build_only=True)
