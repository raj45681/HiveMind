# Local semantic memory

HiveMind can find a previous solution even if the new question uses different words.
It combines SQLite keyword ranking with locally computed text similarity inside
the existing `memory_search` and `hive_context` tools. No agent skill, new MCP
tool, hosted vector database, API key, or agent inference call is required.

## Setup

From CMD in a project, after cloning HiveMind:

```cmd
D:\HiveMind\hivemind.cmd --with-semantic
```

Or pass a project path:

```cmd
D:\HiveMind\hivemind.cmd "D:\Projects\My App" --with-semantic
```

On Linux/macOS:

```bash
python3 ~/HiveMind/bootstrap.py /path/to/project --with-semantic
```

If the project is already enrolled, run the install once on the **coordinator
device**, the machine holding the authoritative vault:

```cmd
D:\HiveMind\.venv\Scripts\python.exe D:\HiveMind\hive.py semantic-setup
```

Check `hive.py doctor` for `semantic_memory.installed: true`. Restart an active
agent session to load updated server code. Once the model is installed, every
enrolled project uses it automatically; agent instructions do not change.

## What it stores and costs

The one-time installation downloads [FastEmbed 0.8.1](https://pypi.org/project/fastembed/0.8.1/)
and its [BAAI/bge-small-en-v1.5 model](https://qdrant.github.io/fastembed/examples/Supported_Models/)
into `runtime/`. This English-focused model is about 67 MB; dependencies add
more disk space. Searches load it from the local cache only. The model processes
note passages and questions on the coordinator CPU; it sends no vault text to
an embedding service and consumes no Codex, Grok, or Antigravity inference quota.
The returned excerpts still count toward the agent's normal context window,
under HiveMind's existing response budget.

Markdown notes remain the source of truth. `runtime/hivemind.db` holds disposable
vectors, keyed by source revision and model/index version. The first semantic
search indexes the eligible notes; later searches re-embed changed notes and
remove deleted ones. A restored vault can rebuild the index. Source notes are
never rewritten by search. Project scoping is enforced for both keyword and
semantic matches; candidates and archives remain excluded by default.

The semantic index covers durable memory, decisions, and project notes. Generated
task and session views are searched by keywords and supplied separately in the
current session brief. Only the first 48 passages of a very long note are embedded;
keyword search can still find later text. The model is English-focused, so
cross-language recall may be weaker. An absent or broken model falls back to
keyword search; a failure detail is saved in `runtime/semantic-last-error.log`.

You can run both optional features together:

```cmd
D:\HiveMind\hivemind.cmd "D:\Projects\My App" --with-semantic --with-graphify
```
