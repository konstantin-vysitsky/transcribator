"""Живая проверка: полный цикл диктовки и журнал с новыми отметками.

Нажимает F7 (начать), ждёт, нажимает F7 (остановить) и печатает новые
строки журнала. Так видно, что начало и конец обработки пишутся парой,
с длительностью, и что запись помечена своей длиной.

Запуск: .venv\\Scripts\\python.exe tests\\check_live_cycle.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "dictation.log"
VK_F7 = 0x76
KEYEVENTF_KEYUP = 0x0002


def lines() -> list[str]:
    return LOG.read_bytes().decode("utf-8", errors="replace").splitlines()


def press() -> None:
    ctypes.windll.user32.keybd_event(VK_F7, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(VK_F7, 0, KEYEVENTF_KEYUP, 0)


def main() -> int:
    before = len(lines())
    print("F7: начало записи")
    press()
    time.sleep(2.5)
    print("F7: остановка")
    press()
    time.sleep(6.0)

    new = lines()[before:]
    for line in new:
        print(line)

    body = "\n".join(new)
    failures = []
    if "запись начата" not in body:
        failures.append("запись не началась")
    if "запись сохранена" not in body:
        failures.append("запись не сохранилась")
    if "обработка начата" not in body:
        failures.append("нет отметки о начале обработки")
    if "обработка закончена за" not in body and "обработка прервана через" not in body:
        failures.append("нет отметки о конце обработки")
    # Длительность стоит прямо в строке сохранения: «… .wav (2.5 с)».
    saved_line = next((l for l in new if "запись сохранена" in l), "")
    if saved_line and " с)" not in saved_line:
        failures.append(f"у сохранённой записи нет длительности: {saved_line!r}")

    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: журнал показывает начало, конец и длительность")
    return 0


if __name__ == "__main__":
    sys.exit(main())
