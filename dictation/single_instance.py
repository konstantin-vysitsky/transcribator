from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_MUTEX_NAME = "DictationAppSingleInstance"
# Файл, в котором держатель запрета пишет свой pid. Нужен, чтобы отличить
# «приложение уже работает» от «старая копия ещё выходит».
LOCK_PATH = Path(tempfile.gettempdir()) / "dictation_instance.pid"
_ERROR_ALREADY_EXISTS = 183


def wait_single_instance(timeout_s: float = 15.0) -> bool:
    """Дождаться своей очереди на единственный экземпляр.

    Перезапуск из меню рождает новую копию раньше, чем старая отпустит запрет.
    Раньше новая копия сразу сдавалась и выходила — снаружи это выглядело как
    «перезапустил, а приложения нет». Ждём, пока запрет отпустят; если его
    держит живой экземпляр, не ждём вовсе.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if acquire_single_instance():
            return True
        holder = _read_lock_pid()
        if holder is not None and _is_alive(holder):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def acquire_single_instance() -> bool:
    """Return True if this process should keep running."""
    if sys.platform != "win32":
        return True

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD

    handle = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if not handle:
        return False

    already_running = kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
    if already_running:
        kernel32.CloseHandle(handle)
        return False

    globals()["_instance_mutex_handle"] = handle
    _write_lock_pid()
    return True


def _read_lock_pid() -> int | None:
    try:
        return int(LOCK_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_lock_pid() -> None:
    try:
        LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _is_alive(pid: int) -> bool:
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 258  # WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
