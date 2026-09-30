# Core memory and goal workflows

HiveMind connects current knowledge to the task an agent is doing, and turns
completed work into reviewable learning. All workflows use the local Markdown
vault and SQLite authority. They make no inference calls to propose memory or
plans. Source text, task results, and verification remain evidence to inspect.

Run commands below with `.venv/bin/python` on Linux or
`.venv\Scripts\python.exe` on Windows, from the HiveMind folder.

## Current guidance and conflicting advice

Structured learning can include `topic`, a short `claim`, and affected `files`.
`hive.py learn examples/learned-decision.json` demonstrates the format; agents use
the existing `memory_learn` MCP tool. Project-specific solutions now live in the
project's `Solutions` folder. Existing shared-folder notes with explicit project
metadata are also scoped during keyword and semantic retrieval; they are not moved.

Two current notes with the same topic and scope but different claims receive a
potential-conflict warning. This compares declared claims, not the truth of prose.
Agents should inspect sources before using conflicting advice.

To record an explicit relationship, read both notes for their current revisions:

```text
hive.py memory-relate supersedes NEW_NOTE OLD_NOTE --revision NEW_REVISION --related-revision OLD_REVISION --source "Reviewed architecture decision"
hive.py memory-relate conflicts FIRST_NOTE SECOND_NOTE --revision FIRST_REVISION --related-revision SECOND_REVISION --source "Conflicting source evidence"
hive.py memory-relate resolve FIRST_NOTE SECOND_NOTE --revision FIRST_REVISION --related-revision SECOND_REVISION --source "Explicit conflict reviewed"
```

The equivalent MCP tool is `memory_relate`. Relationships stay within one scope.
Replacement marks the older note `State: superseded` and records `ReplacedBy` and
the relationship source in Markdown. Normal recall excludes superseded notes;
`memory_search(archive=true)` can inspect them. Current results link back to the
older notes. Note versions remain available. Conflicts are recorded on both notes
and surfaced in briefs. Resolving an explicit link does not remove a warning from
still-competing declared claims. Updating notes uses revision checks and preserves
user-owned personality and system files.

## Task-aware briefs

Task specifications accept a `files` array of project-relative paths. Learned
memory accepts the same array. File-linked notes are selected even when the task
title shares no keywords with them. Use:

```text
hive.py context myapp --task TASK-ID --budget 1000
hive.py context myapp --file src/auth.py --query "credential renewal" --budget 1000
```

Agents pass `task_id` or `files` to `hive_context`; CLI workers do this automatically.
Briefs include bounded dependency summaries and reported verification. When an
existing Graphify graph is available, directly related files can bring in further
linked memory. This reads the cached graph without rebuilding it; it is partial
and may be stale, so inspect live source or use `code_query` to refresh relationships.
All additions share the existing context budget. Large task details can be omitted
to preserve useful memory. A task ID from another project is rejected.

## Consolidation proposals

Find near-duplicate learned notes without changing them:

```text
hive.py consolidate myapp
hive.py consolidate myapp --stage
hive.py consolidate myapp --path FIRST_NOTE --path SECOND_NOTE --summary "Reviewed combined lesson" --stage
```

Omit the project to consolidate shared learning. The equivalent MCP tool is
`memory_consolidate`. Automatic discovery uses a conservative word-overlap
heuristic on at most 500 active learned notes; it is not semantic judgement.
Proposals preserve source paths and exact revisions, and keep file associations.
Procedure proposals retain the first source's trigger, steps, and applicability;
review all source differences before accepting or supply revised content.

Drafts are saved in `Review-Queue` and excluded from ordinary recall. Repeating
the same proposal preserves reviewer edits. Read the draft and its originals,
then explicitly decide using its current revision:

```text
hive.py learning-review PROPOSAL_PATH --revision CURRENT_REVISION --accept
hive.py learning-review PROPOSAL_PATH --revision CURRENT_REVISION --accept --content-file reviewed-memory.md
hive.py learning-review PROPOSAL_PATH --revision CURRENT_REVISION --reject
```

