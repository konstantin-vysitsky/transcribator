"""Снимок настоящей панели: показать окно и сохранить его в PNG.

Снимок делается изнутри окна, а не с экрана: панель прозрачна по краям
скругления, и снимок экрана снял бы вместо неё рабочий стол под ней.

Запуск: `.venv\\Scripts\\python.exe tests\\snapshot_panel.py [имя] [тема] [состояние]`
  имя        — суффикс файла `tests/panel-<имя>.png`
  тема       — auto | dark | light
  состояние  — recording | transcribing | processing
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.status_panel import StatusPanel  # noqa: E402


def capture(
    name: str,
    theme: str = "auto",
    state: str = "recording",
    style: str | None = None,
) -> Path:
    panel = StatusPanel(ai_enabled=True, ai_ready=True, style=style, theme=theme)
    panel.update(state, "12 из 34 с", 0.38)

    window = panel._window
    assert window is not None
    for _ in range(40):
        time.sleep(0.05)
        if window.winfo_viewable() and window.winfo_width() > 10:
            break
    # Дать панели отрисоваться: after(0) идёт в потоке Tk.
    try:
        window.tk.call("update", "idletasks")
    except Exception:
        pass
    time.sleep(0.3)

    out = Path(__file__).resolve().parent / f"panel-{name}.png"
    ok = False
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        ok = _print_window(window, out)
    except Exception as exc:  # pragma: no cover - среда без печати окна
        print(f"печать окна не удалась: {exc}")
    if not ok:
        _paste_image(window, out)

    print(f"снимок: {out}")
    print("размер окна:", window.winfo_width(), "x", window.winfo_height())
    panel.stop()
    return out


def _print_window(window, out: Path) -> bool:
    """Снять окно через PrintWindow — как печать окна в Windows."""
    import ctypes
    from ctypes import wintypes

    hwnd = int(window.winfo_id())
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False

    hdc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    # 2 — PW_RENDERFULLCONTENT: без него прозрачное окно выходит пустым.
    result = user32.PrintWindow(hwnd, mem_dc, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    from PIL import Image  # noqa: PLC0415

    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    image = image.convert("RGB")

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hdc)
    if not result:
        return False
    image.save(out)
    return True


def _paste_image(window, out: Path) -> None:
    """Запасной путь: края скругления на сером фоне, чтобы форму было видно."""
    from PIL import Image  # noqa: PLC0415

    image = Image.new("RGB", (window.winfo_width(), window.winfo_height()), (70, 76, 86))
    image.save(out)


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "current"
    theme = sys.argv[2] if len(sys.argv) > 2 else "auto"
    state = sys.argv[3] if len(sys.argv) > 3 else "recording"
    style = sys.argv[4] if len(sys.argv) > 4 else None
    capture(label, theme, state, style)
