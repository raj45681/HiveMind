"""A generated Obsidian review page; never approves or promotes memory."""
from urllib.parse import quote

from .context import validate_project
from .learning_ops import _fields, candidate_inbox, procedure_report
from .store import atomic_write, utc

MARKER = "<!-- HIVEMIND:REVIEW-DASHBOARD -->"


def link(path):
    label = path.removesuffix(".md").replace("[", "\\[").replace("]", "\\]")
    return f"[{label}](<{quote(path, safe='/')}>)"


def render(hive, project="", stale_days=180, limit=25):
    validate_project(project)
    if not 1 <= limit <= 100:
        raise ValueError("Review limit must be 1-100")
    target = hive.note_path("Review.md")
    if target.exists() and not target.read_text(encoding="utf-8").startswith(MARKER + "\n"):
        raise ValueError("Review.md contains a personal note; move it before generating a dashboard")
    candidates = candidate_inbox(hive, limit, project)
    procedures = procedure_report(hive, project, stale_days, limit, stale_only=True)
    drafts, proposals, unreadable = [], [], 0
    projects = hive.vault / "03-Projects"
    for path in sorted(projects.glob("*/Review-Queue/*.md")):
        scope = path.relative_to(projects).parts[0]
        if project and scope.casefold() != project.casefold():
            continue
        name = path.relative_to(hive.vault).as_posix()
        try:
            note = hive.read_note(name, limit=1000)
            if _fields(note["text"]).get("Status", "draft").casefold() == "draft":
                if _fields(note['text']).get('Kind') in {'outcome', 'consolidation'}:
                    proposals.append(name)
                else:
                    drafts.append(name)
        except (OSError, UnicodeError, ValueError):
            unreadable += 1
    lines = [MARKER, "# Memory review", "", f"Generated: {utc()}",
             f"Scope: {project or 'all projects'} (shared preferences and procedures included).", "",
             "[[Home|Task overview]] · [[START|Start here]]", "",
             "Open each note and inspect its source and verification before deciding. "
             "Refreshing this page does not approve preferences, promote drafts, or archive procedures.", "",
             f"## Preferences awaiting approval ({candidates['total']})", ""]
    lines.extend("- " + link(item["path"]) for item in candidates["candidates"])
    if not candidates["total"]:
        lines.append("No preferences await approval.")
    lines.extend(["", "Read the current revision before explicitly approving or rejecting a candidate:", "",
                  "```text", 'hive.py read "CANDIDATE_PATH"',
                  'hive.py candidate-approve "CANDIDATE_PATH" --expected-revision CURRENT_REVISION',
                  'hive.py candidate-reject "CANDIDATE_PATH" --expected-revision CURRENT_REVISION', "```", "",
                  f"## Checkpoint drafts ({len(drafts)})", ""])
    lines.extend("- " + link(name) for name in drafts[:limit])
    if not drafts:
        lines.append("No checkpoint drafts await review.")
    lines.extend(["", "Drafts contain agent-reported evidence. After review, change `Status: draft` to "
                  "`Status: reviewed` in the draft to remove it from this queue. "
                  "Create new drafts with `hive.py review-checkpoint SESSION-ID --stage`.", "",
                  f"## Learning proposals ({len(proposals)})", ""])
    lines.extend('- ' + link(name) for name in proposals[:limit])
    if not proposals:
        lines.append('No consolidation or completed-task lessons await review.')
    lines.extend(['', 'Read the proposal and source evidence, then accept or reject its current revision:', '',
                  '```text', 'hive.py learning-review "PROPOSAL_PATH" --revision CURRENT_REVISION --accept',
                  'hive.py learning-review "PROPOSAL_PATH" --revision CURRENT_REVISION --reject', '```', '',
                  f"## Procedures to check ({procedures['stale_count']})", "",
                  f"These procedures have not been edited for at least {stale_days} days. Age alone does not make them incorrect.", ""])
    lines.extend(f"- {link(item['path'])} — {item['age_days']} days; {item['uses']} recorded verified uses"
                 for item in procedures["procedures"])
    if not procedures["stale_count"]:
        lines.append("No procedures meet the age threshold.")
    lines.extend(["", "Inspect applicability and verification before editing or archiving a procedure:", "",
                  "```text", 'hive.py procedure-report',
                  'hive.py procedure-archive "PROCEDURE_PATH" --expected-revision CURRENT_REVISION', "```", "",
                  "## Refresh", "", "Run these commands with your HiveMind Python interpreter from the HiveMind folder.", "",
                  "```text", "hive.py review-dashboard" + (" --project " + project if project else ""), "```", "",
                  f"Each section shows at most {limit} entries. Unreadable checkpoint drafts: {unreadable}.", ""])
    atomic_write(target, "\n".join(lines))
    return {"dashboard": "vault/Review.md", "saved": True, "project": project or None,
            "candidates": candidates["total"], "drafts": len(drafts), "stale_procedures": procedures["stale_count"],
            'learning_proposals': len(proposals),
            "truncated": candidates["truncated"] or len(drafts) > limit or len(proposals) > limit or procedures["stale_count"] > limit,
            "unreadable_drafts": unreadable, "model_calls": 0}
