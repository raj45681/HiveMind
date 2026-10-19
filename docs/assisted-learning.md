# Assisted learning with free or cheap models

HiveMind can combine its local memory workflow with an explicitly enabled
OpenRouter free model. Local code records activity; the model suggests concise
progress, tastes, working style, decisions, lessons and procedure ideas. Your
normal working agent still saves confirmed preferences and verified solutions.

This feature sends selected, filtered activity to an external provider. It does
not upload the vault or crawl previous conversations. Core memory remains local;
the `offline` setting for legacy memory connections is preserved. Assisted
learning is a separate, explicit opt-in to outbound inference.

## Guided project setup

Run `hive.py setup PROJECT` (or the normal `hivemind.cmd` launcher). The wizard
asks which clients to configure, free/cheap/off learning, model and budget,
capture/processing choices and optional project tastes. It requests a missing
OpenRouter key using hidden terminal input and stores it privately on this
device. Each project can use a different policy. See the
[client setup guide](harness-setup.md) for the 14 adapters and scope details.

## Legacy free-only enable command

Set `OPENROUTER_API_KEY` in the environment used by your clients. Do not put the
key in Git, a vault note or a command argument. From the HiveMind folder:

```powershell
.\.venv\Scripts\python.exe hive.py assist-enable myapp --daily-limit 1000
.\.venv\Scripts\python.exe hive.py assist-install-hooks --project 'D:\Projects\My App'
```

Use `--all-projects` instead of project names to cover all enrolled projects,
including projects enrolled later. Unenrolled folders are ignored. To retain
automatic recording but invoke the model manually, add `--manual` when enabling.
The default local daily budget is 900; `--daily-limit 1000` uses the reported
1000-request allowance. Other apps share that account allowance, so the local
counter cannot report your actual remaining OpenRouter quota.

Restart Codex and Grok Build and review/trust the new HiveMind hooks through
their native hook controls. Existing hook definitions are preserved and backed
up under `runtime/adapter-backups/`. Codex uses the project's `.codex/hooks.json`;
Grok uses the project's `.grok/hooks/hivemind.json`. User-wide hooks require an
explicit `--scope user`. Installation does not bypass hook trust. Until
callbacks actually run, native conversation capture is not active.

## What runs automatically

The command hook captures supported submitted prompts, tool events, visible final
responses, interruptions and turn boundaries. It first saves a bounded, filtered
file with a flushed filesystem write; a separate finite Python worker imports
those files into SQLite. Native UI callbacks never wait for the API or SQLite.
Source IDs make replay idempotent, and separate sessions/projects stay scoped.

User prompts and turn/interrupt/end events signal one local coalescing worker.
A burst shares a worker instead of launching one for every callback. Prompt-only
activity waits up to 60 seconds; a completed turn waits for 15 seconds of quiet,
so its prompts, tools and response can be processed together. Continuous activity
cannot postpone the first flush forever. Work arriving during inference gets a
fresh quiet window. Tool callbacks record without making a request per action.

Structured Hive checkpoints are already handoffs: they remain local and bypass
the second model. Empty lifecycle markers and short acknowledgements also skip
inference. Important interrupt reasons remain available as local handoff evidence.
HiveMind's own named MCP memory/checkpoint results are excluded from recorder
input, preventing shared memory and acknowledgement payloads from feeding back
into new learning suggestions.
Large tool payloads keep identity, command/path metadata and output edges instead
of resending entire files. Truncation is labelled; original harness output remains
the source for omitted content. User and assistant messages retain the normal
8000-character recording limit.

A worker makes at most four requests and runs for a bounded period, including
short rate/backoff waits. Longer quota deferrals wait for a later callback or
explicit `assist-run`. No paid agent session is launched. Background processes
have no console window on Windows. Manual project policies are excluded from
automatic processing even when another project's worker is active.

Each bounded batch contains new recorded events and the previous derived summary
for that harness session. It does not send unrelated project history or confirmed
personality files. The returned JSON is validated, and each proposed memory must
include a source ID and an exact quote from the submitted events. The model has
no tools, cannot approve its own output, and cannot change personality files.

- Tastes and working style enter `01-Memory/Candidates/` for explicit review.
- Decisions, lessons and procedure ideas enter the project's `Review-Queue/`.
- Progress is available as `derived_progress` in budgeted `hive_context`, labelled
  unreviewed. It is context for inspection, not proof that code or tests succeeded.

Normal memory search excludes candidates and review drafts. Existing
`candidate-approve`, `candidate-reject` and `learning-review` commands remain the
only promotion path for these suggestions. Read the note and original evidence
before accepting. Procedure ideas still need concrete verification and proper
runbook authoring before being relied on as verified procedures.

## Model budgets and failure behavior

The default model is `openrouter/free`. You can select a catalog model with an
explicit `:free` suffix using `--model`. Paid IDs are rejected, and the request
sets a zero input/output price ceiling. There is no paid fallback in free mode.
Project setup can separately opt into `cheap` mode with an explicit model,
daily dollar budget and input/output price ceilings. Those calls reserve a
conservative cost before sending; failed or unknown outcomes keep the reservation.
Successful reported usage reconciles it. Project request caps also apply under
the shared device cap. API/provider errors keep the local journal intact.

