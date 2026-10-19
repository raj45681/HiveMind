# HiveMind managed project CLI. Shared state stays in the configured Hive home.
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    project = Path(__file__).resolve().parent.parent
    local = json.loads((project / '.hivemind/local.json').read_text(encoding='utf-8-sig'))
    manifest = json.loads((project / '.hivemind/project.json').read_text(encoding='utf-8-sig'))
    root = Path(local['hive_home']).resolve()
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file() or not (root / 'hive.py').is_file():
        raise ValueError('Shared HiveMind installation is unavailable; rerun its installer for this project')
    args = sys.argv[1:]
    if not args:
        args = ['--help']
    name, rest = args[0], args[1:]
    env = os.environ.copy()
    if name == 'launch':
        if not rest or rest[0] != 'cline':
            raise ValueError('Use launch cline; other clients read their project settings directly')
        settings = project / '.hivemind/clients/cline.json'
        if not settings.is_file():
            raise ValueError('Run setup --harness cline first')
        executable = shutil.which('cline')
        if not executable:
            raise ValueError('Cline CLI is not installed or not on PATH')
        env['CLINE_MCP_SETTINGS_PATH'] = str(settings)
        command = [executable, *rest[1:]]
    elif name == 'setup':
        command = [str(python), str(root / 'bootstrap.py'), str(project), *rest]
    else:
        if name in {'context', 'resume', 'handoff', 'session-start', 'assist-events', 'client-info',
                    'code-query', 'code-index', 'code-setup', 'recovery-enable', 'recovery-disable', 'recovery-prune'}:
            args = [name, manifest['project'], *rest]
        elif name in {'assist-status', 'assist-run', 'review-dashboard', 'memory-audit'} and '--project' not in rest:
            args += ['--project', manifest['project']]
        command = [str(python), str(root / 'hive.py'), '--root', str(root), *args]
    return subprocess.run(command, cwd=project, env=env).returncode


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print('HiveMind project: ' + str(exc), file=sys.stderr)
        sys.exit(1)
