from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import pyperclip

VK_ALT = 0x12
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9

user32 = ctypes.windll.user32


def capture_foreground_window() -> int | None:
    hwnd = user32.GetForegroundWindow()
    return hwnd if hwnd else None


def restore_foreground_window(hwnd: int | None) -> bool:
    """Return focus to the window that was active when recording started."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Brief Alt press helps SetForegroundWindow from a background process.
    user32.keybd_event(VK_ALT, 0, 0, 0)
    time.sleep(0.01)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_ALT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    return user32.GetForegroundWindow() == hwnd


def _send_ctrl_v() -> None:
    """Send Ctrl+V via Win32 keybd_event (reliable on Windows, any keyboard layout)."""
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def _send_enter() -> None:
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)


def paste_text(
    text: str,
    append_space: bool = True,
    auto_enter: bool = False,
    target_hwnd: int | None = None,
) -> tuple[bool, bool]:
    """Paste text via clipboard. Returns (paste_ok, enter_sent)."""
    if not text:
        return False, False
    if append_space:
        text = text + " "
    try:
        pyperclip.copy(text)
        time.sleep(0.05)
    except Exception:
        return False, False
    try:
        restore_foreground_window(target_hwnd)
        time.sleep(0.05)
        _send_ctrl_v()
        enter_sent = False
        if auto_enter:
            time.sleep(0.12)
            _send_enter()
            enter_sent = True
    except Exception:
        return False, False
    return True, enter_sent
