# Local memory inspection

HiveMind uses one Markdown vault and one SQLite authority on one device. These
commands inspect or repair local memory without launching an AI agent. They do not
run automatically, enlarge the default context brief, or add MCP tools.

Run commands from the HiveMind folder with `.venv\Scripts\python.exe` on Windows;
substitute `.venv/bin/python` on Linux.

## Note history and recovery

HiveMind saves content-addressed versions when notes are written through its tools
or when indexing observes an edit made directly in Obsidian. Existing installations
begin recording versions on their next index; changes made before that point may
not be recoverable. Generated task, dashboard and session views are excluded because
their source records already live in SQLite. History remains in the private runtime
database and is included in a normal HiveMind backup.

```cmd
.venv\Scripts\python.exe hive.py history "01-Memory/Solutions/my-fix.md"
.venv\Scripts\python.exe hive.py diff "01-Memory/Solutions/my-fix.md" OLD_REVISION
.venv\Scripts\python.exe hive.py read "01-Memory/Solutions/my-fix.md" --revision OLD_REVISION
.venv\Scripts\python.exe hive.py restore "01-Memory/Solutions/my-fix.md" OLD_REVISION --expected-revision CURRENT_REVISION
```

`history` returns recent revision IDs and timestamps. `diff` compares a saved
version with the current note by default and caps output at 12,000 characters;
`truncated` shows whether more differs. `restore` requires the current revision
from `read` or `history` and refuses to overwrite an intervening edit. It follows
the normal agent-write boundary: only `01-Memory`, `02-Decisions`, and `03-Projects`
can be restored through this command. Original personality and system notes remain
user-owned. Restoring creates another version, so the former current content is
still available.

Agents can use the existing `note_read` MCP tool with `include_history=true` to
see recent revision IDs, then pass `revision` to read one. The default `note_read`
output is unchanged.

## Read-only audit

```cmd
.venv\Scripts\python.exe hive.py memory-audit --project myapp
```

The audit scans local memory, decisions and the selected project's notes. It flags
empty or oversized notes, exact duplicate memory content, incomplete metadata on
structured learned notes, solution notes lacking verified basis/evidence, and
project metadata that disagrees with its folder. It does not modify files or judge
whether a statement is true. The output caps listed issues at 50 by default and
reports the total count. Omit `--project` to inspect all projects.

## Search stored handoffs

```cmd
.venv\Scripts\python.exe hive.py handoff-search "cache invalidation" --project myapp
.venv\Scripts\python.exe hive.py search "cache invalidation" --project myapp --handoffs
```

The first command searches the latest structured checkpoint for each session, completed task results,
and targeted messages stored in SQLite. The second combines at most two handoff
matches with ordinary note matches. Agents can opt in through the existing
`memory_search` MCP tool by setting `include_handoffs=true`. Results are scoped by
project when supplied, capped at five, and use short excerpts. It searches the
latest 1,000 matching records of each type; unscoped messages are excluded from a
project-filtered search. It does not import vendor chat transcripts. Checkpoint
summaries and verification remain agent-reported evidence, not proof on their own.

The audit and search use deterministic local processing. Reading returned text
still consumes an agent's normal context tokens when you pass it to a model.
