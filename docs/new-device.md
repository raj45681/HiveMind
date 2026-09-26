# Move HiveMind to another device

## Current setup: portable local memory

HiveMind is designed for one active device. This guide covers migration or
recovery, not ongoing synchronization. Create a fresh
bundle with `hive.py backup PATH.zip`, extract its `HiveMind` folder on the next
device, install Python 3.11+ and your agent CLIs, then run that folder's
`hivemind.cmd` from each project's CMD prompt. On Linux run
`python3 ~/HiveMind/bootstrap.py`. Initial Python dependencies need internet once.
The bundle restores vault memory, tasks and messages; device paths and credentials
are recreated. Restart existing agent sessions. See the root README for commands.

Use one active copy and move a new bundle when switching devices. Offline copies
do not sync or merge automatically. Keep working repositories and their artifacts
separately; the portable memory bundle excludes worktrees, run logs and CLI logins.

## Legacy optional remote coordination (not active)

The remaining steps describe a separately configured server retained for existing
installations. They are outside the single-device workflow and are not performed
by the project installer.

You do not need access to an existing second device to prepare this. Each future
device installs the same bundle and keeps its own CLI logins and project paths.

## 1. Install locally

Copy and extract the portable bundle to a permanent folder, for example
`D:\HiveMind` on Windows or `~/HiveMind` on Linux. Avoid moving it after registration,
because the agent configurations contain its absolute Python and script paths.
Install Python 3.11+ and Git, plus whichever agent CLIs you want on that device.
Authenticate the agent CLIs using their supported account login flows.

Windows PowerShell:

```powershell
powershell -File .\setup.ps1
.\.venv\Scripts\python.exe hive.py doctor
```

Linux terminal (the OS's Python venv package may be needed):

```bash
bash setup.sh
.venv/bin/python hive.py doctor
```

Missing CLIs are skipped. Rerun setup after installing them. Setup preserves
other MCP entries and makes local config backups. To avoid registration use
`setup.ps1 -SkipRegister` or `bash setup.sh --skip-register`.

For independent local use, you are done. Open `vault` as a vault in Obsidian.
For a shared Hive, follow the remaining steps so devices use one coordinator.

## 2. Choose one authoritative coordinator

Choose a device that will be available when others use the Hive. Only it holds the
authoritative `runtime/hivemind.db` and active vault. Initially this project is a
local coordinator on Windows. Nothing is exposed over the network automatically.

Start the HTTP server on the chosen authority:

```bash
.venv/bin/python hive.py serve --http --hostname YOUR-PRIVATE-HOSTNAME
```

On Windows use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`.
The server listens only on `127.0.0.1:8787` and requires a bearer token. It creates
`runtime/server-token` on first HTTP startup. Read that file privately when pairing
another device; do not put its contents into chats, notes, Git or screenshots.

Use a private HTTPS reverse proxy to reach this loopback service. For example,
after installing/signing into Tailscale on both devices:

```bash
tailscale serve --bg 8787
```

Use **Serve**, which is private to your tailnet. Do not enable Funnel for this Hive.
Tailscale may require HTTPS/Serve setup in its admin UI. Use the HTTPS hostname it
provides as `--hostname`, and append `/mcp` to the URL when joining devices. Keep the
server running. An example Linux user systemd service is provided in
`scripts/hivemind.service.example`; replace the path/hostname before installing it.
Persistent server startup and network exposure are intentionally not activated by setup.

Reference: [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

## 3. Pair each additional device

```powershell
.\.venv\Scripts\python.exe hive.py connect --url https://YOUR-PRIVATE-HOSTNAME/mcp
```

The command privately prompts for the coordinator token and verifies the connection
before saving it. On Linux substitute `.venv/bin/python`. The token is stored only
under that device's ignored `runtime/client-token` file. Treat it as access to the
whole shared Hive; this version has a single trusted-user token, not per-device roles.

The same `hivemind` stdio registration now forwards all tools to the authority.
Restart agent sessions after changing the connection. Run `hive.py doctor` to verify
it; no model call is made. There is no silent fallback to a second local database if
the coordinator is offline.

## 4. Map repositories

Use the same project ID on every device, but each device's own clone path:

```powershell
.\.venv\Scripts\python.exe hive.py project-add myapp D:\Projects\MyApp
```

```bash
.venv/bin/python hive.py project-add myapp ~/projects/myapp
```

Use Git to transfer code changes. A queued task with `machine: "any"` can be claimed
by either device; a specific hostname restricts it. `doctor` shows the local machine
ID. Execute tasks explicitly with `hive.py run TASK-ID`; no agent daemon is installed.

For automatic instructions and project registration together, run the project
installer from each repository's root instead of manually mapping it:

```powershell
& "D:\HiveMind\install.ps1"
```

```bash
bash ~/HiveMind/install.sh
```

This preserves any committed project identity, maps the local clone, registers
the MCP bridge and installs the managed AGENTS.md workflow. Restart the harness
session once; subsequent normal work uses the shared-memory instructions.

## Obsidian on multiple devices

Agents always use the authority's notes through MCP. You can optionally synchronize
the **vault only** for human reading/editing with your preferred Obsidian sync setup.
The coordinator's search reindexes changed Markdown on the next search. Do not sync
`runtime`, `.venv`, `.worktrees`, credentials or working repositories this way.

Sync is not a distributed lock. Avoid editing the same note concurrently in Obsidian
and through agents. Task views are generated: change execution state through Hive
tools, not by editing a synced task status. If the authority is offline, synced notes
remain readable but shared task execution is unavailable.

## Move the authority later

Stop task workers and the old coordinator, then back up and move the vault and the
runtime database using a consistent SQLite backup. Do not copy an active DB while
ignoring its WAL files. Start the new authority and reconnect every client. Leave
the old authority stopped. Device clones, virtual environments and logins are not
portable state; recreate them with setup.

To leave a shared coordinator, run `hive.py disconnect` and restart agent sessions.
This selects local mode and does not move or delete remote data.