The request ledger is shared across enabled projects. It counts failed attempts,
applies a rolling 24-hour local budget, spaces requests by at least four seconds,
and caps local traffic at 18 requests per minute. Network and invalid-output
retries are limited to three attempts with a delay. Deferred batches are revisited
on a subsequent source event or explicit `assist-run`; there is no perpetual
model-driven polling loop. Keep the event journal when inference is unavailable.

Publishing failures reuse the already saved model response. Repeated exact
suggestions do not recreate an already reviewed candidate. Source records,
results and deduplication live in `runtime/hivemind.db`, outside the vault and Git.

The original capture time survives spool replay, including subsecond precision.
Dead-process leases can be recovered without waiting for their timeout. The
worker's final generation check covers callbacks arriving while it exits.
Malformed spool records move to `runtime/assisted-quarantine/`; good records
continue and the originals remain available for inspection. Source events are
retained when inference fails or request allowance is exhausted.

## Token accounting and clean handoffs

`assist-status --project PROJECT` reports actual provider prompt, completion,
total, cached and reasoning token counts for the last 24 hours. Missing usage is
reported separately, rather than presented as measured zero usage. Accounting
also survives malformed/truncated model output; model response text and reasoning
contents are never added to accounting logs. Counts use the provider's tokenizer;
they are distinct from HiveMind's bytes/4 context-budget estimates. Reasoning can
consume the output allowance even when the visible JSON is short.

The default maximum completion allowance is now 1800 tokens. Input contains
compact event IDs/roles/text and the short prior summary, without repeated event
timestamps. The model is asked for at most three short learning suggestions.
These device defaults can be changed inside the existing `assisted_learning`
object in ignored `hive.local.json` (preserve its other fields):

```json
{
  "debounce_seconds": 15,
  "max_delay_seconds": 60,
  "max_output_tokens": 1800
}
```

If a provider reports a nonzero charge for a free-only request, that project's
free inference pauses and status identifies the failed job. Review the provider
usage before explicitly retrying that job; `assist-retry` then clears its hold.
This does not switch to a paid model or discard local progress.

Use `hive.py handoff PROJECT` for a readable, model-free continuation packet.
It preserves the last substantive checkpoint, reported checks, changed files,
blockers, next actions and Git drift. New local activity is labelled unverified
and remains available during API outages, quota exhaustion and debounce. A later
empty lifecycle event or delayed older AI response cannot replace useful progress.
`hive_context` also supplies a small local activity fallback when it is newer than
the available AI summary. No new MCP tool or tool-schema overhead was added.

See [context and sessions](context-and-sessions.md) for handoff commands and limits.

```powershell
.\.venv\Scripts\python.exe hive.py assist-status
.\.venv\Scripts\python.exe hive.py assist-events myapp --limit 10
.\.venv\Scripts\python.exe hive.py assist-run --project myapp --limit 4
.\.venv\Scripts\python.exe hive.py review-dashboard --project myapp
```

The status report includes failed job IDs and reasons. After inspecting a failed
batch, use `assist-retry ASSIST-ID` to explicitly reset its attempt budget. Use
`assist-disable` to stop future capture/inference without deleting existing data.

For another client or an explicit source, record a text file (or `-` for stdin):

```powershell
.\.venv\Scripts\python.exe hive.py assist-record myapp activity.txt --agent generic --session my-session --role user --source-id message-1
```

## Limits

This is an extraction aid. An exact quote and valid JSON do not prove that a
suggestion is correct, current, generalizable or authorized. Original instructions
and source roles retain priority. A generated summary can omit detail.

Capture depends on each client's delivered hook fields, enabled features and
trust. Only explicitly enrolled working directories and their subdirectories
are matched; separately located Git worktrees must be enrolled too. Transcript
formats are not parsed. Missing callback fields and unavailable final responses
therefore limit coverage; this is not a full conversation export or automatic
agent failover implementation.

Events are capped at 8000 characters with an explicit truncation notice. A Hive
stores at most 20000 events and 2000 spooled files before reporting capacity
errors. Redaction covers common credential patterns but cannot detect every
secret; avoid including sensitive content. Local records are not encrypted.
Provider retention policies vary. Choose acceptable OpenRouter privacy controls
and endpoints before sending confidential work.

API keys use the process environment first, then private device storage. Windows
uses user-bound DPAPI; Linux uses a 0600 file in a 0700 directory. Environment
changes may require restarting clients. Killing a source agent after its hook has
saved an event does not require that source model to generate another response.
Disk failure or an event never exposed by a client cannot be recovered by this
feature. Checkpoint durability does not depend on assisted capture succeeding.

A hard app shutdown can also terminate its recorder helper or prevent the final
callback from being delivered. Already saved events remain queued for a later
callback or an explicit `assist-run`; there is no always-on service or startup
watcher to restart processing after shutdown. OpenRouter does not observe local
files independently. A complete final summary after a hard kill is not guaranteed.

References checked 2026-10-19: [free variants](https://openrouter.ai/docs/guides/routing/model-variants/free),
[structured output](https://openrouter.ai/docs/guides/features/structured-outputs),
[usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting),
[reasoning and output limits](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
[Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Grok hooks](https://docs.x.ai/build/features/hooks).
