"""Живая проверка панели: снимок экрана сразу после первой вести о записи.

Панель появляется поверх всего, поэтому снимок делается не по её окну, а
по экрану: то, что видит человек в первые доли секунды, видно только так.

Запуск: `.venv\\Scripts\\python.exe tests\\live_reset_shot.py`
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.status_panel import StatusPanel  # noqa: E402


def main() -> None:
    ctypes.windll.user32.SetProcessDPIAware()
    panel = StatusPanel(ai_enabled=True, ai_ready=True, theme="dark")
    # Прошлая диктовка дошла до конца: обе полосы заполнены.
    panel.update("transcribing", "12 сек · ~8 сек осталось", 1.0)
    panel.mark_transcribed()
    panel.update("processing", "жду ответа модели…")
    panel.set_ai_finished(True)
    time.sleep(0.6)
    print("до сброса:", panel.page_state()["transcribe_progress"])
    out = Path(__file__).resolve().parent

    # Новая диктовка — ровно то, что делает main.py при нажатии.
    panel.reset_session()
    panel.update("recording")
    panel.flush()
    window = panel._window
    if window is not None:
        time.sleep(0.05)
        x, y = window.winfo_rootx(), window.winfo_rooty()
        w, h = window.winfo_width(), window.winfo_height()
        ImageGrab.grab(bbox=(x - 20, y - 20, x + w + 20, y + h + 20),
                       all_screens=True).save(out / "live-panel-recording.png")
        print("снимок:", out / "live-panel-recording.png")
        print("состояние:", panel.page_state()["transcribe_progress"],
              panel.page_state()["ai_progress"])
    panel.stop()


if __name__ == "__main__":
    main()
