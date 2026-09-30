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

## Review inferred preferences

An agent can save an observed taste with `memory_learn(kind="preference",
basis="observation")`. It goes into `01-Memory/Candidates/<project>/` (or
`cross-project`) and is excluded from ordinary search and context. A person can
inspect the full note with `read`, then approve or reject its exact revision:

```cmd
.venv\Scripts\python.exe hive.py candidate-inbox
.venv\Scripts\python.exe hive.py read "01-Memory/Candidates/myapp/bright-ui.md"
.venv\Scripts\python.exe hive.py candidate-approve "01-Memory/Candidates/myapp/bright-ui.md" --expected-revision CURRENT_REVISION
.venv\Scripts\python.exe hive.py candidate-reject "01-Memory/Candidates/myapp/other-idea.md" --expected-revision CURRENT_REVISION
```

Approval records `Basis: user-approved` and moves a project preference only into
that project's confirmed preferences. A cross-project candidate enters the shared
profile only when explicitly approved. The original candidate is archived; rejection
also archives it. Revision checks refuse stale decisions. Existing confirmed notes
are never overwritten by approval.

## Reusable procedures

After a repeatable fix is verified, an agent can use the existing `memory_learn`
MCP tool with `kind="procedure"`, `basis="verified-result"`, a stable key,
summary, source, trigger, one to eight steps, and concrete evidence. Set `project`
for a project-only runbook or leave it empty for a genuinely shared procedure.
Procedures are ordinary Markdown notes in `Procedures/`, found with the existing
`memory_search` and `hive_context` query. They are not loaded wholesale or run
automatically. Inspect the trigger and verification before applying one.

```cmd
.venv\Scripts\python.exe hive.py procedure-report --project myapp --stale-days 180
.venv\Scripts\python.exe hive.py procedure-used "03-Projects/myapp/Procedures/cache-repair.md" --expected-revision CURRENT_REVISION --source "task 123" --evidence "Cache test passed"
.venv\Scripts\python.exe hive.py procedure-archive "03-Projects/myapp/Procedures/cache-repair.md" --expected-revision CURRENT_REVISION
.venv\Scripts\python.exe hive.py procedure-unarchive "99-Archive/03-Projects/myapp/Procedures/cache-repair.md" --expected-revision ARCHIVED_REVISION
```

The report is read-only and shows age since the last file edit, explicitly recorded
verified applications, and pairs with identical normalized steps. An agent or person
records an application after checking it worked; ordinary `note_read` stays read-only.
Unrecorded applications remain unknown, and duplicates are mechanical, not semantic.
Archiving removes a
procedure from normal recall but preserves the note and its previous versions.
Unarchive refuses to overwrite a current active note.

## Opt-in checkpoint review

```cmd
.venv\Scripts\python.exe hive.py review-checkpoint SESSION-ID --budget 1000
.venv\Scripts\python.exe hive.py review-checkpoint SESSION-ID --budget 1000 --stage
```

The command makes a bounded review packet from the latest saved checkpoint without
calling a model. `--stage` saves it as a draft under the project's `Review-Queue/`;
drafts are excluded from ordinary search and automatic context. Running it again
for the same checkpoint preserves any edits to the staged draft. A checkpoint is
agent-reported, so verify the source before converting a lesson into a procedure
or a confirmed preference. This command does not promote anything by itself.

## Review dashboard

`Review.md` brings the existing local review workflows into one Obsidian page.
Initialization and `hive.py export` generate the page and link it from `Home`.
Refresh it directly when notes change:

```cmd
.venv\Scripts\python.exe hive.py review-dashboard
.venv\Scripts\python.exe hive.py review-dashboard --project myapp --stale-days 90 --limit 25
```

On Linux use `.venv/bin/python`. The page links to inferred preferences awaiting
approval, checkpoint drafts whose `Status` is `draft`, and procedures older than
the chosen edit-age threshold. A project filter includes that project's notes
plus shared candidates and procedures. Each section has a configurable display
limit (1–100); totals and truncation are reported. Drafts that cannot be safely
read are counted separately. Commands on the page use placeholders: read the note
and its current revision before approving, rejecting, or archiving anything.

After reviewing a staged checkpoint, change `Status: draft` to `Status: reviewed`
in that draft and refresh the dashboard to remove it from the pending queue.
This marks human review only; it does not verify the checkpoint or promote its
claims into durable learning. Age alone does not make a procedure incorrect.
Refreshing the page makes no model calls and changes no source notes or approvals.

`Review.md` is a generated snapshot, so refreshes replace its content. If that
filename already contains a personal note, dashboard generation refuses to
overwrite it and `export` preserves it. Move the personal note if you want to use
the generated page. Existing user-edited `START.md` notes are preserved; `Home`
always provides the review link after export.
