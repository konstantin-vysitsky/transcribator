"""Проверка: приписки по ходу речи сразу уходят в поле.

Запуск: `.venv\\Scripts\\python.exe tests\\check_live_typing.py`

Вставка подменена: вместо буфера обмена и клавиш записывается, что и когда
попало в поле. Так проверяется проводка — очередь, состояние диктовки и
защита от чужого окна, — не трогая настоящий ввод.

Здесь ловился главный сбой: приписки складывались в очередь, но в поле не
уходили, и текст появлялся только в конце диктовки.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation.live_typing import type_live  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(name)


class FakePaster:
    """Вместо буфера обмена: запомнить, что попало в поле и когда."""

    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.typed: list[tuple[float, str]] = []
        self.calls: list[dict] = []
        self.started = time.monotonic()

    def paste(self, text: str, **kwargs) -> tuple[bool, bool]:
        self.calls.append(kwargs)
        if not self.alive:
            return False, False
        self.typed.append((time.monotonic() - self.started, text))
        return True, False

    def foreground(self, _hwnd: int | None) -> bool:
        return self.alive


def run_case(alive: bool, pieces: list[str]) -> tuple[list[tuple[float, str]], list[str]]:
    box: queue.Queue = queue.Queue()
    state = {"nonce": 7, "ever": False, "pasted": False, "len": 0, "started": time.monotonic()}
    paster = FakePaster(alive=alive)
    lines: list[str] = []
    worker = threading.Thread(
        target=type_live,
        kwargs=dict(
            queue_of_pieces=box,
            state=state,
            nonce=7,
            paste=paster.paste,
            foreground=paster.foreground,
            target_window=lambda: 4242,
            append_space=True,
            log=lines.append,
            name="проверка",
        ),
        daemon=True,
        name="check-live",
    )
    worker.start()
    for piece in pieces:
        box.put(piece)
        time.sleep(0.12)
    box.put(None)
    worker.join(timeout=2.0)
    return paster.typed, lines


def main() -> int:
    print("1) обычная диктовка: слова уходят сразу")
    typed, lines = run_case(True, ["смотри ", "это ", "проверка ", "потока "])
    check("в поле ушли все четыре приписки", len(typed) == 4, typed)
    check("порядок слов сохранён", [w for _t, w in typed] == ["смотри ", "это ", "проверка ", "потока "], typed)
    check(
        "слова не копились до конца",
        len({round(t, 1) for t, _w in typed}) >= 3,
        [round(t, 1) for t, _w in typed],
    )
    check("в журнале есть строка на каждую приписку", len([l for l in lines if "приписка" in l]) == 4, lines)

    print("2) человек ушёл в другое окно: по ходу речи не пишем")
    box: queue.Queue = queue.Queue()
    state = {"nonce": 5, "ever": False, "started": time.monotonic()}
    paster = FakePaster(alive=True)
    lines: list[str] = []
    worker = threading.Thread(
        target=type_live,
        kwargs=dict(
            queue_of_pieces=box,
            state=state,
            nonce=5,
            paste=paster.paste,
            foreground=paster.foreground,
            target_window=lambda: 4242,
            append_space=True,
            log=lines.append,
            name="проверка",
        ),
        daemon=True,
    )
    worker.start()
    box.put("это в поле ")
    time.sleep(0.15)
    # Окно сменилось: человек ушёл в другое, и по ходу речи писать нельзя.
    paster.alive = False
    box.put("а это уже не туда ")
    time.sleep(0.15)
    box.put(None)
    worker.join(timeout=2.0)
    check("в поле ушло только сказанное до смены окна", len(paster.typed) == 1, paster.typed)
    check("в журнале сказано, что отложено", any("другое окно" in l for l in lines), lines)

    print("3) прошлая диктовка: чужие приписки не печатаются")
    box: queue.Queue = queue.Queue()
    state = {"nonce": 99, "ever": False}
    paster = FakePaster()
    worker = threading.Thread(
        target=type_live,
        kwargs=dict(
            queue_of_pieces=box,
            state=state,
            nonce=1,
            paste=paster.paste,
            foreground=paster.foreground,
            target_window=lambda: 1,
            append_space=True,
            log=lambda _m: None,
        ),
        daemon=True,
    )
    worker.start()
    box.put("старое ")
    box.put(None)
    worker.join(timeout=2.0)
    check("приписка прошлой диктовки отброшена", not paster.typed, paster.typed)

    if FAILURES:
        print(f"\nПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("\nВсё прошло: слова дописываются по ходу речи.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
