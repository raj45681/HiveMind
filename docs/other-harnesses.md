# Use HiveMind with another agent harness

HiveMind's memory, sessions, tasks, and messages use a local MCP server. A client
does not need to be Codex, Grok Build, or Antigravity to use those tools. It must
support **stdio MCP** and allow you to give it project instructions.

First enroll the project with the normal HiveMind setup. Choose **5 · Other MCP
client** on the first interactive run, or pass `--other-client` when running the
setup command explicitly. A working bridge is a successful setup even if none of
the three recognized CLIs is installed. Then, from the
HiveMind folder on the device that holds its memory, print the exact local
connection details:

```cmd
.venv\Scripts\python.exe hive.py client-info myproject
```

On Linux, use `.venv/bin/python hive.py client-info myproject`.
The result contains the `hivemind` server name, executable command, arguments,
project ID, and the project's `AGENTS.md` path. Add that stdio command to your
client's MCP settings using its own configuration format. HiveMind cannot infer
or safely edit every client's settings. Restart the client and confirm that it
lists the HiveMind tools.

The default `full` profile exposes memory, sessions, task claims, messages, and
optional Graphify. For a client that only needs shared memory and handoffs, run
`hive.py client-info myproject --profile memory`; its stdio args expose eleven
memory, review, and session tools. Task and message coordination requires the full profile.
The client-specific `--profile` does not change other clients. To make memory
the default for this HiveMind device, rerun setup with `--tool-profile memory`.

Make the client load the managed HiveMind workflow in the project's `AGENTS.md`.
If it does not read that file automatically, reference or copy the managed block
into its project-instruction feature. The MCP connection gives the client tools;
the instructions tell it when to retrieve and save context. Each harness still
decides whether to follow those instructions and may require its own trust prompt.

Give the harness a stable lowercase ID such as `cursor`, `claude`, or `aider`:

```text
hive_context(project="myproject", agent="cursor", query="current task")
session_start(project="myproject", agent="cursor", goal="Finish the current task")
```

Use the same ID for task assignment and claims. The ID can contain lowercase
letters, digits, underscores, and hyphens, start with a letter, and be at most
64 characters. Shared working style and project memory are available even when
there is no client-specific note. An optional `05-Agents/cursor.md` vault note
can hold instructions specific to that harness.
If `hive_context` omits `agent`, it uses the neutral `generic` identity and
returns shared style without assuming Codex-specific instructions.

Automatic registration and headless task execution have verified adapters only
for Codex, Grok Build, and Antigravity. Other clients can use the MCP tools and
claim explicitly assigned tasks interactively. `session-run` can wrap a different
CLI when it is on `PATH` or mapped in local `hive.local.json` under `agents`, but
you must supply that CLI's arguments; the wrapper only captures process exit and
Git state. A successful exit is not proof that the work was completed.
