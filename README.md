<div align="center">

![HiveMind cat — One memory. Every agent.](docs/assets/hivemind-cat-banner.png)

**Local shared memory and coordination for any stdio MCP agent.**<br>
Guided setup registers recognized agent CLIs; other compatible clients connect manually.
Keep your preferences, project context, and hard-won solutions in one local folder.

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-8EF0CC?style=flat-square&labelColor=101B2C) ![Storage Markdown + SQLite](https://img.shields.io/badge/Storage-Markdown%20%2B%20SQLite-A59FFF?style=flat-square&labelColor=101B2C) ![Interface MCP](https://img.shields.io/badge/Interface-MCP-92B9FF?style=flat-square&labelColor=101B2C) ![Hosting Not required](https://img.shields.io/badge/Hosting-Not%20required-8EF0CC?style=flat-square&labelColor=101B2C) [![License MIT](https://img.shields.io/badge/License-MIT-C9D7E8?style=flat-square&labelColor=101B2C)](LICENSE)

[Quick start](#quick-start) · [How it works](#how-it-works) · [Connect an agent](docs/other-harnesses.md) · [Your memory](#your-memory) · [Backups](#back-up--move-devices) · [Documentation](#documentation)

</div>

---

## The idea

You finish a task with one agent. Later, another agent opens the same project
and retrieves what changed, why it changed, and what still needs attention. In
your next project, confirmed preferences and relevant solutions are available
again.

![The cat starts a task with an agent, recording a decision, a verified check, and a checkpoint](docs/assets/hivemind-task.gif)

HiveMind makes that handoff possible through **shared files and a local MCP server**.
It gives each agent a small, relevant brief and a place to save what it learns.

![The cat delivers a concise project-aware brief to the next agent](docs/assets/hivemind-handoff.gif)

**HiveMind is single-device software.** One Markdown vault and one SQLite database
serve the agents working on that computer. There is no device-sync requirement,
sync daemon, hosted memory bill, or cross-device conflict to resolve. Move to a
different computer with a backup when needed; do not run two active copies of the
same Hive. Existing remote connectors remain optional legacy paths, not the core
product.

| Remember | Coordinate | Own |
| :--- | :--- | :--- |
| Shared personality and working style | Targeted messages between agents | Plain Markdown you can edit |
| Confirmed preferences across projects | Tasks with explicit ownership | Local SQLite task history |
| Project decisions and verified solutions | Concise, persistent handoffs | Portable backups without hosting |
| Saved note versions and local audits | Searchable task and session history | One SQLite authority on this device |

![The cat gathers decisions, preferences, verified fixes, and handoffs into local Markdown memory](docs/assets/hivemind-remember.gif)

**Now with budgeted briefs and resumable sessions:** select a context budget,
retrieve complete project-aware excerpts, and carry structured checkpoints between
agents. The optional CLI wrapper also captures Git state when an agent exits.
[Explore context & sessions →](docs/context-and-sessions.md)

**Optional code intelligence:** Graphify builds a local map of code relationships.
Agents query it through one bounded HiveMind tool, with no indexing model or API key.
[Explore local code graphs →](docs/code-graphs.md)

**Optional semantic recall:** a local embedding model can find a past solution even
when your new question uses different words. It joins keyword search inside the
existing memory tools; the vault remains plain Markdown.
[Explore semantic memory →](docs/semantic-memory.md)

> **Local memory, normal agent accounts.** HiveMind needs no hosting subscription
> or embedding service. Semantic recall is an optional local model downloaded once.
> Your AI agents still use their own services and account allowances. Retrieved
> memory uses normal context tokens.

**One-command guided setup:** run HiveMind from your project's CMD prompt, pick
the features you want, and start working. It enrolls the project, registers the
supported installed agent CLIs, and checks the MCP connection. Your feature choice
is saved for the next project on this device. [Set up HiveMind →](#quick-start)

## Quick start

You need **Python 3.11+**, **Git**, and an agent that supports stdio MCP. Guided
setup can register recognized installed CLIs; other clients use the manual
connection details. The first setup downloads the Python dependencies. This source
repository is public, so cloning it needs no GitHub sign-in. Your personal vault
and device credentials remain local and are excluded from Git.

### 1 · Get HiveMind and enroll your project

Open **CMD in the project you want to work on** and paste this single line:

```cmd
(if not exist "%USERPROFILE%\HiveMind\hivemind.cmd" call git clone https://github.com/raj45681/HiveMind.git "%USERPROFILE%\HiveMind") & call "%USERPROFILE%\HiveMind\hivemind.cmd"
```

Already have HiveMind installed? From any project's CMD prompt:

```cmd
"%USERPROFILE%\HiveMind\hivemind.cmd"
```

On the first interactive run, choose what to include:

| Choice | Setup |
| :--- | :--- |
| **0 · Core** | Shared Markdown memory, SQLite task state, and MCP bridge |
| **1 · Semantic** | Core plus local paraphrase recall |
| **2 · Graphify** | Core plus local code relationships (Python 3.12+) |
| **3 · Both** | Core, semantic recall, and Graphify |
| **4 · All** | Both extras plus two optional working-style questions |

Semantic recall downloads a local model; Graphify downloads an isolated
dependency. HiveMind saves your semantic and Graphify choices on this device.
For the next project, run the same command from that project's CMD prompt; it
reuses those choices without asking again. The **All** option saves only the
working-style answers you actually enter. Setup makes no paid agent call.

Both commands are safe to rerun. Setup enrolls the project, registers each
installed CLI independently, then opens the local MCP bridge and calls
`hive_context` without starting an AI session. The final report shows each agent
as **ready**, **skipped**, or **needs-action**, plus the bridge check and next step.
A CLI marked ready has its HiveMind entry registered and listed; restart an active
agent session to load it. A skipped CLI can be installed later, then the same
setup command will register it. If one CLI fails, the others are still attempted.

To reopen the menu and change the defaults for future projects, run this from
the project you are setting up:

```cmd
"%USERPROFILE%\HiveMind\hivemind.cmd" --configure
```

If you installed HiveMind before the guided menu was added, use `--configure`
once to set your defaults.

No separate agent skill or manual MCP configuration is needed. Restart any agent
session that was open during setup so it can load the new bridge.

<details>
<summary><strong>Advanced: unattended setup and per-run feature flags</strong></summary>

For scripts or unattended setup, add `--no-prompt` to use saved defaults (core
only on a fresh install). `--with-semantic` and `--with-graphify` add a feature
for one run without opening the menu. Add `--personalize` when you only want
the working-style questions. Changing defaults does not uninstall an already
installed model or disable Graphify in existing projects.

Or specify the project explicitly:

```cmd
"%USERPROFILE%\HiveMind\hivemind.cmd" "D:\Projects\My App"
```

To include **local Graphify code indexing** (Python 3.12+, first setup downloads dependencies):

```cmd
"%USERPROFILE%\HiveMind\hivemind.cmd" "D:\Projects\My App" --with-graphify
```

To include **local semantic memory** (first setup downloads an isolated model):

```cmd
"%USERPROFILE%\HiveMind\hivemind.cmd" "D:\Projects\My App" --with-semantic
```

Once installed on the coordinator device, semantic recall works for **every
enrolled project** through the existing `memory_search` and `hive_context` tools.
No agent skill or extra MCP tool is needed. Combine both flags if you want
Graphify as well. [Setup, privacy and limits →](docs/semantic-memory.md)

Run the same command for each new project **on this device**. Source-only graphs
are rebuilt locally; preferences and handoffs stay in the same vault. Memory-only
setup still works without Graphify. [Supported files, limits and recovery →](docs/code-graphs.md)

</details>

<details>
<summary><strong>PowerShell and Linux commands</strong></summary>

PowerShell, after cloning to your home folder:

```powershell
& "$HOME\HiveMind\hivemind.cmd"
```

Linux, from your project's folder:

```bash
git clone https://github.com/raj45681/HiveMind.git "$HOME/HiveMind" && python3 "$HOME/HiveMind/bootstrap.py"
```

Subsequent projects:

```bash
python3 "$HOME/HiveMind/bootstrap.py"
```

Linux may need its distribution's Python `venv` package. Windows has been tested
locally; the portable code has not yet been exercised on a physical Linux device.

</details>

### 2 · Restart your agent session

Setup registers the `hivemind` MCP bridge with installed CLI adapters it recognizes.
It adds a managed workflow to `AGENTS.md`, handles an existing
`AGENTS.override.md`, and adds a pointer to an existing `GEMINI.md`.

Existing instructions are preserved and backed up. Reruns update the managed
section without duplicating it. Accept normal project-trust and MCP prompts from
your client. Missing CLIs are skipped.
The bridge probe verifies the server itself; it cannot verify that an already-open
client session has reloaded its MCP configuration.

Any stdio MCP client can join the same memory and task state. Run
`hive.py client-info PROJECT` for their local connection command, then load the
project’s `AGENTS.md` workflow in that client. Context and session tools accept a
stable lowercase client ID; automatic registration and queued headless execution
are limited to verified CLI adapters. [Connect another agent →](docs/other-harnesses.md)

### 3 · Work as usual

Agents are instructed to retrieve context before substantial work, save verified
learning at milestones, and leave a handoff. You do not need to repeat
“use HiveMind” for every task in an enrolled project.

**This is an instruction-based workflow.** Agents must follow the rules and have
access to the tools. HiveMind does not silently capture every chat, guarantee
model compliance, or automatically launch another agent.

### Verify the handoff path

From the HiveMind folder, run this after setup or an update:

```cmd
.venv\Scripts\python.exe hive.py verify
```

`verify` creates a disposable vault and Git project. It starts two separate MCP
server processes, saves memory and a session checkpoint in the first, then checks
that a fresh context retrieves the right project note and handoff within a
1,000 estimated-token budget. It also checks safe memory retry, project
isolation, and Git drift detection. The command returns a nonzero exit code on
failure and uses **no paid agent/model calls**. Your real vault and projects are
untouched. On Linux use `.venv/bin/python`.

This proves the local protocol path, not that a vendor client has loaded its
registration or will follow the project instructions. The onboarding report
checks registration; restart the client and ask it to call `hive_context` to
check its live session.

## How it works

![The cat moves between owned tasks, checkpoints, and handoffs for three illustrative agents](docs/assets/hivemind-coordinate.gif)

```mermaid
flowchart TB
    A1[Agent A] --> M
    A2[Agent B] --> M
    A3[Any stdio MCP agent] --> M
    M[Local HiveMind MCP bridge]
    M <--> V["Markdown vault<br/>Style · preferences · project memory"]
    M <--> D["SQLite<br/>Tasks · claims · events · messages"]
    M --> Q["Optional Graphify<br/>Local source relationships"]
    O[Obsidian or your editor] <--> V
    V --> B[Portable backup]
    D --> B
    classDef agent fill:#182b2c,stroke:#8ef0cc,color:#edfff8
    classDef core fill:#23213d,stroke:#a59fff,color:#f0edff
    classDef data fill:#172338,stroke:#92b9ff,color:#e8f0ff
    class A1,A2,A3 agent
    class M core
    class V,D,O,B,Q data
```

| When | What happens |
| :--- | :--- |
| **Start a task** | Fetch a bounded brief: shared style, confirmed preferences, project state, and relevant search matches. |
| **Reach a milestone** | Record a verified solution, reusable procedure or scoped decision with its source and evidence. |
| **Finish work** | Update project state and leave a concise handoff for the next agent. |
| **Switch projects** | Reuse confirmed preferences; search for applicable past solutions. Project choices stay scoped. |

**Small context by design:** configurable briefs (default 1800 estimated tokens),
query-matched notes packed before the remaining standing context, complete excerpts
ranked by project, relevance and freshness, revision-checked writes,
and no model-driven queue polling. Optional semantic search runs local embedding
inference only; it makes no paid agent or hosted API calls. Other search and
coordination remain deterministic;
reading the returned text still consumes context. Inferred tastes stay separate
from confirmed preferences. Current instructions always take precedence over memory.

Candidate review, checkpoint review, procedure maintenance, history, audit and older
handoff search are **on demand**. They do not expand the default brief or add MCP
tools. Agents can save verified procedures through the existing `memory_learn` tool;
inferred preferences need explicit local approval before they enter the profile.
[Use local memory operations →](docs/local-memory-operations.md)

### One server, up to 17 tools

HiveMind registers **one MCP server** with each agent. That server exposes 16 core
tools, plus `code_query` on devices with Graphify-enabled projects:

| Purpose | Tools | Count |
| :--- | :--- | ---: |
| Memory | `hive_context`, `memory_search`, `note_read`, `memory_write`, `memory_learn` | 5 |
| Sessions | `session_start`, `session_checkpoint`, `session_resume` | 3 |
| Tasks | `task_create`, `task_get`, `task_list`, `task_claim`, `task_heartbeat`, `task_finish` | 6 |
| Messages | `message_send`, `message_inbox` | 2 |
| Optional code graph | `code_query` | 1 |

The MCP `tools/list` response distinguishes reads from writes with explicit
`readOnlyHint` annotations. New sessions, tasks and messages are marked additive;
checkpoints, memory updates and task-state changes are marked as mutations.
`code_query` can refresh its disposable local index, so it is marked as a
non-destructive write even though it does not edit source files. These are client
hints, not an access-control boundary; the server still validates every write.

The local authority's `tools/list` response explicitly classifies retries for every write:

| `idempotentHint` | Tools | Meaning |
| :--- | :--- | :--- |
| `true` | `session_checkpoint`, `memory_write`, `memory_learn`, `task_finish` | Repeating the same arguments cannot add another persisted change. `memory_learn` or `task_finish` may still return a conflict or ownership error; read the saved note or task to confirm the first result. |
| `false` | `session_start`, `task_create`, `task_claim`, `task_heartbeat`, `message_send`, optional `code_query` | A repeat may create another record, claim different work, extend a lease, or refresh an index. `session_start` is retry-safe only when the caller supplies a stable `session_id`. |

Read-only tools leave `idempotentHint` unset because the hint only applies to
environment-changing tools. An idempotency hint describes repeated effects, not
an identical response or a guarantee that the first call succeeded. A forwarding
bridge conservatively marks writes non-idempotent because it cannot verify an
older authority's behavior; legacy cloud-memory writes are treated the same way.

Task and messaging tools support explicit coordination; their presence does not
launch other agents. All 16 core tools are currently exposed. A smaller tool profile
is a proposed optimization, not an available setting yet.

**When Graphify runs:** selecting it in setup or using `--with-graphify` builds the initial index.
Subsequent `code_query` calls check for source changes and refresh as needed.
It does not run continuously or after every message. Agents are instructed to use
it for code relationships; preferences and handoffs use the memory/session tools.
[Invocation details →](docs/code-graphs.md#when-graphify-runs)

**Token overhead:** semantic indexing uses your CPU, not agent tokens. Tool definitions,
project instructions and returned text still add context to the agent. The 1,800-token
memory brief and 1,000-token code-query defaults are approximate response ceilings,
not fixed per-task charges or limits on the entire conversation. Actual usage depends
on the harness, tokenizer, caching and number of calls. Net savings from fewer file
reads have not yet been measured in a paid-agent task.

## Your memory

![The cat opens a local Hive folder containing editable notes and task history](docs/assets/hivemind-own.gif)

```text
HiveMind/
├── hivemind.cmd          Windows project installer
├── bootstrap.py          Portable first-run setup
├── hive.py               CLI + MCP entry point
├── hivemind/             Memory, coordination, and execution
├── templates/vault/      Generic starter notes tracked in Git
├── vault/                Your private local notes — ignored by Git
│   ├── 00-System/        Personality and working style
│   ├── 01-Memory/        Preferences and reusable solutions
│   ├── 02-Decisions/     Decisions and reasons
│   ├── 03-Projects/      Project state and handoffs
│   ├── 04-Tasks/         Generated task views
│   └── 05-Agents/        Agent roles
├── runtime/              Private SQLite database, note versions, logs, and config backups
└── tests/                Automated tests without paid inference
```

Open **`vault/` as an Obsidian vault**, then open `START`. No community plugin is
required, and Obsidian does not need to be running. Edit `00-System/Personality.md`
and `00-System/Working-Style.md` to define how your agents should work.

Session checkpoints retain completed work, reported checks, blockers and next
steps in SQLite, with a Markdown view under each project's `Sessions/` directory.
Concurrent updates require revision checks, and a CLI exit alone is never treated
as verified completion. Resume compares each saved Git/worktree fingerprint with
the live state and flags changed or unverifiable handoffs for inspection.
[Session behavior and limitations](docs/context-and-sessions.md).

**Opt-in worktree recovery** saves bounded private file snapshots at session
milestones. Preview a changed file, restore it only while its current hash matches
the preview, and undo that restore if needed. [Setup and limits →](docs/recovery-snapshots.md)

Saved Markdown revisions can be inspected, diffed and restored with a current-revision
check. A read-only audit flags mechanical issues; an opt-in search finds older task,
session and message handoffs. These local operations make no model calls.
[Commands and limits →](docs/local-memory-operations.md)

The repository ships generic templates. First setup creates missing local notes
without replacing your edits. **Your real vault, credentials, databases, device
configuration, and backup ZIPs are excluded from Git.** Cloning this repository
installs the software; it does not restore your personal memory.

## Back up & move devices

From your HiveMind folder, after stopping active agent sessions and task workers:

```cmd
.venv\Scripts\python.exe hive.py backup "D:\Backups\HiveMind-2026-09-25.zip"
```

Choose a new filename each time. Existing backups are never overwritten.

| Included in a personal backup | Recreated or transferred separately |
| :--- | :--- |
| Source and starter templates | Python environment and dependencies |
| Vault notes and attachments | Agent logins and credentials |
| Consistent SQLite snapshot | Device-specific repository paths |
| Task history and messages | Working code, worktrees, run logs, and cloud cache |

Extract the ZIP's `HiveMind` folder onto the next device. Install Python and your
agent CLIs, then run its installer in each project. Keep the project's tracked
`.hivemind/project.json` to preserve its identity. Restart agent sessions.

**Use one active copy.** Moving devices is migration, not live synchronization.
Move a fresh backup when switching devices; do not sync a live SQLite database
with a generic folder-sync tool. Personal backups contain private data—keep them
out of GitHub releases and repository commits.

## Useful commands

Run these from your HiveMind folder. On Linux substitute `.venv/bin/python`.

```cmd
.venv\Scripts\python.exe hive.py doctor
.venv\Scripts\python.exe hive.py status
.venv\Scripts\python.exe hive.py search "authentication"
.venv\Scripts\python.exe hive.py offline
.venv\Scripts\python.exe hive.py context myapp --query "login" --budget 1000
.venv\Scripts\python.exe hive.py resume myapp
.venv\Scripts\python.exe hive.py history "01-Memory/Solutions/my-fix.md"
.venv\Scripts\python.exe hive.py memory-audit --project myapp
.venv\Scripts\python.exe hive.py handoff-search "authentication" --project myapp
```

The installer also supports `--dry-run`, `--name myapp`, `--skip-register`,
`--configure`, and `--no-prompt`.

For an explicitly launched interactive session with automatic exit capture:

```cmd
.venv\Scripts\python.exe hive.py session-run myapp AGENT-CLI
```

Replace `AGENT-CLI` with an installed CLI name or a locally configured adapter.
This uses that agent's normal account usage.
Normal agent launches still work; their handoff capture depends on following the
installed instructions. The context budget is an estimate for the returned brief,
not a hard limit on the agent's full conversation or account usage.

<details>
<summary><strong>Optional: explicitly queue and run an agent task</strong></summary>

```cmd
.venv\Scripts\python.exe hive.py create examples\inspect-project.json
.venv\Scripts\python.exe hive.py run TASK-ID --dry-run
```

Replace `TASK-ID` with the returned ID. Remove `--dry-run` only when you intend
to execute the task using the assigned agent's account. Creating tasks or sending
messages does not wake a model.

Tasks use claims and leases to prevent duplicate ownership. Write tasks require
a clean Git repository with a committed HEAD and run in an isolated worktree.
Expired tasks block for inspection and explicit requeue. Reported evidence needs
review; nothing automatically merges, pushes, or deploys code.

</details>

## Development

```cmd
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests cover memory conflicts, ownership and leases, worker result handling, MCP
interoperability, project-rule preservation, offline routing, backup restoration,
private-vault separation, and a two-process handoff acceptance check. They use
temporary fixtures and no paid model calls.

The protocol suite verifies tool behavior, not a model's instruction compliance.
Real vendor inference and physical Linux behavior need separate smoke tests.

## Documentation

| Guide | Read it for |
| :--- | :--- |
| [Budgeted context & session handoffs](docs/context-and-sessions.md) | Smaller briefs, checkpoints, resume, and optional CLI exit capture |
| [Optional local code graphs](docs/code-graphs.md) | Graphify setup, bounded queries, supported files and fallback behavior |
| [Optional semantic memory](docs/semantic-memory.md) | Paraphrase recall, one-time model setup, local index and limits |
| [Local memory inspection](docs/local-memory-operations.md) | Note history, safe restore, read-only audit, and handoff search |
| [Worktree recovery snapshots](docs/recovery-snapshots.md) | Opt-in capture, diff preview, conflict-checked file restore and undo |
| [Device migration](docs/new-device.md) | Moving a local Hive with a backup; legacy remote configuration |
| [Optional cloud bridge](docs/cloud-memory.md) | Legacy cloud integration; inactive in local mode |
| [Implementation references](docs/sources.md) | Protocol and CLI references |
| [Agent workflow](AGENTS.md) | Instructions used when developing HiveMind |

## License

HiveMind is licensed under the [MIT License](LICENSE). Keep the copyright and
license notice when redistributing it. Installed third-party dependencies retain
their own licenses.

---

<div align="center">

![The cat waves beside the HiveMind mark and the One memory. Every agent. tagline](docs/assets/hivemind-closing.gif)

**Keep the context. Carry the learning. Choose the agent.**

Markdown you can read · SQLite you can back up · A folder you control

</div>
