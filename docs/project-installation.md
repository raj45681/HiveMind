# Project-only activation with shared memory

Install the software and keep one shared vault on your device. Enroll only the
projects that should use it. Project setup writes activation files inside the
selected project; it does not fall back to registering global MCP servers or
hooks. Memory, sessions, preferences, learning policies and private key storage
remain in the existing shared HiveMind folder.

## Install from a project folder

If HiveMind is already installed, reuse that installation. For example, with
your shared Hive at `D:\HiveMind`, open CMD in your application's repository root:

```cmd
D:\HiveMind\hivemind.cmd .
```

PowerShell:

```powershell
& 'D:\HiveMind\hivemind.cmd' .
```

On a fresh device, clone the software once, outside your application project:

```powershell
git clone https://github.com/raj45681/HiveMind.git "$env:USERPROFILE\HiveMind"
& "$env:USERPROFILE\HiveMind\hivemind.cmd" .
```

Run those commands from the project to enroll. Linux uses the same shared layout:

```bash
git clone https://github.com/raj45681/HiveMind.git "$HOME/HiveMind"
python3 "$HOME/HiveMind/bootstrap.py" "$PWD"
```

For later projects, run only the second command from each project folder.
Reusing the same HiveMind installation preserves shared memory. A second clone
starts with a separate empty vault; it is not a copy of your personal knowledge.

The wizard asks for clients, optional local recall/indexing, free/cheap/off
learning, model, budget, activity capture and optional project preferences. It
asks privately for a missing OpenRouter key when learning is selected. There is
no global-scope question. Saved legacy user scope is not silently reused; a
user-wide adapter requires the explicit advanced `--scope user` option.

Setup never launches an agent or an inference request. Restart the selected
client in this project and accept its normal workspace and hook trust prompts.
Codex and Grok discover the project files in trusted repositories. Run setup from
the repository root so those files belong to the intended project.

## Folder layout

```text
Shared HiveMind folder/           # One installation on this device
  vault/                         # Shared notes and project knowledge
  runtime/                       # Shared database, recorder and private keys
  hive.local.json                # Device mappings and per-project policies

My App/
  AGENTS.md                      # Managed workflow; other rules preserved
  .hivemind/
    project.json                 # Portable identity; commit this
    README.md                    # Local workflow instructions
    hive.py                      # Portable CLI helper
    hive.cmd                     # Windows helper
    hive                         # Bash helper
    local.json                   # Ignored pointer to the shared installation
    clients/                     # Ignored fallback/launcher connections
  .codex/config.toml             # Selected clients' project MCP settings
  .codex/hooks.json              # Optional project activity capture
  .grok/config.toml              # Grok project MCP settings when selected
  .grok/hooks/hivemind.json       # Optional Grok project capture
```

Only selected clients receive native files. Other clients use their documented
project paths from the [adapter guide](harness-setup.md). Runtime data and API
keys are not copied into the project. Native connection files contain device
paths: rerun setup on another device. Review them before committing them.

The shared vault is a shared knowledge store, not an access-control boundary
between enrolled projects. This change controls where HiveMind activates.

## Work from the project

```cmd
.hivemind\hive.cmd context --query "what remains to do"
.hivemind\hive.cmd resume
.hivemind\hive.cmd handoff
.hivemind\hive.cmd assist-status
.hivemind\hive.cmd setup --configure
```

On Linux or any platform with Python, substitute `python3 .hivemind/hive.py`.
`bash .hivemind/hive` is also available. The helper supplies the enrolled project
ID, runs from the project root and invokes the shared installation's own Python
environment. Other HiveMind commands can be passed through with their usual
arguments. `setup` always targets this project.

Native project MCP adapters cover Codex, Grok, Claude Code, OpenCode, Command
Code, Gemini CLI, Cursor, Copilot CLI, VS Code, Roo Code and Continue. Cline CLI
uses a process-local MCP settings override:

```cmd
.hivemind\hive.cmd launch cline
```

That environment override applies to the child process only. It does not change
your saved environment or Cline IDE extension settings. For Antigravity,
Windsurf and Cline's IDE extension, the current adapter offers project
instructions plus local CLI access instead of a native project MCP connection.
The agent can run `python .hivemind/hive.py context`, `resume` and `handoff` when
it has shell access. Setup reports these limits rather than editing a global file.

## Migrate an existing global installation

Project setup does not remove older global registrations automatically. First
rerun setup for each project you want to keep active. Then, from the shared
HiveMind folder, preview the legacy entries belonging to that installation:

```cmd
.venv\Scripts\python.exe hive.py global-cleanup
```

After inspecting the listed paths, apply that exact migration:

```cmd
.venv\Scripts\python.exe hive.py global-cleanup --apply
```

The command matches the Hive script and home before removing a global MCP entry
or recorder hook. Other MCP servers, login credentials and trust settings remain.
Changed configs receive private backups under `runtime/adapter-backups/`.
Conflicting, malformed or unsupported files are preserved and reported as
`needs-action`. Known aliases of an already handled config remain aliases.
Restart clients after migration; active processes may retain their old connection
until they restart. The shared vault and existing checkpoints are preserved.
