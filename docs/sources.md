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
- [Graphify upstream source and license](https://github.com/Graphify-Labs/graphify)
- [Graphify package](https://pypi.org/project/graphifyy/0.9.67/)

The project pins MCP SDK 1.30.0 to the supported v1 API used here. The main SDK has
a newer v2 API; upgrades require adapting and rerunning the protocol tests.

The optional code adapter pins Graphify 0.9.67 and its tested dependency versions
in requirements-graphify.txt. It calls deterministic extraction and graph querying;
it does not install Graphify's assistant skill or use semantic document extraction.
