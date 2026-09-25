"""Optional interactive CLI wrapper; process exit is never treated as verified work."""
import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

from .sessions import project_path
from .store import atomic_write


async def run_session(root, api, project, agent, agent_args, config):
    from .worker import stop_process
    workspace = project_path(root, project)
    if not workspace or not workspace.is_dir():
        raise ValueError('Enroll this project before starting a wrapped session')
    exe = config.get('agents', {}).get(agent) or shutil.which({'codex': 'codex', 'grok': 'grok', 'antigravity': 'agy'}[agent])
    if not exe:
        raise ValueError(f'{agent} is not installed or not on PATH')
    ident = 'SESSION-' + uuid.uuid4().hex[:16]
    await api.call('session_start', project=project, agent=agent,
                   goal='Interactive work in ' + project, session_id=ident)
    env = dict(os.environ, HIVE_SESSION_ID=ident, HIVE_SESSION_PROJECT=project)
    print(f'HiveMind session: {ident}\nThe wrapper records Git state; the agent must save its verified handoff.', flush=True)
    process, exit_code, interrupted, checkpoint_saved = None, None, False, False
    try:
        args = list(agent_args)
        if args[:1] == ['--']:
            args.pop(0)
        process = await asyncio.create_subprocess_exec(exe, *args, cwd=workspace, env=env,
                        **({} if os.name == 'nt' else {'start_new_session': True}))
        exit_code = await process.wait()
    except BaseException:
        interrupted = True
        if process:
            await stop_process(process)
        raise
    finally:
        payload = None
        try:
            # The agent may have checkpointed during execution. Never use the initial revision blindly.
            for attempt in range(3):
                latest = (await api.call('session_resume', project=project, ident=ident))['session']
                payload = dict(latest['checkpoint'])
                payload.update(source='wrapper', exit_code=exit_code)
                if interrupted or exit_code != 0:
                    payload['status'] = 'interrupted'
                    payload['blockers'] = (payload['blockers'] + ['Agent process interrupted or exited unsuccessfully; inspect before resuming.'])[-8:]
                elif payload['status'] == 'active':
                    payload['status'] = 'needs_handoff'
                    payload['next_steps'] = (payload['next_steps'] + ['Review work and save a verified handoff; exit code 0 is not verification.'])[-8:]
                try:
                    saved = await api.call('session_checkpoint', ident=ident, checkpoint=payload,
                                           expected_revision=latest['revision'])
                    checkpoint_saved = saved.get('saved', False)
                    break
                except ValueError as exc:
                    if 'Checkpoint changed' not in str(exc) or attempt == 2:
                        raise
            print(f"HiveMind checkpoint: {ident} ({saved['session']['status']})", flush=True)
        except Exception as exc:
            recovery = Path(root) / 'runtime/session-recovery' / (ident + '.json')
            atomic_write(recovery, json.dumps({'session': ident, 'project': project, 'checkpoint': payload,
                                               'exit_code': exit_code, 'error': str(exc)[:500]}, indent=2))
            print(f'Checkpoint recovery saved to {recovery}; resume the session before retrying.', flush=True)
    return {'session': ident, 'exit_code': exit_code, 'checkpoint_saved': checkpoint_saved,
            'model_calls': 'one explicitly requested CLI session'}
