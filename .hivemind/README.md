# HiveMind project: hivemind

The managed section in root AGENTS.md enables the shared-memory workflow.
Commit AGENTS.md and project.json to carry instructions and identity with the repo.
Run the installer once on each device to register its MCP bridge and local path.
local.json is device-specific and ignored by Git; no credentials are stored here.

Restart existing agent sessions after installation. Project trust and normal MCP
permission prompts still apply. No background models are launched by installation.
