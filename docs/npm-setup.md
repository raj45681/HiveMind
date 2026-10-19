# npm and npx project setup

Use Node.js 20+, Python 3.11+ and Git. npm packages the installer and Python source;
the same Python implementation still provides memory, checkpoints, models and MCP.
No JavaScript dependencies or install lifecycle scripts are required.

## One command from your project

```sh
npx --yes github:raj45681/HiveMind setup
```

Or keep the launcher in the project:

```sh
npm install --save-dev github:raj45681/HiveMind
npx hivemind setup
```

The package is installed directly from this public GitHub repository. It has not
been published as an npm-registry package. No npm login is needed for this path.
For reproducible installs, npm supports pinning the GitHub spec to a commit:
`github:raj45681/HiveMind#COMMIT`.

`npm install` only installs source and the `hivemind` command. Running `setup`
opens the existing wizard: optional local features, harnesses, project learning
mode/model/budget, activity capture, project preferences and explicit hidden
OpenRouter key entry. Enter reuses an available key or skips missing-key entry.
The terminal is inherited so passwords remain hidden. Setup starts no agent or
model generation. The first run prepares a Python virtual environment and installs
the pinned bridge dependencies; optional features download only when selected.

## Keep one shared Hive

Existing projects keep the home in `.hivemind/local.json`. For a new project, the
launcher reuses a configured source checkout, the last successfully registered
home, or an existing `~/HiveMind` installation. A fresh device defaults to:

```text
~/.hivemind/
  installation.json       # Shared-home pointer; no credentials or activation
  home/                   # Persistent Python software and environment
    vault/                # Shared memory
    runtime/              # Database and protected keys
    hive.local.json       # Project mappings and learning settings
```

The pointer is device metadata, not a global MCP connection or hook. Only enrolled
projects receive activation files. The shared home cannot be placed in
`node_modules`, npm's cache or a fresh subfolder of the application project.
Deleting a package, project `node_modules` or npm cache therefore leaves memory
and the project CLI helper usable. Shared memory is not a security boundary.

If your installation is at a custom path, select it once:

```sh
npx hivemind setup --hive-home "D:\HiveMind"
```

Without a local dependency, use
`npx --yes github:raj45681/HiveMind setup --hive-home "D:\HiveMind"`.
The `HIVEMIND_HOME` environment variable can also hold an absolute shared-home path.
An existing project's pointer takes precedence over automatic discovery; conflicting
explicit homes are rejected to avoid splitting that project's memory. A missing or
malformed existing pointer is reported instead of silently creating an empty vault.

## Options and updates

All normal setup options pass through, including `--configure`, `--no-prompt`,
`--dry-run`, `--learning free`, `--harness claude`, `--capture`, `--with-semantic`
and `--with-graphify`. Use `npx hivemind setup --help` for the launcher help.
The folder defaults to the current working directory; an explicit project folder
can be supplied after `setup`. Paths with spaces are supported.

The launcher prefers the shared home's existing Python. `HIVEMIND_PYTHON` can
name a Python executable; otherwise supported Windows `py` versions, `python3`
and `python` are checked for Python 3.11+. No Python installer is downloaded.

An npm-managed shared home receives updated packaged software on later runs;
vault files, runtime databases, keys and local configuration are preserved.
Source hashes prevent overwriting local software edits. An interrupted copy can
resume from its pending manifest. Older package versions cannot downgrade a
newer shared backend. A Git checkout or other existing unmanaged
installation is reused without overwriting its code: update that checkout normally.
Dry runs do not deploy software or update the remembered home; they require an
existing initialized installation. Restart active harnesses after software updates.

The traditional `hivemind.cmd` and Python installers remain available.

Implementation: [`npm/launcher.cjs`](../npm/launcher.cjs).
npm references: [package specs](https://docs.npmjs.com/cli/v11/using-npm/package-spec/)
and [npm exec](https://docs.npmjs.com/cli/v11/commands/npm-exec/).
