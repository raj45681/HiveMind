# Guided client and project setup

Run `hivemind.cmd` in a project on Windows, or use `setup.sh` on Linux. From the
HiveMind folder, `python bootstrap.py /path/to/project` and `python hive.py setup
/path/to/project` use the same wizard. Use the bundle's virtualenv Python after
installation. No agent or inference request is launched by setup.

The initial local feature menu selects core memory, semantic recall and/or code
graphs. Each new project then chooses its clients,
learning mode, model and request budget, native activity capture, automatic or
manual inference, and optional purpose and working preference. Blank optional
answers save nothing. Project preferences remain scoped to that project.

Free learning shows six numbered choices: the automatic free router and five
named free models. Pick a number or enter a custom `:free` model ID. The selected
model leads a free fallback pool, with three HTTP attempts total and short
backoff that honors `Retry-After`. Existing free configurations receive the
default pool automatically. See the [model list and retry behavior](assisted-learning.md#model-budgets-and-failure-behavior).

Interactive setup always explicitly asks for the OpenRouter key when Free or
Cheap learning is enabled, including saved projects and existing credentials.
Input is hidden. Enter keeps an existing key; with no key, Enter skips entry
and activity remains queued. A pasted key is stored privately for the shared
Hive installation, so this step needs no special flag. `--no-prompt`, dry runs
and noninteractive setup never request a key. Learning off skips the step unless
`--set-key` explicitly requests it in an interactive terminal. Keys
are never accepted as command arguments, included in client settings, or saved
in the vault. Environment `OPENROUTER_API_KEY` takes precedence over storage;
setup reports when this override remains active after a new key is saved.
Windows storage uses DPAPI for the current Windows user. Linux uses a private
0700 directory and 0600 file under `runtime/secrets/`. Move devices by entering
the key again. Core memory works without any API key.

## Adapter capabilities

All entries below configure stdio MCP and arrange project instructions. The
MCP bridge is tested separately from native client behavior. An installed config
is reported **configured**, not proof of an end-to-end vendor session.

| Client ID | MCP settings, default scope | Native activity capture | Hive headless worker |
| --- | --- | --- | --- |
| `codex` | Project `.codex/config.toml` | Project `.codex/hooks.json`: prompts, tools, final response and boundaries | Yes |
| `grok` | Project `.grok/config.toml` | Project `.grok/hooks/hivemind.json`: prompts, tools, final response and boundaries | Yes |
| `antigravity` | Project instructions + local CLI fallback; no global MCP write | Project `.agents/hooks.json`: tools and lifecycle | Yes |
| `claude` | Project `.mcp.json`; user `~/.claude.json` | `.claude/settings.json`: prompts, tools, available final response and boundaries | Manual client |
| `opencode` | Project `opencode.json`; user `~/.config/opencode/opencode.json` | Local plugin: visible user/assistant text batched at session idle | Manual client |
| `commandcode` | Project `.mcp.json`; user `~/.commandcode/mcp.json` | `.commandcode/settings.json`: tools/lifecycle; chat checkpoint via MCP | Manual client |
| `gemini` | `.gemini/settings.json` | BeforeAgent / AfterTool / AfterAgent and boundaries | Manual client |
| `cursor` | `.cursor/mcp.json` | `.cursor/hooks.json`: prompts, tools, visible final response and boundaries | Manual client |
| `copilot` | Project `.mcp.json`; user `~/.copilot/mcp-config.json` | Explicit MCP checkpoints | Manual client |
| `vscode` | Project `.vscode/mcp.json`; user Copilot global config | Explicit MCP checkpoints | Manual client |
| `cline` | Project `.hivemind/clients/cline.json` through `hive.py launch cline`; IDE uses local CLI fallback | Explicit checkpoints | Manual client |
| `roo` | `.roo/mcp.json` | Explicit MCP checkpoints | Manual client |
| `continue` | `.continue/mcpServers/hivemind.json` | Explicit MCP checkpoints | Manual client |
| `windsurf` | Project instructions + local CLI fallback; no global MCP write | Explicit CLI checkpoints | Manual client |

Project scope is strict: no adapter falls back to a user-wide file. Codex and
Grok MCP settings and hooks stay inside the project, subject to normal trust.
The wizard does not ask for global scope or reuse a legacy saved global choice.
The advanced `--scope user` option is an explicit opt-in for existing user-wide
adapters; Roo still uses project scope. Cline CLI can be started through the
project helper with a child-only `CLINE_MCP_SETTINGS_PATH` override. Its IDE
extension, Antigravity and Windsurf use project instructions and local CLI access
when no native project-only MCP adapter is available. See the
[installation and migration guide](project-installation.md).

Only Codex, Grok Build and Antigravity have verified Hive task execution
adapters. The other clients can continue the project interactively using MCP.
Automatic quota detection, launching the next harness and transferring hidden
model state are not provided. Shared checkpoints and source files carry the
continuity. Source agents must expose an event or save a checkpoint first.

## Repeatable commands

```powershell
# Guided project setup; change a saved project with --configure.
.\.venv\Scripts\python.exe hive.py setup 'D:\Projects\My App'

# Existing key; enable free automatic learning for one project.
.\.venv\Scripts\python.exe hive.py setup 'D:\Projects\My App' --no-prompt --harness claude --harness opencode --learning free --daily-limit 100 --capture

# Inspect adapters without modifying settings or starting clients.
.\.venv\Scripts\python.exe hive.py harnesses

# Preview; no client writes, key entry or model calls.
.\.venv\Scripts\python.exe hive.py setup 'D:\Projects\My App' --dry-run --harness all

# Explicit paid opt-in, with conservative per-project spending reservations.
.\.venv\Scripts\python.exe hive.py setup 'D:\Projects\My App' --no-prompt --harness cursor --learning cheap --model provider/model-id --daily-limit 50 --daily-usd 0.10 --prompt-price 0.10 --completion-price 0.40 --capture
```

Replace the paid model placeholder with an available OpenRouter model supporting
structured output. Price ceilings are dollars per million tokens. Provider
availability can change; no particular paid model is hardcoded. Free mode pins
both price ceilings to zero and never falls back to paid inference.

The device request cap defaults to 900/rolling 24 hours. The existing
`assist-enable PROJECT --daily-limit 1000` command sets the shared device cap;
project setup changes only that project's limit. Both apply, and other apps
can use the same OpenRouter account allowance. Cheap mode also reserves a
conservative input/output cost before each attempt, retaining the reservation
on uncertain failures and reconciling known successful usage. Account-level
billing limits remain controlled by OpenRouter.

Choices live in ignored `hive.local.json`: `project_onboarding` and
`assisted_learning.project_settings`. Changing one project preserves others.
`--learning off` stops that project's future capture/inference; `--no-capture`
stops native activity even if user hooks are still installed, while explicit
MCP checkpoint learning can continue. `--manual-learning` records without
automatically waking inference. `--no-prompt` never asks for a key or opts a
fresh project into inference without explicit flags. Reruns reuse saved choices.

Adapters preserve unrelated servers, settings and project instructions. Changed
client files are backed up under `runtime/adapter-backups/`. Malformed JSON,
JSONC with comments and symlink escapes are preserved and reported as needing
action. Use `client-info PROJECT` to merge the connection manually in those
cases. Native hooks/plugins still require the client's normal restart/trust.
No trust switches or agent permissions are changed.

See [assisted learning](assisted-learning.md) for redaction, source-backed review,
queue limits, retries and provisional progress. The model never promotes its
own tastes or lessons and does not edit personality files.

Vendor formats checked 2026-10-19: [Codex](https://learn.chatgpt.com/docs/hooks),
[Grok Build](https://docs.x.ai/build/features/hooks),
[Claude Code](https://code.claude.com/docs/en/hooks),
[OpenCode](https://opencode.ai/docs/plugins/),
[Command Code](https://commandcode.ai/docs/hooks),
[Gemini CLI](https://geminicli.com/docs/hooks/reference/),
[Antigravity](https://www.antigravity.google/docs/hooks),
[Cursor](https://cursor.com/docs/hooks),
[Copilot / VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers),
[Continue](https://docs.continue.dev/customize/deep-dives/mcp),
[Cline](https://github.com/cline/cline/blob/main/docs/mcp/mcp-overview.mdx),
[Roo Code](https://roocodeinc.github.io/Roo-Code/features/mcp/using-mcp-in-roo/),
[Windsurf](https://docs.devin.ai/desktop/cascade/mcp).
