from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import pyperclip

VK_ALT = 0x12
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_BACK = 0x08
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9

user32 = ctypes.windll.user32


def capture_foreground_window() -> int | None:
    hwnd = user32.GetForegroundWindow()
    return hwnd if hwnd else None


def window_info(hwnd: int | None) -> tuple[int | None, str, str]:
    """hwnd окна, его заголовок и класс — одной строкой для журнала.

    Нужно измерениям приписки по ходу речи: по номеру окна видно, то ли
    окно на переднем плане, куда печатать. Никогда не падает: без названия
    окна диктовка работать обязана.
    """
    if not hwnd:
        return None, "", ""
    try:
        hwnd = int(hwnd)
        if not user32.IsWindow(hwnd):
            return hwnd, "окно уже закрыто", ""
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        klass = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, klass, 256)
        return hwnd, title.value, klass.value
    except Exception:  # noqa: BLE001 — подпись окна не должна мешать вставке
        try:
            return int(hwnd), "?", "?"
        except Exception:
            return None, "?", "?"


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


def _send_backspaces(count: int) -> None:
    for _ in range(max(0, count)):
        user32.keybd_event(VK_BACK, 0, 0, 0)
        time.sleep(0.002)
        user32.keybd_event(VK_BACK, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.002)


def erase_pasted(count: int, target_hwnd: int | None = None) -> bool:
    """Стереть в поле последнюю вставку — ровно `count` знаков.

    Нужно вставке по ходу речи: потоковая модель может переписать уже
    показанное слово, и прошлый текст в поле заменяется целиком.
    Возвращает False, если стереть не вышло: тогда живой текст дальше
    идёт только в конце, а не латается в поле.
    """
    if count <= 0:
        return True
    try:
        restore_foreground_window(target_hwnd)
    except Exception:
        return False
    _send_backspaces(count)
    return True


def foreground_is(hwnd: int | None) -> bool:
    """То же окно, куда человек диктует, сейчас на переднем плане."""
    return bool(hwnd) and user32.GetForegroundWindow() == hwnd


def press_enter(target_hwnd: int | None = None) -> bool:
    """Нажать Enter в поле диктовки: этим заканчивается автоперевод духа."""
    try:
        restore_foreground_window(target_hwnd)
        time.sleep(0.05)
        _send_enter()
    except Exception:
        return False
    return True


def paste_text(
    text: str,
    append_space: bool = True,
    auto_enter: bool = False,
    target_hwnd: int | None = None,
    only_if_foreground: bool = False,
) -> tuple[bool, bool]:
    """Paste text via clipboard. Returns (paste_ok, enter_sent).

    `only_if_foreground` не отнимает фокус у другого окна: если поле
    диктовки ушло на второй план, вставка не делается вовсе. Нужно
    вставке по ходу речи: иначе вместе с фокусом текст уезжал бы не в
    то окно.
    """
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
        if only_if_foreground and target_hwnd and user32.GetForegroundWindow() != target_hwnd:
            return False, False
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
