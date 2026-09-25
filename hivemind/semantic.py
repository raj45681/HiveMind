"""Optional, local semantic retrieval over disposable Markdown-derived vectors."""
from array import array
import json
import math
import os
from pathlib import Path
import re
import subprocess

MODEL = "BAAI/bge-small-en-v1.5"
VERSION = "fastembed==0.8.1"
INDEX_VERSION = MODEL + ":chunks-v3"


def interpreter(root):
    return Path(root) / "runtime" / "tools" / "semantic" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ready(root):
    marker = Path(root) / "runtime" / "tools" / "semantic-ready.json"
    try:
        return interpreter(root).is_file() and json.loads(marker.read_text()) == {"model": MODEL, "version": VERSION}
    except (OSError, ValueError):
        return False


def _embed(root, texts, download=False):
    if not texts:
        return []
    worker = Path(__file__).with_name("semantic_worker.py")
    cache = Path(root) / "runtime" / "models" / "semantic"
    cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if not download:
        env["HF_HUB_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    result = subprocess.run(
        [str(interpreter(root)), str(worker)],
        input=json.dumps({"cache": str(cache), "texts": texts, "download": download}),
        text=True, capture_output=True, timeout=180 if download else 60, env=env,
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-1200:] or "Local embedding process failed")
    vectors = json.loads(result.stdout)
    if len(vectors) != len(texts) or any(len(v) != 384 for v in vectors):
        raise RuntimeError("Local model returned unexpected vectors")
    return vectors


def setup(root):
    """Explicitly install/download once; later retrieval is strictly cache-only."""
    root = Path(root)
    python = interpreter(root)
    if not python.exists():
        import venv
        venv.EnvBuilder(with_pip=True).create(python.parent.parent)
    check = subprocess.run([str(python), "-c", "import fastembed; assert fastembed.__version__ == '0.8.1'"], capture_output=True)
    if check.returncode:
        subprocess.run([str(python), "-m", "pip", "install", VERSION], check=True)
    _embed(root, ["local semantic memory"], download=True)
    marker = root / "runtime" / "tools" / "semantic-ready.json"
    marker.write_text(json.dumps({"model": MODEL, "version": VERSION}), encoding="utf-8")
    return {"installed": True, "model": MODEL, "scope": "local only", "note": "Markdown remains authoritative; the vector index rebuilds automatically."}


def chunks(title, content):
    """Split prose into bounded overlapping-free passages; preserve exact source text."""
    content = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", content, flags=re.S)
    content = re.sub(r"```.*?```", "", content, flags=re.S)
    content = re.sub(r"^#{1,6}[^\n]*(?:\n|$)", "", content, flags=re.M)
    parts = []
    for block in re.split(r"\n\s*\n", content):
        block = " ".join(block.split())
        while block:
            if len(block) <= 900:
                parts.append(block)
                break
            cut = block.rfind(" ", 0, 900)
            cut = cut if cut >= 450 else 900
            parts.append(block[:cut])
            block = block[cut:].strip()
    result, current = [], ""
    for part in parts:
        if len(current) + len(part) + 1 > 900 and current:
            result.append(current)
            current = ""
        current = (current + " " + part).strip()
    if current:
        result.append(current)
    return [f"{title}. {part}" for part in result[:48]]


def _schema(c):
    c.execute("CREATE TABLE IF NOT EXISTS semantic_notes(path TEXT PRIMARY KEY, revision TEXT NOT NULL, model TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS semantic_chunks(path TEXT NOT NULL, chunk INTEGER NOT NULL, content TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(path,chunk))")


def _eligible(path, project, archive):
    if path.startswith("01-Memory/Candidates/") or (not archive and path.startswith("99-Archive/")):
        return False
    if project and path.lower().startswith("03-projects/"):
        return path.lower().startswith(f"03-projects/{project.lower()}/")
    return True


def recall(hive, query, project="", archive=False, limit=80):
    """Refresh changed notes and rank locally. Failure leaves keyword search usable."""
    if not ready(hive.root):
        return []
    with hive.connect() as c:
        _schema(c)
        notes = {r["path"]: r for r in c.execute("SELECT path,title,content,revision FROM notes")
                 if r["path"].startswith(("01-Memory/", "02-Decisions/", "03-Projects/", "99-Archive/"))
                 and not r["path"].startswith("01-Memory/Candidates/")
                 and "/Sessions/" not in r["path"]}
        prior = {r["path"]: r["revision"] for r in c.execute("SELECT path,revision,model FROM semantic_notes WHERE model=?", (INDEX_VERSION,))}
    changed = [row for path, row in notes.items() if prior.get(path) != row["revision"]]
    prepared = [(row, chunks(row["title"], row["content"])) for row in changed]
    texts = [text for _, passages in prepared for text in passages]
    vectors = _embed(hive.root, texts + [query])
    query_vector = vectors.pop()
    with hive.connect(write=True) as c:
        _schema(c)
        for path in set(prior) - set(notes):
            c.execute("DELETE FROM semantic_chunks WHERE path=?", (path,))
            c.execute("DELETE FROM semantic_notes WHERE path=?", (path,))
        offset = 0
        for row, passages in prepared:
            path = row["path"]
            c.execute("DELETE FROM semantic_chunks WHERE path=?", (path,))
            for index, passage in enumerate(passages):
                c.execute("INSERT INTO semantic_chunks VALUES (?,?,?,?)", (path, index, passage, array("f", vectors[offset]).tobytes()))
                offset += 1
            c.execute("INSERT OR REPLACE INTO semantic_notes VALUES (?,?,?)", (path, row["revision"], INDEX_VERSION))
        rows = c.execute("SELECT s.path,s.content,s.vector,n.revision FROM semantic_chunks s JOIN semantic_notes n ON n.path=s.path WHERE n.model=?", (INDEX_VERSION,)).fetchall()
    qnorm = math.sqrt(sum(x*x for x in query_vector)) or 1
    best = {}
    for row in rows:
        path = row["path"]
        if path not in notes or notes[path]["revision"] != row["revision"] or not _eligible(path, project, archive):
            continue
        vector = array("f")
        vector.frombytes(row["vector"])
        norm = math.sqrt(sum(x*x for x in vector)) or 1
        score = sum(a*b for a,b in zip(query_vector, vector)) / (qnorm * norm)
        if score > best.get(path, (-1, ""))[0]:
            best[path] = (score, row["content"])
    return sorted(((score, path, content) for path,(score,content) in best.items()), reverse=True)[:limit]
