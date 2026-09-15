from __future__ import annotations

import sys

_MUTEX_NAME = "DictationAppSingleInstance"
_ERROR_ALREADY_EXISTS = 183


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
    return True
