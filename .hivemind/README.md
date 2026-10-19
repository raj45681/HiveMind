# HiveMind project: hivemind

The managed section in root AGENTS.md enables the shared-memory workflow.
Commit AGENTS.md and project.json to carry instructions and identity with the repo.
Run the installer once on each device to configure project-only activation.
local.json is device-specific and ignored by Git; no credentials are stored here.

Memory, checkpoints, model policies and private keys stay in the shared Hive folder.
Use `python .hivemind/hive.py handoff`, `context --query TOPIC`, `resume`, or
`setup --configure` from the project. Windows also has `.hivemind\hive.cmd`;
Linux has `bash .hivemind/hive`. Commands insert the current project ID.
Cline CLI can use `launch cline`; the MCP override applies only to that process.
A shared vault is not a security boundary between enrolled projects.

Restart existing agent sessions after installation. Project trust and normal MCP
permission prompts still apply. No background models are launched by installation.
