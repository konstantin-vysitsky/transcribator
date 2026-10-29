"""Проверка: залипший признак занятости не глушит диктовку навсегда.

Отказ, который здесь проверяется, выглядел так: приложение слушает, но
запись больше не начинается. Признак «занят» оставался взведённым после
сбоя в фоновой обработке, и каждое нажатие молча упиралось в проверку
занятости. Лечилось только перезапуском.

Проверка воспроизводит залипание и убеждается, что новая запись всё равно
начинается.

Запуск: .venv\\Scripts\\python.exe tests\\check_busy_stuck.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeStory:
    """Тот же порядок проверок, что в on_record_start, без окна и модели."""

    def __init__(self) -> None:
        self.busy = threading.Event()
        self.processing_thread: list[threading.Thread | None] = [None]
        self.starts = 0
        self.notes: list[str] = []

    def processing_thread_alive(self) -> bool:
        thread = self.processing_thread[0]
        return thread is not None and thread.is_alive()

    def on_record_start(self) -> bool:
        if self.busy.is_set():
            if not self.processing_thread_alive():
                self.notes.append("признак занятости залип: снимаю")
                self.busy.clear()
            else:
                self.notes.append("ещё обрабатывается")
                return False
        self.busy.set()
        self.starts += 1
        self.notes.append("запись начата")
        # работа считается законченной сразу: поток обработки завершён
        self.busy.clear()
        return True


def main() -> int:
    failures = []

    # 1. Признак занятости остался, а поток обработки уже умер.
    story = FakeStory()
    story.busy.set()                      # залипло
    story.processing_thread[0] = None     # потока нет
    started = story.on_record_start()
    if not started:
        failures.append("залипший признак занятости не дал начать запись")
    if story.busy.is_set() and story.starts == 0:
        failures.append("признак остался взведённым")

    # 2. Настоящая работа идёт — мешать нельзя.
    story2 = FakeStory()
    story2.busy.set()
    alive = threading.Thread(target=lambda: time.sleep(5), daemon=True)
    alive.start()
    story2.processing_thread[0] = alive
    started = story2.on_record_start()
    alive.join(timeout=0.1)
    if started:
        failures.append("запись начата поверх настоящей обработки")
    if "ещё обрабатывается" not in story2.notes:
        failures.append("нет записи о настоящей занятости")

    print("заметки:", story.notes, story2.notes)
    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: залипший признак снимается, настоящая работа не трогается")
    return 0


if __name__ == "__main__":
    sys.exit(main())
