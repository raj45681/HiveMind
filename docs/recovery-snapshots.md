# Opt-in worktree recovery

HiveMind can save private, bounded file snapshots at session start and each saved
checkpoint. This is separate from the normal Git drift fingerprint: the fingerprint
detects a changed worktree, while a recovery snapshot can restore one captured file.
It launches no model and does not change the project's Git HEAD or index.

Enable it once for a mapped Git project on the device holding the HiveMind vault:

```cmd
.venv\Scripts\python.exe hive.py recovery-enable myapp
.venv\Scripts\python.exe hive.py session-start myapp --agent codex --goal "Fix login"
```

Sessions and checkpoints now return `recovery_snapshot` with `saved`, the snapshot
ID, and any exclusion or failure. An existing agent bridge must be restarted to
load new code. A snapshot failure leaves the session/checkpoint intact and reports
the reason. `recovery-disable myapp` stops future captures without deleting old
snapshots. Wrapped CLI sessions and queued write workers also capture their
execution worktree at their normal milestones when the project is enabled.

## Inspect before restoring

```cmd
.venv\Scripts\python.exe hive.py recovery-list --project myapp
.venv\Scripts\python.exe hive.py recovery-diff SESSION-0123456789abcdef-r0
.venv\Scripts\python.exe hive.py recovery-diff SESSION-0123456789abcdef-r0 --path src/app.py
.venv\Scripts\python.exe hive.py recovery-restore SESSION-0123456789abcdef-r0 src/app.py --expected-current CURRENT_SHA256
```

The file preview shows a bounded text diff and `current_sha256` (`missing` for a
deleted file). Pass that value to restore. If the file changes after preview,
restore refuses to write; run the preview again. Restore is per file and creates
an undo record of the prior bytes:

```cmd
.venv\Scripts\python.exe hive.py recovery-undo UNDO-0123456789abcdef --expected-current RESTORED_SHA256
.venv\Scripts\python.exe hive.py recovery-prune myapp --keep 10
.venv\Scripts\python.exe hive.py recovery-prune myapp --keep 10 --drop-undos
```

Undo also refuses if the file changed after restore. A different HEAD, branch,
worktree identity, missing worktree, symlink, or oversized current file blocks
restore. `recovery-prune` explicitly removes older snapshot archives for one
project; at least one is kept.
The last command additionally discards that project's restore undo records.

## Scope and limits

Snapshots include regular tracked and untracked, nonignored files at capture time.
They skip common credential filenames, symlinks, missing files and files over
2 MiB. A snapshot is refused above 2,000 Git-visible paths, 20 MiB of raw
captured data, 25 MiB of compressed archive, or a 200 MiB snapshot store. Undo
bytes have a separate 50 MiB limit. A
partial snapshot reports how many paths were excluded. Filename filtering cannot
prove a repository has no secrets; snapshots are local and **not encrypted**.

Archives live under `runtime/recovery/`, outside the vault and Git. They are not
included in portable HiveMind backups. Recovery restores captured file bytes only:
it does not remove files created later, reset Git staging or commits, restore
ignored files, or undo database, service, or deployment changes. Since independent
agents can edit without passing through HiveMind, it cannot identify which person
or agent made a change before the preview. Review the diff and select each file
deliberately. The hash guard protects edits made *after* that preview.
