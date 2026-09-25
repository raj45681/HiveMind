# Online HiveMind memory

**Optional legacy integration.** New installations use local memory. The hosted
service is not included in this repository, and no website is needed for setup.
Use `hivemind.cmd` and the root README for the supported local workflow. The
instructions below apply only when you already have a compatible private service
and explicitly choose to connect it.

Memory is stored in a private hosted database. Codex, Grok Build and Antigravity
keep using the same local MCP bridge; memory calls go to that database over HTTPS.
No additional model or embedding calls are used for syncing, searching or retrying.
Retrieved text still uses the active agent's normal context tokens.

## New device, one project command

Open your private HiveMind site, choose **Connect**, download the bundle, and extract
its `HiveMind` folder into your home folder. Install Python 3.11+ if needed.
Open a terminal in the project you want to enroll.

Windows PowerShell:

```powershell
& "$HOME/HiveMind/install.ps1" -MemoryUrl "YOUR-PRIVATE-SITE-URL"
```

macOS / Linux:

```bash
python3 ~/HiveMind/bootstrap.py
~/HiveMind/.venv/bin/python ~/HiveMind/hive.py cloud-connect --url "YOUR-PRIVATE-SITE-URL"
```

The site supplies these commands with its real URL. Click **Copy private connection
key** and paste it only into the installer's hidden prompt. The key is stored under
the installation's ignored `runtime/` directory, never in project files or the vault.
It is an owner credential: do not share it with someone who should not access your memory.
To rotate it, rotate the Site gateway key and update the Site's `HIVE_API_TOKEN` secret,
then reconnect devices. Existing device keys will stop working.

Subsequent project enrollment on that device needs only the installer command.
Existing installations can use `.venv/Scripts/python.exe hive.py cloud-connect --url URL`
(or `.venv/bin/python hive.py ...` on macOS/Linux).

Restart existing agent sessions after setup. Each harness still controls project trust,
MCP permission prompts, and whether it follows project instructions. No extra skill is
required. Interactive memory capture is instruction-driven, not an operating-system
hook that can read every CLI conversation. Hive-dispatched workers additionally save
their validated result as a project handoff without an extra model call.

## What follows you

- Shared personality and working style, plus confirmed user preferences.
- Project state and decisions, scoped by the project's stable ID.
- Searchable verified solutions: problem, fix, evidence, and conditions where it applies.
- Inferred preferences remain candidates and are excluded from the automatic profile.

`hive_context(project, query)` retrieves a fresh small brief for substantial tasks.
The hosted brief caps included note/excerpt text at 7,800 characters, plus metadata.
`memory_learn` saves useful milestones. Agents should retrieve at most three full notes
initially and avoid repeating unchanged context or copying transcripts.
Past solutions still require checking against the new project's versions and constraints.

## Offline operation and conflicts

Writes are durably queued locally before sending. On a network outage they return
`saved: false, queued: true`; other devices cannot see them yet. Pending writes retry
on the next memory call or `hive.py memory-flush`. No background model polls the service.
Cached reads are explicitly marked stale. A revision conflict is retained for inspection,
never silently overwritten. Use `hive.py memory-outbox`, read the current remote note,
merge deliberately, then discard the superseded local entry by its ID.

Obsidian stays useful as a local mirror:

```text
hive.py memory-sync          # pull; preserve local edits
hive.py memory-sync --push   # also push edits to previously mirrored notes
hive.py memory-import        # import new local notes; never overwrite conflicts
```

Run these through the installation's `.venv` Python. New local-only notes require
`memory-import`; deletion is not propagated. `memory-sync` never erases notes. Do not
run multiple mirror operations at the same time. Keep independent backup exports.

The online service stores memory only. Task claims, agent execution and inter-agent
message queues remain on the existing coordinator. An always-on shared executor is
a separate deployment; this service does not run paid agents by itself.

## Hosting and portability

The original private Sites app used a Workers-compatible server and D1 storage.
Its source is outside this local-memory repository. Markdown remains exportable.
The Python agent bridge talks to `/api/hive`; the service
is not a native remotely authenticated MCP server. Agents continue using the registered
stdio MCP bridge. No model/API subscription credentials are uploaded.
