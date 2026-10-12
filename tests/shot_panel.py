"""Снимок панели статуса через PrintWindow: без гонки за верхнее окно.

Запуск: `.venv\\Scripts\\python.exe tests\\shot_panel.py [папка]`

Обычный снимок экрана ловит то окно, что оказалось сверху: панель
`overrideredirect` спорит за верх с чужими окнами и часто проигрывает.
`PrintWindow` рисует содержимое конкретного окна в память независимо от того,
видно его на экране или нет.

Что снимается: `panel_recording.png`, `panel_processing.png`,
`panel_ai_off.png` и `prompt_editor.png`.
"""
from __future__ import annotations

import ctypes
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.prompt import DEFAULT_PROMPT  # noqa: E402
from dictation.prompt_editor import PromptEditor  # noqa: E402
from dictation.status_panel import StatusPanel  # noqa: E402

_PW_RENDERFULLCONTENT = 2
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _capture_hwnd(hwnd: int, width: int, height: int):
    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        raise RuntimeError("GetWindowDC вернул пусто")
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, _PW_RENDERFULLCONTENT)
        if not ok:
            # Часть окон отказывает в RENDERFULLCONTENT: тогда обычная копия.
            gdi32.BitBlt(mem_dc, 0, 0, width, height, window_dc, 0, 0, _SRCCOPY)

        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # сверху вниз, как читает Pillow
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0

        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi32.GetDIBits(
            mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), _DIB_RGB_COLORS
        )
        if lines == 0:
            raise RuntimeError("GetDIBits вернул пусто")
        return Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, window_dc)


def capture(window, path: Path, root) -> None:
    """Снять окно Tk. Ждём в потоке Tk: только он знает актуальные размеры."""
    result: dict = {}
    done = threading.Event()

    def worker() -> None:
        try:
            window.update_idletasks()
            window.deiconify()
            window.update()
            time.sleep(1.0)
            window.update_idletasks()
            hwnd = window.winfo_id()
            parent = ctypes.windll.user32.GetParent(hwnd) or hwnd
            width = window.winfo_width()
            height = window.winfo_height()
            result["image"] = _capture_hwnd(parent, width, height)
            result["size"] = (width, height)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
        finally:
            done.set()

    root.after(0, worker)
    if not done.wait(timeout=20.0):
        raise RuntimeError("снимок не сделан: поток Tk не ответил")
    if "error" in result:
        raise result["error"]
    result["image"].save(path)
    width, height = result["size"]
    print(f"  сохранено {path} ({width}x{height})")


def run_on_ui(root, action) -> None:
    done = threading.Event()

    def wrapper() -> None:
        try:
            action()
        finally:
            done.set()

    root.after(0, wrapper)
    if not done.wait(timeout=5.0):
        raise RuntimeError("действие в потоке Tk не выполнилось")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "shots"
    out.mkdir(parents=True, exist_ok=True)

    panel = StatusPanel(ai_enabled=True)
    root = panel._root
    window = panel._window
    assert root is not None and window is not None

    panel.update("recording")
    time.sleep(0.8)
    capture(window, out / "panel_recording.png", root)

    panel.update("processing", "отправляю текст модели…")
    time.sleep(0.6)
    capture(window, out / "panel_processing.png", root)

    run_on_ui(root, panel._on_toggle_ai)
    panel.update("recording")
    time.sleep(0.6)
    capture(window, out / "panel_ai_off.png", root)

    panel.update("idle")
    panel.stop()
    time.sleep(0.4)

    editor = PromptEditor(load=lambda: DEFAULT_PROMPT + "\n", save=lambda _t: None)
    editor.open()
    time.sleep(1.2)
    ed_window = editor._window
    ed_root = editor._root
    assert ed_window is not None and ed_root is not None
    capture(ed_window, out / "prompt_editor.png", ed_root)
    editor.stop()
    time.sleep(0.4)

    print("готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
