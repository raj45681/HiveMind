# Implementation references

Checked 2026-09-25. Installed CLI help was used to confirm executable flags.

- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Grok headless execution](https://docs.x.ai/build/cli/headless-scripting)
- [Grok MCP](https://docs.x.ai/build/features/mcp-servers)
- [Grok project rules and folder trust](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/12-project-rules.md)
- [Antigravity headless execution](https://antigravity.google/docs/cli/headless/)
- [Antigravity MCP](https://antigravity.google/docs/mcp)
- [Official MCP Python SDK, supported v1 maintenance branch](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [Obsidian vaults](https://obsidian.md/help/vault)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

The project pins MCP SDK 1.30.0 to the supported v1 API used here. The main SDK has
a newer v2 API; upgrades require adapting and rerunning the protocol tests.
