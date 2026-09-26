# Budgeted context and resumable sessions

These features run locally using Markdown, SQLite and Git metadata. They do not
call a summarization model, launch a manager agent, or capture raw conversations.

## Context you can budget

From your HiveMind folder:

```cmd
.venv\Scripts\python.exe hive.py context myapp --query "login bug" --budget 1000
.venv\Scripts\python.exe hive.py context-budget 1200
```

The first command overrides this one brief. The second saves the local authority's
default in the ignored `hive.local.json`. The default is 1800; allowed values are
512–8192. MCP agents use `hive_context(project="myapp", query="login bug",
budget_tokens=1000)`. Omitting the budget uses the authority's configured default.

The budget covers the **serialized returned JSON**, including metadata. Its hard
ceiling is `budget_tokens * 4` UTF-8 bytes. The displayed token count is a bytes/4
estimate, not a vendor tokenizer or an account-usage measurement. Tool schemas,
AGENTS.md, other tool calls, and the rest of the conversation are outside this
budget. Non-English text and code can tokenize differently.

The brief includes shared rules, preferences, project state and preferences, a
compact latest-session packet, and up to three relevant notes. Small budgets can
omit sections or evidence; `omitted`, `details_omitted`, and `omitted_items` make
that explicit. Use `session_resume` for the full checkpoint and `note_read` for
an original note. Never overwrite a full note with a context excerpt.

Excerpts contain whole source sentences or bullets. Long units that cannot fit
are omitted rather than cut in half. They are extractive selections, not generated
summaries, and may skip intervening content. Read the original for nuance.

Search combines SQLite lexical relevance with bounded boosts for the current
project and file modification freshness. Use `memory_search(..., project="myapp")`
to exclude other projects' `03-Projects` notes while retaining shared solutions.
An unscoped search still searches all projects. Candidate tastes and archives
remain excluded by default. Freshness uses file timestamps, not proof that a
solution is still correct. Existing notes are never rewritten by retrieval.

## Structured handoffs

Each session has a project, starting agent, goal, stable ID and revision. Each
checkpoint records:

- A concise outcome and completed work.
- Agent-reported changed files and verification evidence.
- Blockers and concrete next steps.
- Observed Git branch, HEAD and file paths when the project is mapped locally.
- A fingerprint of staged changes and dirty tracked/untracked file contents in the
  execution worktree. The fingerprint is metadata; file contents are not saved.

Git metadata does not include diffs, file contents, remote URLs or agent arguments.
Observed files can include pre-existing or concurrent changes; they are not proof
of authorship. A missing Git installation or repository is recorded explicitly.
Interactive sessions observe the mapped repository. Queued write workers capture
their separate execution worktree before launch and at later checkpoints.
This lets a resume notice partial edits even when a worker exits before a final
handoff. Fingerprints cover Git-visible changes, not ignored files or external
state such as databases, running services or deployments.

`session_resume` returns `session.git_drift.status`: `match`, `changed`, or
`unverifiable`. It compares the saved branch, HEAD and fingerprint against the
same live worktree. `hive_context` includes the compact status in its session
packet. A changed or unverifiable result means inspect the live repository before
trusting the saved handoff; it does not discard history or claim a fix. Older
checkpoints without a fingerprint, removed worktrees, unavailable Git, and dirty
submodules are reported as unverifiable rather than silently treated as current.
The check is local and requires no model call. It is a point-in-time observation,
not a lock against another process editing the repo immediately afterward.

Agents use `session_start`, `session_checkpoint`, and `session_resume`. A checkpoint
requires the last revision; stale writes fail instead of overwriting newer work.
Retrying the identical write against its immediately previous revision is safe.
Read and merge a conflicting checkpoint before retrying. Start a new session for
a different agent's task after reading the previous handoff.

SQLite retains every revision, and the latest checkpoint is mirrored to
`vault/03-Projects/<project>/Sessions/<session-id>.md`. This is a generated view.
If its export fails, the response says `saved: true, markdown_saved: false`; the
database checkpoint remains resumable and included in personal backups. Retrying
the same checkpoint repairs the view. These notes and databases stay out of Git.

### Read-only resume

```cmd
.venv\Scripts\python.exe hive.py resume myapp
.venv\Scripts\python.exe hive.py resume myapp --session SESSION-0123456789abcdef
```

This reads saved state; it does not execute next steps or launch an agent. Legacy
Markdown handoffs remain searchable and are not silently converted into sessions.

### Save from the CLI

```cmd
.venv\Scripts\python.exe hive.py session-start myapp --agent codex --goal "Fix login validation"
.venv\Scripts\python.exe hive.py checkpoint SESSION-ID examples\checkpoint.json --revision 0
```

Replace `SESSION-ID` with the returned ID and edit the JSON to describe actual
work. The example is a format guide, not evidence to reuse. Completed status
requires nonempty completed work and reported verification. HiveMind validates
the fields, not the truth of a model's claims. Checkpoints do not change task leases.

## Optional automatic exit capture

To explicitly launch an interactive CLI through HiveMind:

```cmd
.venv\Scripts\python.exe hive.py session-run myapp codex
.venv\Scripts\python.exe hive.py session-run myapp grok
.venv\Scripts\python.exe hive.py session-run myapp antigravity
```

Additional CLI arguments can follow `--`. This launches the selected CLI and uses
its normal account, permissions and usage. It runs in the mapped project folder.
The wrapper sets `HIVE_SESSION_ID` so the agent can checkpoint the same session.
Restart or rerun project enrollment to load the updated AGENTS.md instructions.

On exit, the wrapper preserves any agent-authored handoff and captures repository
state. Exit code 0 without a final handoff becomes `needs_handoff`, not `completed`.
A failed or interrupted process becomes `interrupted`. A checkpoint failure leaves
a recovery file under `runtime/session-recovery/`; use resume before merging it.

An abrupt machine shutdown or forced termination of the wrapper can prevent its
exit capture. The last committed milestone survives; an `active` session is not
proof that a process is still running. Saving milestones limits lost context.
Normal unwrapped sessions remain instruction-driven. Hive task workers save
structured checkpoints from their validated result without a second model call.

## Existing installations

Pull the source update, rerun `hivemind.cmd` in each enrolled project, and restart
existing agent sessions to discover the 16 MCP tools. Initialization adds the
session tables without replacing tasks, messages or Markdown notes.

An upgraded remote MCP authority supports these tools too; its local configuration
and mapped paths apply. The legacy hosted memory API does not support explicit
context budgets or scoped search. Keep this installation in local mode for the
complete workflow; no hosted service update is performed by this release.