The MCP tool is `learning_review`. Acceptance saves the consolidated note and
marks its sources superseded while retaining history. Source drift rejects the
operation before any updates. Rejection changes only the draft's review status.
Oversized proposals are rejected before staging; split or shorten the source set.

## Learning from completed work

Finishing a task with `done` and reported verification creates an outcome proposal
containing its summary, affected files, and bounded evidence. The full task result
remains available. The draft is excluded from recall until explicitly accepted;
acceptance records reviewed evidence, not independent proof. Failed or blocked
tasks do not create reusable success lessons. `task_finish` reports proposal-save
errors without undoing an already completed task.

Refresh `hive.py review-dashboard` and open `Review` in Obsidian to inspect outcome
and consolidation proposals alongside other memory awaiting review. Preferences
continue to use explicit candidate approval and are never inferred from task success.

Task results can also explicitly report applied verified procedures:

```json
{
  "used_procedures": [
    {"path": "03-Projects/myapp/Procedures/refresh.md", "revision": "CURRENT_64_CHARACTER_HASH", "evidence": "Applied the steps and the renewal regression passed"}
  ]
}
```

Add this optional field to the usual task result. The coordinator checks the saved
procedure revision and scope before recording the application. Successful uses
give a bounded retrieval boost only while that note revision remains current.
Ordinary recall still writes no usage telemetry, and reported use is not independent
verification of the procedure's truth.

## Goal-level coordination

Create a draft dependency graph with explicit execution limits:

```text
hive.py goal-create examples/goal.json
hive.py goal-show GOAL-ID
hive.py goal-run GOAL-ID
```

The last command is a preview; it never launches agents. MCP clients use
`goal_create`, `goal_status`, and `goal_control`. A plan accepts up to 20 keyed task
nodes with dependencies and agent assignments; invalid references and cycles are
rejected. If no nodes are supplied, HiveMind proposes plan, implementation, and
review stages using the chosen agent. This is a template to inspect, not inferred
decomposition of arbitrary work. Stable project/key pairs avoid duplicate plans.
Draft plans and live state changes generate an Obsidian goal page linked from `Home`.
`goal-show` returns current state; the generated page is refreshed by state changes
and goal runs.

After reviewing the plan, activation queues its tasks without starting sessions:

```text
hive.py goal-activate GOAL-ID --revision CURRENT_REVISION
```

When you intend to use your configured agent accounts, explicitly execute:

```text
hive.py goal-run GOAL-ID --execute --max-tasks 2 --max-seconds 900 --max-parallel 1
```

The local scheduler dispatches ready nodes through the existing CLI workers,
without an LLM polling loop. Launch and time usage persist across runs; per-run
overrides can reduce the goal's configured limits. The runner reserves its time
allowance before dispatch and returns unused time on normal exit. An abruptly lost
runner retains that reservation when released, preserving the configured ceiling.
Concurrency defaults to one and
is capped at four. Unavailable adapters and other-machine assignments stop dispatch.
Generic MCP clients can still claim tasks interactively; local headless execution
uses the existing Codex, Grok, and Antigravity adapters. Actual executions use those
agents' account allowances.

Write tasks retain the existing clean-repository and isolated-worktree requirements.
Dependent tasks wait until the completed write has been reviewed and integrated:

```text
hive.py goal-integrated GOAL-ID --key implement --revision CURRENT_REVISION --source "Reviewed and merged the task branch"
```

This records the operator's integration statement; it does not perform or prove a
merge. The goal also waits for integration of terminal write nodes before reporting
completion. No result automatically merges, pushes, or deploys code. Failed dependencies
block downstream work. Failed tasks and expired leases require explicit inspection
and requeue; the scheduler never retries them automatically. A stale runner also
requires review before `goal-release --revision ... --source ...` can clear it.
Do not release a runner while its process is still executing. Execution limits apply
to the goal runner; separately authorized manual task runs remain available.

These workflows require an updated local or MCP authority. The legacy cloud-memory
connector does not implement the new lifecycle, review, or task-aware context APIs.
