"""One bounded local coalescer; no agent session or model-driven queue polling."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .store import atomic_write


def _read(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def _guard(folder):
    """Short nonblocking OS lock; a crashed process cannot strand it."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'assisted-launch.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        locked = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            pass
        try:
            yield locked
        finally:
            if locked:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def schedule(root, boundary=True):
    root = Path(root).resolve()
    folder = root / 'runtime'
    folder.mkdir(parents=True, exist_ok=True)
    # The journal is already durable. This generation closes the worker-exit race.
    atomic_write(folder / 'assisted-wake.json', json.dumps({'id': uuid.uuid4().hex, 'at': time.time(), 'boundary': boundary}))
    with _guard(folder) as locked:
        if not locked:
            return {'scheduled': False, 'reason': 'coalescing'}
        path = folder / 'assisted-worker.json'
        prior = _read(path)
        if prior.get('until', 0) > time.time() and _alive(prior.get('pid')):
            return {'scheduled': False, 'reason': 'worker already scheduled'}
        token = uuid.uuid4().hex
        args = [sys.executable, str(Path(__file__).resolve().parents[1] / 'hive.py'),
                '--root', str(root), 'assist-run', '--background', '--ticket', token, '--limit', '4']
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True, **options)
        atomic_write(path, json.dumps({'token': token, 'pid': process.pid, 'until': time.time() + 240}))
        # Long-lived MCP hosts reap children without blocking the callback. A
        # short-lived hook can exit immediately; the worker owns its own ticket.
        wait = getattr(process, 'wait', None)
        if callable(wait):
            threading.Thread(target=wait, name='hivemind-recorder-reaper', daemon=True).start()
        return {'scheduled': True}


def background(hive, ticket, limit=4, infer_fn=None, *, clock=time.time, sleep=time.sleep):
    from . import assisted
    if type(limit) is not int or not 1 <= limit <= 4:
        raise ValueError('A background worker may make 1-4 requests')
    folder = hive.runtime
    path = folder / 'assisted-worker.json'
    for _ in range(20):
        current = _read(path)
        if current:
            break
        sleep(.01)
    if current.get('token') != ticket:
        return {'status': 'superseded', 'model_calls': 0}
    cfg = assisted.settings(hive.root)
    started = clock()
    deadline = started + 180
    observed = _read(folder / 'assisted-wake.json')
    quiet_since = clock()
    batch_started = started
    calls, processed, failure = 0, 0, None
    try:
        while clock() < deadline and calls < limit:
            wake = _read(folder / 'assisted-wake.json')
            if wake.get('id') != observed.get('id'):
                observed, quiet_since, batch_started = wake, clock(), clock()
            # Coalesce bursts, but continuous activity cannot postpone a flush forever.
            due = min(quiet_since + cfg['debounce_seconds'], batch_started + cfg['max_delay_seconds']) if observed.get('boundary', True) else batch_started + cfg['max_delay_seconds']
            while clock() < due:
                sleep(min(.5, due - clock()))
                wake = _read(folder / 'assisted-wake.json')
                if wake.get('id') != observed.get('id'):
                    observed, quiet_since = wake, clock()
                    due = min(quiet_since + cfg['debounce_seconds'], batch_started + cfg['max_delay_seconds']) if wake.get('boundary', True) else batch_started + cfg['max_delay_seconds']
            if clock() >= deadline:
                break
            result = assisted.run(hive, limit=1, infer_fn=infer_fn, automatic=True)
            calls += result.get('model_calls', 0)
            processed += result.get('processed', 0)
            failure = result.get('error')
            if result['status'] == 'busy':
                wait = 1
            elif result.get('retry_after') is not None:
                wait = result['retry_after']
            elif result.get('processed'):
                wait = 0  # Drain a bounded backlog; the request ledger enforces spacing.
            else:
                wake = _read(folder / 'assisted-wake.json')
                if wake.get('id') != observed.get('id'):
                    observed, quiet_since = wake, clock()
                    continue
                break
            if calls >= limit or wait > 60 or clock() + wait >= deadline:
                break
            if wait:
                sleep(wait)
        return {'status': 'deferred' if failure else 'ok', 'model_calls': calls, 'processed': processed, 'error': failure}
    finally:
        # Clear before the last generation check. A callback arriving on either
        # side of this boundary is handled here or schedules its own worker.
        for _ in range(50):
            with _guard(folder) as locked:
                if locked:
                    if _read(path).get('token') == ticket:
                        path.unlink(missing_ok=True)
                    break
            sleep(.02)
        wake = _read(folder / 'assisted-wake.json')
        if wake.get('id') != observed.get('id'):
            schedule(hive.root)
