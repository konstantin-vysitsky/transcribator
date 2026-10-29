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

    Один запрет на всех держится объектом ядра: он исчезает вместе со своим
    владельцем, и после аварийного выхода файл с pid остаётся врать. Поэтому
    «нас опередили» — это не «в файле есть pid», а «по этому pid действительно
    живо приложение диктовки».
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if acquire_single_instance():
            return True
        if _holder_is_dictation():
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def _holder_is_dictation() -> bool:
    """Держит ли запрет именно приложение, а не просто чужой живой pid.

    Файл с pid переживает аварийное падение владельца, а сам pid после
    перезагрузки может достаться другой программе. Проверяем командную строку
    процесса: если это не `dictation.main`, запрет считаем брошенным и не ждём.
    """
    holder = _read_lock_pid()
    if holder is None or not _is_alive(holder):
        return False
    command = _process_command_line(holder)
    if command is None:
        # Командную строку не узнать (нет прав) — верим файлу.
        return True
    return "dictation" in command.lower()


def _process_command_line(pid: int) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import subprocess

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\")"
                ".CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or "").strip()
    return text or None


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
