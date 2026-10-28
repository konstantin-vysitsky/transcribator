"""Проверка на живом приложении: F7 доходит и запись действительно начинается.

Приложение уже запущено и слушает F7 (режим toggle). Скрипт нажимает F7,
ждёт, смотрит журнал и нажимает F7 ещё раз — запись должна начаться и
остановиться.

Записывается короткое нажатие, поэтому текст может быть пустым: важно, что
цепочка «клавиша → запись → журнал» живая.

Запуск: .venv\\Scripts\\python.exe tests\\check_live_f7.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from pynput.keyboard import Controller, Key

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "dictation.log"


def _tail(n: int = 8) -> list[str]:
    raw = LOG.read_bytes().decode("utf-8", errors="replace")
    return raw.splitlines()[-n:]


def main() -> int:
    before = len(LOG.read_bytes().decode("utf-8", errors="replace").splitlines())

    kb = Controller()
    print("нажимаю F7 (старт записи)")
    kb.press(Key.f7)
    kb.release(Key.f7)
    time.sleep(2.5)

    mid = LOG.read_bytes().decode("utf-8", errors="replace").splitlines()
    started = any("запись начата" in line for line in mid[before:])

    print("нажимаю F7 (остановка)")
    kb.press(Key.f7)
    kb.release(Key.f7)
    time.sleep(4.0)

    after = LOG.read_bytes().decode("utf-8", errors="replace").splitlines()
    new = after[before:]
    print("--- новые строки журнала ---")
    for line in new:
        print(line)

    if not started and not any("запись начата" in line for line in new):
        print("ПРОВАЛ: F7 не запустил запись")
        return 1
    print("ОК: F7 доходит до приложения, запись стартует")
    return 0


if __name__ == "__main__":
    sys.exit(main())
