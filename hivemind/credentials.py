"""Private device credentials. Keys never belong in project configuration."""
import base64
import ctypes
import json
import os
from pathlib import Path

from .store import atomic_write


def _protect(data, decrypt=False):
    """Windows DPAPI binds the saved secret to the current Windows user."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination))
    if not ok:
        raise OSError('Windows could not access the protected OpenRouter credential')
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.data)


def _path(root):
    root = Path(root).resolve()
    path = root / 'runtime' / 'secrets' / 'openrouter.json'
    if not path.resolve().is_relative_to(root / 'runtime'):
        raise ValueError('Credential directory resolves outside the Hive runtime')
    return path


def save_key(root, key):
    key = key.strip()
    if not key or len(key) > 512 or any(char.isspace() for char in key):
        raise ValueError('Enter a nonempty OpenRouter API key without whitespace')
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    protected = os.name == 'nt'
    data = _protect(key.encode()) if protected else key.encode()
    atomic_write(path, json.dumps({'storage': 'dpapi' if protected else 'private-file',
                                  'value': base64.b64encode(data).decode()}) + '\n')
    path.chmod(0o600)
    return {'saved': True, 'storage': 'Windows DPAPI' if protected else 'file mode 0600'}


def get_key(root):
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if key:
        return key
    path = _path(root)
    if not path.exists():
        return ''
    if os.name != 'nt' and (path.stat().st_mode & 0o077 or path.parent.stat().st_mode & 0o077):
        raise ValueError('OpenRouter credential permissions must be private (directory 0700, file 0600)')
    value = json.loads(path.read_text(encoding='utf-8'))
    data = base64.b64decode(value['value'], validate=True)
    if value['storage'] == 'dpapi':
        if os.name != 'nt':
            raise ValueError('Re-enter the OpenRouter key on this device; Windows credentials are user-bound')
        data = _protect(data, decrypt=True)
    elif value['storage'] != 'private-file':
        raise ValueError('Unrecognized OpenRouter credential format')
    return data.decode()


def key_status(root):
    try:
        available = bool(get_key(root))
        return {'available': available, 'source': 'environment' if os.environ.get('OPENROUTER_API_KEY') else
                'private device storage' if available else 'missing'}
    except (OSError, ValueError, KeyError):
        return {'available': False, 'source': 'unreadable; re-enter key with setup'}
