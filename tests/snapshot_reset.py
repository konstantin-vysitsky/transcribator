"""Снимок полос сразу после новой диктовки: обе ступени должны быть пусты.

Запуск: `.venv\\Scripts\\python.exe tests\\snapshot_reset.py`
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.status_panel import StatusPanel  # noqa: E402
from tests.snapshot_stages import shot  # noqa: E402


def main() -> None:
    panel = StatusPanel(ai_enabled=True, ai_ready=True, theme="dark")
    # Прошлая диктовка дошла до самого конца: обе полосы заполнены.
    panel.update("transcribing", "12 сек · ~8 сек осталось", 1.0)
    panel.mark_transcribed()
    panel.update("processing", "жду ответа модели…")
    panel.set_ai_finished(True)
    time.sleep(0.5)
    print("до сброса:", panel.page_state()["transcribe_progress"],
          panel.page_state()["ai_progress"])

    # Новая диктовка: то же, что делает main.py при нажатии.
    panel.reset_session()
    time.sleep(0.5)
    state = panel.page_state()
    print("после сброса:", state["transcribe_progress"], state["ai_progress"],
          state["transcribe_done"], state["ai_result"])
    shot(panel, "reset-recording")
    panel.update("transcribing", "12 сек · ~8 сек осталось", 0.0)
    time.sleep(0.5)
    state = panel.page_state()
    print("первая весть:", state["transcribe_progress"], state["ai_progress"])
    shot(panel, "reset-first-news")
    panel.stop()


if __name__ == "__main__":
    main()
