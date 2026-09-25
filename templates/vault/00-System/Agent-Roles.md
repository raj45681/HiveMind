# Agent roles

These are editable routing defaults, not claims that a vendor is always best at a task.

| Task kind | Default agent |
| --- | --- |
| Analysis / implementation | Codex |
| Research / independent review | Grok |
| Frontend | Antigravity |

Override `agent` in a task JSON file when another tool is more appropriate.
Routes currently live in `hivemind/models.py`; this note documents them.
