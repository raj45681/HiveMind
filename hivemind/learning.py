"""Structured scoped learning without additional model calls."""
import re
from .store import utc


def learning_note(kind, key, summary, source, project="", evidence="", basis="observation"):
    if kind not in {"preference", "solution", "decision"} or basis not in {"user-stated", "verified-result", "observation"}:
        raise ValueError("Invalid memory kind or basis")
    if not re.fullmatch(r"[a-z0-9_-]{1,80}", key):
        raise ValueError("Use a stable lowercase memory key with letters, digits, underscores or hyphens")
    if project and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", project):
        raise ValueError("Invalid project ID")
    for value, limit in ((summary, 3000), (source, 500), (evidence, 1500)):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError("Keep learning concise: summary 3000, source 500, evidence 1500 characters")
    if not summary.strip() or not source.strip():
        raise ValueError("A useful summary and traceable source are required")
    if kind == "preference":
        if basis != "user-stated":
            directory = "01-Memory/Candidates"
        else:
            directory = f"03-Projects/{project}/Preferences" if project else "01-Memory/User/Learned"
    elif kind == "solution":
        if basis != "verified-result" or not evidence.strip():
            raise ValueError("Reusable solutions need verified-result basis and concrete verification evidence")
        directory = "01-Memory/Solutions"
    else:
        if not project:
            raise ValueError("A project decision must have a project scope")
        directory = f"03-Projects/{project}/Decisions"
    content = (f"# {key.replace('-', ' ')}\n\nKind: {kind}\nBasis: {basis}\n"
               f"Project: {project or 'cross-project'}\nRecorded: {utc()}\nSource: {source}\n\n"
               f"{summary.strip()}\n\nVerification / applicability:\n{evidence.strip() or 'User statement; no broader inference.'}\n")
    return f"{directory}/{key}.md", content
