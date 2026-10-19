"""Declarative MCP, project instructions and activity capture adapters.

Configuration is not a claim that a vendor binary has executed successfully.
No adapter launches an agent or bypasses native workspace/hook trust.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib


# id: (display name, CLI aliases, project instructions, native capture)
HARNESS = {
    'codex': ('Codex', ('codex',), 'AGENTS.md', 'hooks'),
    'grok': ('Grok Build', ('grok',), 'AGENTS.md', 'hooks'),
    'antigravity': ('Antigravity', ('agy',), 'GEMINI.md', 'hooks'),
    'claude': ('Claude Code', ('claude',), 'CLAUDE.md', 'hooks'),
    'opencode': ('OpenCode', ('opencode',), 'AGENTS.md', 'plugin'),
    'commandcode': ('Command Code', ('commandcode', 'cmd'), 'AGENTS.md', 'hooks'),
    'gemini': ('Gemini CLI', ('gemini',), 'GEMINI.md', 'hooks'),
    'cursor': ('Cursor', ('cursor', 'cursor-agent'), '.cursor/rules/hivemind.mdc', 'hooks'),
    'copilot': ('GitHub Copilot CLI', ('copilot',), 'AGENTS.md', 'mcp'),
    'vscode': ('VS Code / Copilot', ('code',), '.github/instructions/hivemind.instructions.md', 'mcp'),
    'cline': ('Cline', ('cline',), '.clinerules/hivemind.md', 'mcp'),
    'roo': ('Roo Code', (), '.roo/rules/hivemind.md', 'mcp'),
    'continue': ('Continue', ('cn',), '.continue/rules/hivemind.md', 'mcp'),
    'windsurf': ('Windsurf', ('windsurf',), '.windsurf/rules/hivemind.md', 'mcp'),
}


def executable(name):
    for alias in HARNESS[name][1]:
        found = shutil.which(alias)
        # Command Code's `cmd` must never resolve to the Windows system shell.
        if found and not (alias == 'cmd' and Path(found).name.lower() in {'cmd.exe', 'cmd.com'}):
            return found
    return None


def inventory(home=None):
    home = Path(home or Path.home())
    folders = {'codex': '.codex', 'grok': '.grok', 'antigravity': '.gemini/antigravity',
               'claude': '.claude', 'opencode': '.config/opencode', 'commandcode': '.commandcode',
               'gemini': '.gemini', 'cursor': '.cursor', 'copilot': '.copilot', 'vscode': '.vscode',
               'cline': '.cline', 'continue': '.continue', 'windsurf': '.codeium/windsurf'}
    extensions = home / '.vscode/extensions'
    extension_names = [p.name.lower() for p in extensions.iterdir()] if extensions.is_dir() else []
    rows = []
    for name, (label, _, _, capture) in HARNESS.items():
        cli = executable(name)
        detected = bool(cli or (name in folders and (home / folders[name]).is_dir()))
        if name in {'cline', 'roo', 'continue'}:
            prefix = {'cline': 'saoudrizwan.claude-dev-', 'roo': 'rooveterinaryinc.roo-cline-',
                      'continue': 'continue.continue-'}[name]
            detected |= any(item.startswith(prefix) for item in extension_names)
        rows.append({'id': name, 'name': label, 'detected': detected, 'cli': cli,
                     'capture': capture, 'task_execution': name in {'codex', 'grok', 'antigravity'}})
    return rows


def _safe(path, boundary):
    path, boundary = Path(path), Path(boundary).resolve()
    if not path.resolve().is_relative_to(boundary) or path.is_symlink():
        raise ValueError('Adapter path resolves outside its configuration scope')
    return path


def _write(root, path, text, original, *, preserve_bytes=False):
    from .store import atomic_write
    if text == original:
        return False
    current = (path.read_bytes().decode('utf-8') if preserve_bytes else
               path.read_text(encoding='utf-8-sig')) if path.exists() else None
    if current != original:
        raise ValueError('Configuration changed during setup; rerun to merge the current version')
    if original is not None:
        digest = hashlib.sha256((str(path) + original).encode()).hexdigest()[:20]
        backup = Path(root) / 'runtime/adapter-backups' / (digest + path.suffix)
        if not backup.exists():
            atomic_write(backup, original)
            backup.chmod(0o600)
    atomic_write(path, text)
    return True


def update_json(root, path, mutate, boundary):
    path = _safe(path, boundary)
    original = path.read_text(encoding='utf-8-sig') if path.exists() else None
    try:
        body = json.loads(original) if original else {}
    except ValueError:
        raise ValueError(f'{path.name} is not strict JSON (possibly JSONC); preserved. Merge the generated client-info manually.') from None
    if not isinstance(body, dict):
        raise ValueError('Client configuration must be a JSON object; preserved')
    before = json.dumps(body, sort_keys=True)
    mutate(body)
    if original is not None and json.dumps(body, sort_keys=True) == before:
        return False
    return _write(root, path, json.dumps(body, indent=2, ensure_ascii=False) + '\n', original)


def _server(body, key, value):
    servers = body.setdefault(key, {})
    if not isinstance(servers, dict):
        raise ValueError('Existing MCP server map has an unsupported shape; preserved')
    prior = servers.get('hivemind', {})
    if not isinstance(prior, dict):
        raise ValueError('Existing hivemind entry is not an object; preserved')
    # Keep unrelated native per-server options, but replace obsolete Hive connection fields.
    prior = {k: v for k, v in prior.items() if k not in {'url', 'type', 'transport', 'command', 'args', 'env', 'environment'}}
    servers['hivemind'] = {**prior, **value}


def mcp_target(name, project, home, scope):
    if scope not in {'project', 'user'}:
        raise ValueError('Choose project or user scope')
    if scope == 'project' and name in {'antigravity', 'cline', 'windsurf'}:
        return project / '.hivemind/clients' / (name + '.json'), project
    base = project if scope == 'project' else home
    paths = {'codex': '.codex/config.toml', 'grok': '.grok/config.toml',
             'claude': '.mcp.json' if scope == 'project' else '.claude.json',
             'commandcode': '.mcp.json' if scope == 'project' else '.commandcode/mcp.json',
             'antigravity': '.gemini/config/mcp_config.json', 'gemini': '.gemini/settings.json',
             'cursor': '.cursor/mcp.json', 'copilot': '.mcp.json' if scope == 'project' else '.copilot/mcp-config.json',
             'roo': '.roo/mcp.json', 'continue': '.continue/mcpServers/hivemind.json',
             'opencode': 'opencode.json' if scope == 'project' else '.config/opencode/opencode.json',
             'vscode': '.vscode/mcp.json', 'windsurf': '.codeium/windsurf/mcp_config.json',
             'cline': '.cline/data/settings/cline_mcp_settings.json'}
    if name == 'roo':
        base = project
    if name == 'vscode' and scope == 'user':
        return home / '.copilot/mcp-config.json', home
    if name == 'cline' and scope == 'user':
        roaming = Path(os.environ.get('APPDATA', str(home / 'AppData/Roaming'))) if home == Path.home().resolve() else home / 'AppData/Roaming'
        for storage in (roaming / 'Code/User/globalStorage', home / '.config/Code/User/globalStorage'):
            candidate = storage / 'saoudrizwan.claude-dev/settings/cline_mcp_settings.json'
            if candidate.exists() or candidate.parent.is_dir():
                return candidate, home
    return base / paths[name], base


def update_toml_mcp(root, target, command, boundary):
    """Manage one named table, preserving all TOML outside our marked block."""
    target = _safe(target, boundary)
    original = target.read_bytes().decode('utf-8') if target.exists() else None
    current = original or ''
    parsed = tomllib.loads(current.lstrip('\ufeff'))
    begin, end = '# HIVEMIND:MCP:BEGIN', '# HIVEMIND:MCP:END'
    if current.count(begin) != current.count(end) or current.count(begin) > 1:
        raise ValueError('Malformed HiveMind TOML markers; existing configuration preserved')
    servers = parsed.get('mcp_servers', {})
    if not isinstance(servers, dict):
        raise ValueError('Unsupported MCP table shape; existing configuration preserved')
    entry = servers.get('hivemind')
    if begin not in current and entry is not None:
        if isinstance(entry, dict) and entry.get('command') == command[0] and entry.get('args') == command[1:]:
            return False
        raise ValueError('An unmanaged hivemind MCP table exists; preserved. Merge client-info manually.')
    newline = '\r\n' if '\r\n' in current else '\n'
    block = newline.join((begin, '[mcp_servers.hivemind]',
        'command = ' + json.dumps(command[0], ensure_ascii=False),
        'args = ' + json.dumps(command[1:], ensure_ascii=False), end))
    if begin in current:
        first, last = current.index(begin), current.index(end)
        if last < first:
            raise ValueError('Malformed HiveMind TOML markers; existing configuration preserved')
        owned = tomllib.loads(current[first + len(begin):last])
        if not isinstance(entry, dict) or owned != {'mcp_servers': {'hivemind': entry}} or set(entry) != {'command', 'args'}:
            raise ValueError('HiveMind TOML block has custom settings; preserved for manual merge')
        text = current[:first] + block + current[last + len(end):]
    else:
        text = current + (newline if current and not current.endswith('\n') else '') + block + newline
    tomllib.loads(text.lstrip('\ufeff'))
    return _write(root, target, text, original, preserve_bytes=True)


def install_mcp(root, project, name, scope='project', home=None, python=None):
    from .store import atomic_write
    root, project, home = Path(root).resolve(), Path(project).resolve(), Path(home or Path.home()).resolve()
    python = str(python or sys.executable)
    command = [python, str(Path(__file__).resolve().parents[1] / 'hive.py'), '--root', str(root), 'serve']
    if scope == 'project' and name in {'codex', 'grok'}:
        target, boundary = mcp_target(name, project, home, scope)
        changed = update_toml_mcp(root, target, command, boundary)
        return {'status': 'configured', 'scope': 'project', 'path': str(target), 'changed': changed,
                'detail': 'Project MCP configured; restart in this project and grant normal workspace trust'}
    if name in {'codex', 'grok'}:
        # Their supported CLI owns TOML parsing. Registration is user-scoped.
        exe = executable(name)
        if not exe:
            return {'status': 'needs-action', 'detail': 'CLI missing; use client-info to connect manually', 'scope': 'user'}
        target = home / ('.codex/config.toml' if name == 'codex' else '.grok/config.toml')
        if name == 'codex':
            target = Path(os.environ.get('CODEX_HOME', str(home / '.codex'))) / 'config.toml'
        if target.exists():
            original = target.read_text(encoding='utf-8-sig')
            # Back up before the vendor CLI mutates its own configuration.
            digest = hashlib.sha256(original.encode()).hexdigest()[:20]
            backup = root / 'runtime/adapter-backups' / f'{name}-{digest}.toml'
            if not backup.exists():
                atomic_write(backup, original)
                backup.chmod(0o600)
            if name == 'codex':
                entry = tomllib.loads(original).get('mcp_servers', {}).get('hivemind', {})
                if entry.get('command') == command[0] and entry.get('args') == command[1:]:
                    return {'status': 'configured', 'detail': 'MCP configuration already current', 'scope': 'user'}
        added = subprocess.run([exe, 'mcp', 'add', 'hivemind', '--', *command], capture_output=True, text=True, timeout=30)
        if added.returncode:
            raise ValueError('Vendor CLI rejected MCP registration; inspect its MCP settings')
        check = [exe, 'mcp', 'get', 'hivemind'] if name == 'codex' else [exe, 'mcp', 'list']
        listed = subprocess.run(check, capture_output=True, text=True, timeout=30)
        if listed.returncode or 'hivemind' not in listed.stdout.lower():
            raise ValueError('Vendor CLI did not confirm hivemind; inspect its MCP list')
        return {'status': 'configured', 'detail': 'registered and listed by vendor CLI', 'scope': 'user'}
    target, boundary = mcp_target(name, project, home, scope)
    if name == 'opencode' and not target.exists() and target.with_suffix('.jsonc').exists():
        target = target.with_suffix('.jsonc')
    value = {'command': command[0], 'args': command[1:]}
    key = 'mcpServers'
    if name == 'opencode':
        key, value = 'mcp', {'type': 'local', 'command': command, 'enabled': True}
    elif name == 'vscode' and scope == 'project':
        key, value = 'servers', {**value, 'type': 'stdio'}
    changed = update_json(root, target, lambda body: _server(body, key, value), boundary)
    if scope == 'project' and name in {'antigravity', 'cline', 'windsurf'}:
        return {'status': 'launcher' if name == 'cline' else 'cli-fallback', 'scope': 'project',
                'path': str(target), 'changed': changed,
                'detail': 'Use .hivemind/hive.py launch cline for a process-local MCP connection; the IDE adapter remains global-only'
                if name == 'cline' else 'No native project MCP adapter: use .hivemind/hive.py context/resume/handoff; global settings were not changed'}
    return {'status': 'configured', 'detail': 'configuration written; restart and approve MCP in the client',
            'path': str(target), 'changed': changed, 'scope': 'user' if boundary == home else 'project'}


def install_instructions(root, project, name, project_id):
    from .project import managed_block, instructions_pointer
    relative = HARNESS[name][2]
    if relative == 'AGENTS.md':
        return {'path': str(project / relative), 'changed': False}
    path = _safe(project / relative, project)
    original = path.read_text(encoding='utf-8-sig') if path.exists() else None
    body = instructions_pointer(project_id)
    text = managed_block(original or '', body)
    if original is None and relative.endswith('.mdc'):
        text = '---\ndescription: HiveMind project continuity\nalwaysApply: true\n---\n\n' + text
    elif original is None and name == 'vscode':
        text = '---\napplyTo: "**"\n---\n\n' + text
    elif original is None and name == 'continue':
        text = '---\nalwaysApply: true\n---\n\n' + text
    elif original is None and name == 'windsurf':
        text = '---\ntrigger: always_on\n---\n\n' + text
    return {'path': str(path), 'changed': _write(root, path, text, original)}


def command_string(root, agent, python=None):
    args = [str(python or sys.executable), str(Path(__file__).resolve().parents[1] / 'hive.py'),
            '--root', str(Path(root).resolve()), 'assist-hook', '--agent', agent]
    if os.name == 'nt':
        if any('"' in part or '%' in part or '\n' in part or '\r' in part for part in args):
            raise ValueError('Hook paths cannot contain quotes, percent expansions or newlines')
        return ' '.join('"' + part + '"' for part in args)
    return shlex.join(args)


def install_capture(root, agent, project=None, home=None, scope='project', python=None):
    home = Path(home or Path.home()).resolve()
    if scope == 'project' and project is None:
        raise ValueError('Project capture needs a project directory; pass --project or use project setup')
    base = Path(project).resolve() if project is not None and scope == 'project' else home
    if agent == 'opencode':
        from .opencode_plugin import plugin_source
        path = _safe(base / ('.opencode/plugins/hivemind.js' if base != home else '.config/opencode/plugins/hivemind.js'), base)
        original = path.read_text(encoding='utf-8-sig') if path.exists() else None
        if original is not None and not original.startswith('// HiveMind managed activity adapter'):
            raise ValueError('An unrelated hivemind.js plugin exists; preserved')
        changed = _write(root, path, plugin_source(root, python), original)
    else:
        paths = {'codex': '.codex/hooks.json', 'grok': '.grok/hooks/hivemind.json',
                 'claude': '.claude/settings.json', 'commandcode': '.commandcode/settings.json',
                 'gemini': '.gemini/settings.json', 'antigravity': '.agents/hooks.json',
                 'cursor': '.cursor/hooks.json'}
        if agent not in paths:
            return {'status': 'mcp-only', 'detail': 'Agent saves explicit session checkpoints through MCP; no native capture adapter'}
        if agent == 'antigravity' and base == home:
            path = home / '.gemini/config/hooks.json'
        else:
            path = base / paths[agent]
        if agent == 'codex' and scope == 'user' and home == Path.home().resolve():
            path = Path(os.environ.get('CODEX_HOME', str(home / '.codex'))) / 'hooks.json'
            base = path.parent
        events = {
            'codex': ['UserPromptSubmit', 'PostToolUse', 'Stop', 'SessionEnd', 'PostCompact', 'Interrupt'],
            'grok': ['UserPromptSubmit', 'PostToolUse', 'Stop', 'SessionEnd', 'PostCompact', 'StopFailure', 'PostToolUseFailure'],
            'claude': ['UserPromptSubmit', 'PostToolUse', 'Stop', 'SessionEnd', 'PreCompact'],
            'commandcode': ['SessionStart', 'PostToolUse', 'Stop'],
            'gemini': ['BeforeAgent', 'AfterTool', 'AfterAgent', 'SessionEnd', 'PreCompress'],
            'antigravity': ['SessionStart', 'PostToolUse', 'Stop'],
            'cursor': ['beforeSubmitPrompt', 'postToolUse', 'afterAgentResponse', 'stop', 'sessionEnd', 'preCompact'],
        }[agent]
        command = command_string(root, agent, python)

        def mutate(body):
            if agent == 'cursor':
                if body.get('version', 1) != 1:
                    raise ValueError('Unsupported Cursor hooks schema; preserved')
                body['version'] = 1
            hooks = body.setdefault('hivemind', {}) if agent == 'antigravity' else body.setdefault('hooks', {})
            if not isinstance(hooks, dict):
                raise ValueError('Existing hooks have an unsupported shape; preserved')
            for event in events:
                groups = hooks.setdefault(event, [])
                if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
                    raise ValueError('Existing hook handlers have an unsupported shape; preserved')
                flat = agent == 'cursor'
                if not flat and any(not isinstance(group.get('hooks', []), list) or
                        any(not isinstance(handler, dict) for handler in group.get('hooks', [])) for group in groups):
                    raise ValueError('Existing hook commands have an unsupported shape; preserved')
                handlers = groups if flat else [h for group in groups for h in group.get('hooks', [])]
                if any(h.get('command') == command for h in handlers):
                    continue
                event_command = command + ' --event ' + event if agent == 'antigravity' else command
                if any(h.get('command') == event_command for h in handlers):
                    continue
                handler = {'type': 'command', 'command': event_command, 'timeout': 5000 if agent == 'gemini' else 5}
                groups.append(handler if flat else {'hooks': [handler]})
        changed = update_json(root, path, mutate, base)
    return {'status': 'configured', 'path': str(path), 'changed': changed,
            'detail': 'Restart client and review/trust hooks or plugin using native controls; no inference run by setup'}


def install(root, project, project_id, names, scope='project', capture=False, home=None, python=None):
    if scope not in {'project', 'user'}:
        raise ValueError('Choose project or user scope')
    results = {}
    for name in dict.fromkeys(names):
        if name not in HARNESS:
            raise ValueError('Unknown harness: ' + name)
        result = {}
        for part, operation in (
            ('mcp', lambda: install_mcp(root, project, name, scope, home, python)),
            ('instructions', lambda: install_instructions(root, Path(project).resolve(), name, project_id)),
            ('capture', lambda: install_capture(root, name, project, home, scope, python) if capture else
             {'status': 'off', 'detail': 'Learning disabled or native capture not selected'}),
        ):
            try:
                result[part] = operation()
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                result[part] = {'status': 'needs-action', 'detail': str(exc)[:300]}
        result['status'] = 'needs-action' if any(x.get('status') == 'needs-action' for x in result.values()) else 'configured'
        result['task_execution'] = name in {'codex', 'grok', 'antigravity'}
        results[name] = result
    return results
