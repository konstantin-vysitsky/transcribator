"""Снимок полос хода работы: распознавание и обработка через ИИ.

Снимает панель в трёх положениях второй ступени — ждёт, идёт, вышло, —
чтобы видеть, что две ступени читаются без подписей.

Запуск: `.venv\\Scripts\\python.exe tests\\snapshot_stages.py [вид] [тема]`
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.status_panel import StatusPanel  # noqa: E402
from tests.snapshot_panel import _print_window, _paste_image  # noqa: E402


def shot(panel: StatusPanel, name: str) -> None:
    window = panel._window
    assert window is not None
    for _ in range(40):
        time.sleep(0.05)
        if window.winfo_viewable() and window.winfo_width() > 10:
            break
    time.sleep(0.35)
    out = Path(__file__).resolve().parent / f"panel-{name}.png"
    if not _print_window(window, out):
        _paste_image(window, out)
    print("снимок:", out)


def main() -> None:
    style = sys.argv[1] if len(sys.argv) > 1 else None
    theme = sys.argv[2] if len(sys.argv) > 2 else "dark"
    panel = StatusPanel(
        ai_enabled=True, ai_ready=True, style=style, theme=theme, scale=1.0
    )
    panel.update("transcribing", "12 сек · ~8 сек осталось", 0.62)
    shot(panel, f"{style or 'default'}-{theme}-stage1")
    panel.mark_transcribed()
    panel.update("processing", "жду ответа модели…")
    time.sleep(0.6)
    shot(panel, f"{style or 'default'}-{theme}-stage2-wait")
    panel.set_ai_finished(True)
    time.sleep(0.4)
    shot(panel, f"{style or 'default'}-{theme}-stage2-done")
    panel.stop()


if __name__ == "__main__":
    main()
