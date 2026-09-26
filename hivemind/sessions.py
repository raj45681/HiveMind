"""Durable session checkpoints. SQLite is authoritative; Markdown is a recoverable view."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context import AGENTS, validate_project

Item = Annotated[str, Field(min_length=1, max_length=400)]


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(min_length=1, max_length=1200)
    status: Literal['active', 'completed', 'blocked', 'interrupted', 'needs_handoff'] = 'active'
    completed: list[Item] = Field(default_factory=list, max_length=8)
    changed_files: list[Item] = Field(default_factory=list, max_length=20)
    verification: list[Item] = Field(default_factory=list, max_length=8)
    blockers: list[Item] = Field(default_factory=list, max_length=8)
    next_steps: list[Item] = Field(default_factory=list, max_length=8)
    source: Literal['agent', 'worker', 'wrapper'] = 'agent'
    exit_code: int | None = None

    @model_validator(mode='after')
    def evidence(self):
        if not self.summary.strip() or any(not value.strip() for field in
                (self.completed, self.changed_files, self.verification, self.blockers, self.next_steps) for value in field):
            raise ValueError('Checkpoint fields must contain meaningful text')
        if self.status == 'completed' and (not self.completed or not self.verification):
            raise ValueError('Completed sessions require completed work and reported verification evidence')
        if len(self.model_dump_json()) > 12000:
            raise ValueError('Keep checkpoint content below 12000 characters')
        return self


def project_path(root, project):
    config_path = Path(root) / 'hive.local.json'
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
    mapping = config.get('projects', {'hivemind': str(root)})
    path = mapping.get(project)
    return Path(path).resolve() if path else None


def git_snapshot(root, project, baseline=None, workspace=None):
    """Metadata only: no diffs, file contents, remote URLs, or command arguments."""
    mapped = project_path(root, project)
    path = Path(workspace).resolve() if workspace else mapped
    if not path or not path.is_dir():
        return {'available': False, 'reason': 'Project or saved worktree is not available on this device'}
    def git(at, *args):
        # A stdio MCP server owns stdin. Git for Windows can inherit that pipe
        # through its launcher and leave a child holding the request open.
        return subprocess.run(['git', '-C', str(at), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=10,
                              check=True).stdout.decode('utf-8', errors='replace').rstrip('\r\n')
    def common_dir(at):
        value = Path(git(at, 'rev-parse', '--git-common-dir'))
        return (value if value.is_absolute() else at / value).resolve()
    try:
        if not mapped or not mapped.is_dir() or Path(git(mapped, 'rev-parse', '--show-toplevel')).resolve() != mapped:
            return {'available': False, 'reason': 'Mapped project is not the Git root'}
        if common_dir(mapped) != common_dir(path):
            return {'available': False, 'reason': 'Saved worktree is not linked to the mapped project'}
        if Path(git(path, 'rev-parse', '--show-toplevel')).resolve() != path:
            return {'available': False, 'reason': 'Workspace is not the Git root'}
        git_dir = Path(git(path, 'rev-parse', '--absolute-git-dir')).resolve()
        head = git(path, 'rev-parse', '--verify', 'HEAD')
        branch = git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
        # --no-renames keeps one path per status record. All paths are repository-relative.
        records = git(path, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames').split('\0')
        dirty = [entry[3:] for entry in records if entry]
        changed = []
        if baseline and baseline.get('head') and baseline['head'] != head:
            changed = git(path, 'diff', '--name-only', '-z', baseline['head'], head, '--').split('\0')
        # Hash only dirty paths, never the whole repository or its contents in the handoff.
        # Include staged blob IDs so replacing a staged version also changes the fingerprint.
        digest = hashlib.sha256()
        digest.update(git(path, 'diff', '--cached', '--raw', '-z', '--no-renames', '--').encode('utf-8'))
        complete = True
        for entry in sorted(record for record in records if record):
            digest.update(entry.encode('utf-8', errors='replace') + b'\0')
            file = path / entry[3:]
            if file.is_symlink():
                digest.update(b'L' + os.readlink(file).encode('utf-8', errors='replace'))
            elif file.is_file():
                digest.update(b'F' + str(stat.S_IMODE(file.stat().st_mode)).encode('ascii') + b'\0')
                with file.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
            elif not file.exists():
                digest.update(b'D')
            else:
                complete = False  # A dirty submodule/directory cannot be content-fingerprinted here.
                digest.update(b'O')
        observed = sorted(set(p for p in dirty + changed if p))
        return {'available': True, 'head': head, 'branch': branch, 'workspace': str(path),
                'git_dir': str(git_dir),
                'fingerprint': digest.hexdigest(), 'fingerprint_complete': complete,
                'dirty_files': [p for p in dirty[:20] if len(p) <= 400],
                'observed_files': [p for p in observed[:20] if len(p) <= 400],
                'files_omitted': max(0, len(observed) - 20) + sum(len(p) > 400 for p in observed[:20]),
                'attribution': 'Git observations may include pre-existing or concurrent edits; not proof of authorship or correctness.'}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {'available': False, 'reason': 'Git snapshot unavailable (missing Git, unborn HEAD, or command failure)'}


def git_drift(root, project, saved):
    """Compare a saved checkpoint against the same live repository/worktree."""
    if (not saved.get('available') or not saved.get('fingerprint') or not saved.get('workspace')
            or not saved.get('fingerprint_complete')):
        return {'status': 'unverifiable', 'reason': 'Checkpoint has no comparable Git fingerprint'}
    current = git_snapshot(root, project, workspace=saved['workspace'])
    if not current.get('available'):
        return {'status': 'unverifiable', 'reason': current.get('reason', 'Worktree unavailable')}
    if not current.get('fingerprint_complete'):
        return {'status': 'unverifiable', 'reason': 'Current worktree contains dirty paths that cannot be fingerprinted'}
    changed = [key for key in ('head', 'branch', 'git_dir', 'fingerprint') if saved.get(key) != current.get(key)]
    if changed:
        return {'status': 'changed', 'reason': 'Git state changed since checkpoint: ' + ', '.join(changed),
                'current_head': current['head'], 'current_branch': current['branch']}
    return {'status': 'match', 'reason': 'Saved checkpoint matches the current Git state'}


def validate_id(ident):
    if not re.fullmatch(r'SESSION-[a-f0-9]{16}', ident):
        raise ValueError('Invalid session ID')


def start(hive, project, agent, goal, session_id='', workspace=None):
    from .store import utc
    validate_project(project, required=True)
    if agent not in AGENTS or not isinstance(goal, str) or not goal.strip() or len(goal) > 400:
        raise ValueError('Use a supported agent and a goal of 1-400 characters')
    ident = session_id or 'SESSION-' + uuid.uuid4().hex[:16]
    validate_id(ident)
    baseline = git_snapshot(hive.root, project, workspace=workspace)
    payload = Checkpoint(summary='Session started; no verified handoff recorded yet.', next_steps=[goal]).model_dump()
    # A caller-supplied ID makes a retry safe after a lost acknowledgement.
    with hive.connect(write=True) as c:
        old = c.execute('SELECT project,agent,goal FROM sessions WHERE id=?', (ident,)).fetchone()
        if old:
            if (old['project'], old['agent'], old['goal']) != (project, agent, goal):
                raise ValueError('Session ID already belongs to different work')
        else:
            c.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)',
                      (ident, project, agent, goal, utc(), time.time(), json.dumps(baseline), 0))
            c.execute('INSERT INTO checkpoints VALUES (?,?,?,?,?,?)',
                      (ident, 0, json.dumps(payload), json.dumps(baseline), utc(), 'start'))
    result = persist_view(hive, resume(hive, project, ident))
    from .recovery import capture
    try:
        recovery = capture(hive, project, ident, result['session']['revision'], result['session']['git'])
    except Exception as exc:
        recovery = {'saved': False, 'reason': f'Recovery snapshot unavailable: {type(exc).__name__}: {exc}'[:250]}
    if recovery and recovery.get('saved'):
        result['session']['recovery_snapshot'] = {'id': recovery['id'], 'revision': result['session']['revision']}
    return {**result, 'recovery_snapshot': recovery} if recovery is not None else result


def checkpoint(hive, ident, checkpoint, expected_revision, workspace=None):
    from .store import utc
    validate_id(ident)
    payload = Checkpoint.model_validate(checkpoint).model_dump()
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ValueError('Expected revision must be a nonnegative integer')
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with hive.connect() as c:
        session = c.execute('SELECT * FROM sessions WHERE id=?', (ident,)).fetchone()
        last_snapshot = c.execute('''SELECT snapshot FROM checkpoints WHERE session=?
            ORDER BY revision DESC LIMIT 1''', (ident,)).fetchone() if session else None
    if session is None:
        raise ValueError('Session not found')
    previous_workspace = json.loads(last_snapshot['snapshot']).get('workspace') if last_snapshot else None
    target_workspace = workspace or previous_workspace
    snapshot = git_snapshot(hive.root, session['project'], json.loads(session['baseline']), target_workspace)
    if workspace and not snapshot.get('available'):
        raise ValueError(snapshot.get('reason', 'Workspace Git snapshot unavailable'))
    with hive.connect(write=True) as c:
        current = c.execute('SELECT revision FROM sessions WHERE id=?', (ident,)).fetchone()[0]
        last = c.execute('SELECT digest FROM checkpoints WHERE session=? AND revision=?', (ident, current)).fetchone()[0]
        if current != expected_revision:
            if current != expected_revision + 1 or last != digest:
                raise ValueError('Checkpoint changed; resume the session and use its current revision')
        else:
            c.execute('INSERT INTO checkpoints VALUES (?,?,?,?,?,?)',
                      (ident, current + 1, encoded, json.dumps(snapshot), utc(), digest))
            c.execute('UPDATE sessions SET revision=?,updated_at=? WHERE id=?', (current + 1, time.time(), ident))
    result = persist_view(hive, resume(hive, session['project'], ident))
    from .recovery import capture
    try:
        recovery = capture(hive, session['project'], ident, result['session']['revision'], result['session']['git'])
    except Exception as exc:
        recovery = {'saved': False, 'reason': f'Recovery snapshot unavailable: {type(exc).__name__}: {exc}'[:250]}
    if recovery and recovery.get('saved'):
        result['session']['recovery_snapshot'] = {'id': recovery['id'], 'revision': result['session']['revision']}
    return {**result, 'recovery_snapshot': recovery} if recovery is not None else result


def resume(hive, project, ident=''):
    validate_project(project, required=True)
    if ident:
        validate_id(ident)
    with hive.connect() as c:
        row = c.execute('''SELECT s.*,c.payload,c.snapshot,c.created AS checkpoint_at
            FROM sessions s JOIN checkpoints c ON c.session=s.id AND c.revision=s.revision
            WHERE s.project=? COLLATE NOCASE AND (?='' OR s.id=?)
            ORDER BY s.updated_at DESC,s.rowid DESC LIMIT 1''', (project, ident, ident)).fetchone()
    if row is None:
        if ident:
            raise ValueError('Session not found in this project')
        return {'session': None, 'note': 'No structured checkpoint yet. Existing Markdown handoffs remain searchable.'}
    payload = json.loads(row['payload'])
    saved = json.loads(row['snapshot'])
    with hive.connect() as c:
        recovery = c.execute('''SELECT id,revision,created,file_count,excluded_count
            FROM recovery_snapshots WHERE session=? ORDER BY revision DESC LIMIT 1''', (row['id'],)).fetchone()
    return {'session': {'id': row['id'], 'project': row['project'], 'agent': row['agent'], 'goal': row['goal'],
            'status': payload['status'], 'revision': row['revision'], 'created': row['created'],
            'checkpoint_at': row['checkpoint_at'], 'checkpoint': payload, 'git': saved,
            'git_drift': git_drift(hive.root, project, saved),
            'recovery_snapshot': dict(recovery) if recovery else None,
            'path': f"03-Projects/{row['project']}/Sessions/{row['id']}.md"},
            'evidence_policy': 'Verification is agent-reported. Git changes and successful process exit are not proof of completion.'}


def persist_view(hive, result):
    from .store import atomic_write
    session = result['session']
    # Serialize export with checkpoint writers, then read the latest committed revision.
    try:
        with hive.connect(write=True) as c:
            row = c.execute('SELECT payload,snapshot FROM checkpoints WHERE session=? ORDER BY revision DESC LIMIT 1',
                            (session['id'],)).fetchone()
            current = c.execute('SELECT revision FROM sessions WHERE id=?', (session['id'],)).fetchone()[0]
            data, snapshot = json.loads(row['payload']), json.loads(row['snapshot'])
            text = (f"# Session {session['id']}\n\nProject: {session['project']}\nAgent: {session['agent']}\n"
                    f"Revision: {current}\nStatus: {data['status']}\n\nGoal: {session['goal']}\n\n{data['summary']}\n")
            for key, title in [('completed', 'Completed work'), ('changed_files', 'Reported changed files'),
                               ('verification', 'Reported verification'), ('blockers', 'Blockers'), ('next_steps', 'Next steps')]:
                text += f'\n## {title}\n\n' + ('\n'.join('- ' + item for item in data[key]) or 'None reported.') + '\n'
            text += '\n## Observed Git state\n\n```json\n' + json.dumps(snapshot, indent=2, ensure_ascii=False) + '\n```\n'
            text += '\nGenerated view. Use session_checkpoint to update; SQLite retains every checkpoint revision.\n'
            path = hive.note_path(session['path'])
            atomic_write(path, text)
            hive._index_note(c, path)
        return {**result, 'saved': True, 'markdown_saved': True}
    except (OSError, ValueError, sqlite3.Error) as exc:
        # The database checkpoint is already committed and remains resumable/backed up.
        return {**result, 'saved': True, 'markdown_saved': False, 'warning': str(exc)[:250]}
