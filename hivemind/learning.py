"""Structured scoped learning without additional model calls."""
import re
from .store import utc


def learning_note(kind, key, summary, source, project="", evidence="", basis="observation",
                  trigger="", steps=None, applicability=""):
    if kind not in {"preference", "solution", "decision", "procedure"} or basis not in {"user-stated", "verified-result", "observation"}:
        raise ValueError("Invalid memory kind or basis")
    if not re.fullmatch(r"[a-z0-9_-]{1,80}", key):
        raise ValueError("Use a stable lowercase memory key with letters, digits, underscores or hyphens")
    if project and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", project):
        raise ValueError("Invalid project ID")
    for value, limit in ((summary, 3000), (source, 500), (evidence, 1500),
                         (trigger, 400), (applicability, 500)):
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError("Keep learning concise: summary 3000, source 500, evidence 1500 characters")
    if not summary.strip() or not source.strip():
        raise ValueError("A useful summary and traceable source are required")
    if kind != "procedure" and (trigger or steps or applicability):
        raise ValueError("Procedure fields are only valid for procedure learning")
    if kind == "preference":
        if basis != "user-stated":
            directory = f"01-Memory/Candidates/{project or 'cross-project'}"
        else:
            directory = f"03-Projects/{project}/Preferences" if project else "01-Memory/User/Learned"
    elif kind == "solution":
        if basis != "verified-result" or not evidence.strip():
            raise ValueError("Reusable solutions need verified-result basis and concrete verification evidence")
        directory = "01-Memory/Solutions"
    elif kind == "procedure":
        if (basis != "verified-result" or not evidence.strip() or not trigger.strip()
                or not isinstance(steps, list) or not 1 <= len(steps) <= 8
                or any(not isinstance(step, str) or not step.strip() or len(step) > 400 for step in steps)):
            raise ValueError("Procedures need a trigger, 1-8 concise steps, verified-result basis and verification evidence")
        directory = f"03-Projects/{project}/Procedures" if project else "01-Memory/Procedures"
    else:
        if not project:
            raise ValueError("A project decision must have a project scope")
        directory = f"03-Projects/{project}/Decisions"
    content = (f"# {key.replace('-', ' ')}\n\nKind: {kind}\nBasis: {basis}\n"
               f"Project: {project or 'cross-project'}\nRecorded: {utc()}\nSource: {source}\n\n"
               f"{summary.strip()}\n\n")
    if kind == "procedure":
        content += (f"When to use: {trigger.strip()}\n\n## Steps\n\n"
                    + "\n".join(f"{index}. {step.strip()}" for index, step in enumerate(steps, 1))
                    + f"\n\nApplicability: {applicability.strip() or 'Only where the stated trigger and verification apply.'}\n\n")
    content += f"Verification / applicability:\n{evidence.strip() or 'User statement; no broader inference.'}\n"
    return f"{directory}/{key}.md", content
