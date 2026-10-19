"""Explicit migration away from this Hive's legacy global activation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tomllib

from .harnesses import _safe, _write, mcp_target
from .store import atomic_write


def _owned(entry, root, operation):
    if not isinstance(entry, dict):
        return False
    command = entry.get('command', [])
    try:
        args = list(command) if isinstance(command, list) else shlex.split(command)
        supplied = entry.get('args', [])
        if not isinstance(supplied, list) or not all(isinstance(arg, str) for arg in supplied):
            return False
        args += supplied
        if operation not in args:
            return False
        scripts = {root / 'hive.py', Path(__file__).resolve().parents[1] / 'hive.py'}
        if not any(Path(arg).resolve() in scripts for arg in args if isinstance(arg, str) and not arg.startswith('-')):
            return False
        if '--root' in args:
            return Path(args[args.index('--root') + 1]).resolve() == root
        return root == Path(__file__).resolve().parents[1]
    except (ValueError, TypeError, IndexError, OSError):
        return False


def _remove_toml(original):
    parsed = tomllib.loads(original.lstrip('\ufeff'))
    desired = copy.deepcopy(parsed)
    del desired['mcp_servers']['hivemind']
    if not desired['mcp_servers']:
        del desired['mcp_servers']
    headers = list(re.finditer(r'(?m)^[ \t]*(\[[^\r\n]*\])[ \t]*(?:#[^\r\n]*)?\r?$', original))
    spans = []
    for index, match in enumerate(headers):
        try:
            table = tomllib.loads(match.group(1) + '\n__scope_probe__ = true')
            ours = 'hivemind' in table.get('mcp_servers', {})
        except (ValueError, TypeError):
            ours = False
        if ours:
            spans.append((match.start(), headers[index + 1].start() if index + 1 < len(headers) else len(original)))
    if not spans:
        raise ValueError('Inline or unsupported global TOML entry; remove hivemind manually after reviewing client-info')
    result = original
    for start, end in reversed(spans):
        result = result[:start] + result[end:]
    # Markers are comments only. Remove markers left by a managed global block.
    result = re.sub(r'(?m)^# HIVEMIND:MCP:(?:BEGIN|END)\r?\n?', '', result)
    actual = tomllib.loads(result.lstrip('\ufeff'))
    if actual.get('mcp_servers') == {}:
        del actual['mcp_servers']
    if actual != desired:
        raise ValueError('Could not isolate only the global hivemind table; configuration preserved')
    return result


def _remove_hooks(body, root, key='hooks'):
    changed = False
    hooks = body.get(key, {})
    if not isinstance(hooks, dict):
        return False
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        remaining = []
        for group in groups:
            if not isinstance(group, dict):
                remaining.append(group)
                continue
            handlers = group.get('hooks')
            if isinstance(handlers, list):
                kept = [handler for handler in handlers if not _owned(handler, root, 'assist-hook')]
                if len(kept) != len(handlers):
                    changed = True
                    if kept:
                        remaining.append({**group, 'hooks': kept})
                else:
                    remaining.append(group)
            elif _owned(group, root, 'assist-hook'):
                changed = True
            else:
                remaining.append(group)
        hooks[event] = remaining
    return changed


def cleanup(root, apply=False, home=None):
    """Preview by default. Apply uses fresh-file checks and private backups."""
    root, home = Path(root).resolve(), Path(home or Path.home()).resolve()
    codex = Path(os.environ.get('CODEX_HOME', str(home / '.codex'))) if home == Path.home().resolve() else home / '.codex'
    grok = Path(os.environ.get('GROK_HOME', str(home / '.grok'))) if home == Path.home().resolve() else home / '.grok'
    targets = {codex / 'config.toml': 'toml', grok / 'config.toml': 'toml',
               codex / 'hooks.json': 'hooks', grok / 'hooks/hivemind.json': 'hooks'}
    for name in ('antigravity', 'claude', 'commandcode', 'opencode', 'gemini', 'cursor', 'copilot', 'vscode', 'cline', 'continue', 'windsurf'):
        target, _ = mcp_target(name, root, home, 'user')
        targets[target] = 'json'
    for folder in ('antigravity', 'antigravity-cli'):
        targets[home / '.gemini' / folder / 'mcp_config.json'] = 'json'
    for relative in ('.claude/settings.json', '.commandcode/settings.json', '.cursor/hooks.json', '.gemini/config/hooks.json'):
        targets[home / relative] = 'json'
    targets[home / '.config/opencode/opencode.jsonc'] = 'json'
    plain = {path.resolve() for path in targets if path.exists() and not path.is_symlink()}
    results = []
    for path, kind in targets.items():
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() and path.resolve() in plain:
            continue  # The known canonical config is handled once; leave its alias intact.
        try:
            boundary = codex if path.is_relative_to(codex) else grok if path.is_relative_to(grok) else home
            _safe(path, boundary)
            original = path.read_bytes().decode('utf-8')
            if kind == 'toml':
                body = tomllib.loads(original.lstrip('\ufeff'))
                entry = body.get('mcp_servers', {}).get('hivemind')
                if not _owned(entry, root, 'serve'):
                    continue
                text = _remove_toml(original)
            else:
                body = json.loads(original.lstrip('\ufeff'))
                if not isinstance(body, dict):
                    raise ValueError('Global config is not an object; preserved')
                if kind == 'hooks':
                    changed = _remove_hooks(body, root)
                else:
                    servers = body.get('mcp' if path.name.startswith('opencode.') else 'mcpServers', {})
                    changed = isinstance(servers, dict) and _owned(servers.get('hivemind'), root, 'serve')
                    if changed:
                        del servers['hivemind']
                    changed = _remove_hooks(body, root) or changed
                    if path == home / '.gemini/config/hooks.json':
                        changed = _remove_hooks(body, root, 'hivemind') or changed
                if not changed:
                    continue
                text = json.dumps(body, indent=2, ensure_ascii=False) + '\n'
            if apply:
                _write(root, path, text, original, preserve_bytes=True)
            results.append({'path': str(path), 'kind': kind, 'status': 'removed' if apply else 'planned'})
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            results.append({'path': str(path), 'status': 'needs-action', 'error': str(exc)[:240]})
    plugin = home / '.config/opencode/plugins/hivemind.js'
    if plugin.exists() or plugin.is_symlink():
        try:
            _safe(plugin, home)
            original = plugin.read_bytes().decode('utf-8')
            match = re.search(r'^const command = (\[[^\n]+\]);$', original, re.M)
            entry = {'command': json.loads(match.group(1))} if match else {}
            if original.startswith('// HiveMind managed activity adapter') and _owned(entry, root, 'assist-hook'):
                if apply:
                    digest = hashlib.sha256((str(plugin) + original).encode()).hexdigest()[:20]
                    backup = root / 'runtime/adapter-backups' / (digest + '.js')
                    if not backup.exists():
                        atomic_write(backup, original)
                        backup.chmod(0o600)
                    # One known file, never a directory or an unreviewed symlink.
                    _safe(plugin, home)
                    if plugin.read_bytes().decode('utf-8') != original:
                        raise ValueError('Global plugin changed during migration; preserved')
                    plugin.unlink()
                results.append({'path': str(plugin), 'kind': 'plugin', 'status': 'removed' if apply else 'planned'})
        except (OSError, ValueError) as exc:
            results.append({'path': str(plugin), 'status': 'needs-action', 'error': str(exc)[:240]})
    return {'scope': 'project', 'applied': apply, 'changes': results,
            'ok': not any(item['status'] == 'needs-action' for item in results),
            'backups': str(root / 'runtime/adapter-backups'), 'model_calls': 0,
            'note': 'Only matching HiveMind entries are removed; other servers, hooks, login and trust settings are preserved. Restart clients.'}
