# Local code graphs

HiveMind can use [Graphify](https://github.com/Graphify-Labs/graphify) to map
source-code symbols and relationships. Personal preferences, decisions, solutions
and handoffs continue to live in HiveMind. The graph is a disposable code index.

## One-line setup

From CMD in your project, with HiveMind at `D:\HiveMind`:

```cmd
D:\HiveMind\hivemind.cmd --with-graphify
```

Or pass a project path:

```cmd
D:\HiveMind\hivemind.cmd "D:\Projects\My App" --with-graphify
```

Linux/macOS equivalent after cloning:

```bash
python3 ~/HiveMind/bootstrap.py /path/to/project --with-graphify
```

This optional feature needs **Python 3.12+** and internet for the initial package
download. The base memory system still supports Python 3.11. If an existing HiveMind
environment uses 3.11, upgrade that environment before enabling Graphify. The
installer uses pinned versions and prebuilt wheels; a platform without compatible
wheels receives an explicit unavailable result while memory setup remains usable.

Restart existing agent sessions after enabling it. Setup registers HiveMind with
installed agent CLIs as before. It adds **one MCP tool, `code_query`**, only on devices
with enabled projects. No additional Graphify skill, full MCP server, hook, API key,
watcher or agent process is installed. Existing user instructions are preserved.

## When Graphify runs

| Trigger | Behavior |
| :--- | :--- |
| Setup with `--with-graphify` | Enables indexing for the enrolled project and builds its graph; reruns reuse an unchanged graph. |
| Agent calls `code_query` | Checks source contents, refreshes changed code, then returns a bounded graph answer. |
| Manual `code-index` / `code-query` CLI command | Performs the corresponding local refresh or query; `code-index --force` rebuilds even when unchanged. |
| Editing a file or sending an ordinary message | No background indexing is triggered. Changes are picked up at the next index/query call. |

Typical questions include “What calls this function?”, “Where is authentication
handled?” and “How do these modules connect?”. The agent chooses the tool based on
the installed instructions. After setup, restart existing agent sessions to discover
it. No separate Graphify skill is required for this integration.

For your working style, past decisions and task handoffs, the agent uses HiveMind's
memory/session tools. Having a code graph does not replace those records.

## Automatic use

The managed project instructions tell agents to call `code_query` for code
relationships before broad file reads. Example arguments:

```json
{"project":"my-app","query":"login authenticate","budget_tokens":1000}
```

Each query checks a content fingerprint, rebuilds if source changed, and returns
file/line references and static relationships. Unchanged code reuses its graph.
No background work runs between calls. Instructions guide agents; they do not
guarantee every model will follow them.

The default response ceiling is **4,000 UTF-8 JSON bytes**, roughly 1,000 tokens.
Budgets range from 256 to 2,000 estimated tokens. Whole records are retained; omitted
records are counted. This is a byte limit, not an exact model tokenizer or a promise
of reduced billing. MCP envelopes and the agent's other context are additional.

Graphify extraction and querying make **zero inference calls**. Reading returned
results still uses the coding agent's normal context. The adapter denies network
connections and subprocess launches during parsing/querying. Installation downloads
packages; document/media semantic extraction is not used.

## Scope and storage

- A dedicated environment lives under `runtime/tools/graphify`; its pinned
  dependencies do not change HiveMind's main environment.
- Indexes and temporary source snapshots live under `runtime/code-index` and are
  excluded from Git and portable backups. Temporary snapshots are removed after a
  successful or failed build; an OS-level forced kill can leave a temporary folder.
- Enabled projects and their paths are device-local. Use `--with-graphify` again
  after moving devices. No code or memory is automatically synchronized online.
- In Git projects, selection uses tracked and untracked, non-ignored source files.
  Without Git, a source-extension allowlist and directory exclusions are used;
  `.gitignore` rules are not interpreted in that fallback.
- The initial allowlist covers Python, JavaScript/JSX, TypeScript/TSX, Go, Rust,
  Java, C/C++, C#, Ruby, PHP, Swift, Kotlin, Lua, shell and PowerShell source.
  Other formats are intentionally outside this pilot.
- Hidden paths, symlinks, vaults, runtime folders, dependencies and build outputs
  are excluded. HiveMind's retired `cloud` folder is excluded when indexing HiveMind.
  Markdown, credentials/configuration files, PDFs and media are not selected.
  Source comments/docstrings are part of source; avoid storing secrets in source code.
- Files over 1 MiB or containing NUL bytes are skipped and counted. The pilot caps
  each project at 2,000 selected source files / 20 MiB and each saved graph at 64 MiB.

A source-only snapshot keeps Graphify's resolvers inside the selected corpus.
Excluding manifests/configuration means some alias or framework relationships may
not resolve. Static graphs can also misidentify dynamic calls. **Inspect current
source before editing; graph edges are navigation aids, not proof of behavior.**

## CLI and recovery

Commands below use Windows paths; use `.venv/bin/python` on Linux/macOS:

```cmd
D:\HiveMind\.venv\Scripts\python.exe D:\HiveMind\hive.py code-index my-app
D:\HiveMind\.venv\Scripts\python.exe D:\HiveMind\hive.py code-query my-app "login authenticate" --budget 600
D:\HiveMind\.venv\Scripts\python.exe D:\HiveMind\hive.py code-index my-app --force
```

Rerunning setup is safe. A corrupt graph is rebuilt. Concurrent builds are locked;
a busy index, missing package, timeout or oversized project returns an explicit
`unavailable` status with a native-search fallback. A refresh failure never returns
the old graph as current. Query calls never install packages or retry in a loop.

If setup cannot install Graphify, inspect `runtime/graphify-install.log`; parser
failures are recorded in `runtime/graphify-last-error.log`. These private logs are
excluded from Git. A successful base setup does not imply that an optional graph
is ready: look for `status: ready` in the code setup result.

If a dependency check fails, `runtime/graphify-dependency-check.log` records the
import failure or timeout. Memory operations remain usable during index failures.

The adapter uses the tested Graphify `0.9.67` extraction/query interfaces. Upgrade
the pin only after rerunning integration tests. Windows is exercised locally;
physical Linux/macOS and live paid-agent sessions have not been tested.
