"""Разбор журнала потоковой диктовки: где рвётся цепочка.

Запуск:
  .venv\\Scripts\\python.exe tests\\inspect_stream_log.py
  .venv\\Scripts\\python.exe tests\\inspect_stream_log.py --all
  .venv\\Scripts\\python.exe tests\\inspect_stream_log.py D:\\tools\\my-old-transcribator\\dictation.log

Читает `dictation.log` (UTF-8) и печатает таблицу приписок по ходу речи:
кусок — секунда от нажатия F7 — секунда попытки вставки — задержка —
вставлено или отложено и почему. В конце — короткий вердикт: на каком
шаге цепочка рвётся.

Ничего не запускает и ничего не меняет: только читает журнал.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "dictation.log"

STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)  (.*)$")
START = re.compile(r"^запись начата(?:: (.*))?$")
TARGET = re.compile(r"^целевое окно: hwnd=(\d+|None), «(.*?)», класс=(.*)$")
MODEL = re.compile(r"^текст модели: ([\d.]+) с от F7, звука ([\d.]+) с, «(.*)»$")
HANDLER = re.compile(r"^обработчик текста: ([\d.]+) с, «(.*)»$")
HANDLER_FAIL = re.compile(r"^сбой обработчика текста модели: (.*)$")
QUEUE = re.compile(r"^приписка «(.*?)»: ([\d.]+) с, в очереди, поток=(.*)$")
DEFER = re.compile(r"^приписка «(.*?)»: ([\d.]+) с, отложена — (.*?), поток=(.*)$")
PASTE = re.compile(
    r"^вставка: ([\d.]+) с, целевое окно hwnd=(\d+|None), «(.*?)», "
    r"класс=(.*?); на переднем плане hwnd=(\d+|None), «(.*?)», "
    r"класс=(.*?); поток=(.*)$"
)
RESULT = re.compile(r"^приписка «(.*?)»: ([\d.]+) с, вставлено=(да|нет), поток=(.*)$")
LIVE_FAIL = re.compile(
    r"^(сбой дописывания по ходу речи|сбой дописывания в поле|"
    r"сбой потоковой диктовки|сбой обработки диктовки): (.*)$"
)
SAVED = re.compile(r"^запись сохранена: (.*?) \(([\d.]+) с\)$")
LIVE_END = re.compile(r"^потоковая диктовка: ([\d.]+) с речи, ([\d.]+) с на всё")


class Run:
    """Одна диктовка: от нажатия F7 до последней строки о ней."""

    def __init__(self, stamp: str) -> None:
        self.stamp = stamp
        self.target: tuple[str, str, str] | None = None
        self.started: str | None = None
        self.model: list[tuple[float, float, str]] = []
        self.handlers: list[float] = []
        self.handler_fail: list[str] = []
        self.queued: list[tuple[str, float]] = []
        self.deferred: list[tuple[str, float, str]] = []
        self.pastes: list[dict] = []
        self.results: list[tuple[str, float, str]] = []
        self.failures: list[str] = []
        self.saved: str | None = None
        self.end: str | None = None

    def empty(self) -> bool:
        return not (
            self.target
            or self.started
            or self.model
            or self.handlers
            or self.queued
            or self.results
        )


def read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.splitlines()


def parse_runs(lines: list[str]) -> list[Run]:
    runs: list[Run] = []
    run: Run | None = None
    for line in lines:
        found = STAMP.match(line)
        if not found:
            continue
        stamp, body = found.group(1), found.group(2)

        # «целевое окно» пишется перед каждой записью, поэтому оно и
        # начинает новую диктовку в разборе.
        target = TARGET.match(body)
        if target:
            run = Run(stamp)
            run.target = target.group(1), target.group(2), target.group(3)
            runs.append(run)
            continue
        if run is None:
            continue

        start = START.match(body)
        if start:
            if run is None:
                # Старый журнал без строк «целевое окно»: всё равно разбираем.
                run = Run(stamp)
                run.started = stamp
                runs.append(run)
            else:
                run.started = run.started or stamp
            continue
        model = MODEL.match(body)
        if model:
            run.model.append(
                (float(model.group(1)), float(model.group(2)), model.group(3))
            )
            continue
        handler = HANDLER.match(body)
        if handler:
            run.handlers.append(float(handler.group(1)))
            continue
        failure = HANDLER_FAIL.match(body)
        if failure:
            run.handler_fail.append(failure.group(1))
            continue
        queued = QUEUE.match(body)
        if queued:
            run.queued.append((queued.group(1), float(queued.group(2))))
            continue
        deferred = DEFER.match(body)
        if deferred:
            run.deferred.append(
                (deferred.group(1), float(deferred.group(2)), deferred.group(3))
            )
            continue
        paste = PASTE.match(body)
        if paste:
            run.pastes.append(
                {
                    "sec": float(paste.group(1)),
                    "target_hwnd": paste.group(2),
                    "target": paste.group(3),
                    "target_class": paste.group(4),
                    "fg_hwnd": paste.group(5),
                    "fg": paste.group(6),
                    "fg_class": paste.group(7),
                    "thread": paste.group(8),
                }
            )
            continue
        result = RESULT.match(body)
        if result:
            run.results.append(
                (result.group(1), float(result.group(2)), result.group(3))
            )
            continue
        live_fail = LIVE_FAIL.match(body)
        if live_fail:
            run.failures.append(f"{live_fail.group(1)}: {live_fail.group(2)}")
            continue
        saved = SAVED.match(body)
        if saved:
            run.saved = f"{saved.group(1)} ({saved.group(2)} с)"
            run.end = stamp
            continue
        live_end = LIVE_END.match(body)
        if live_end:
            run.end = stamp
            continue
    return [item for item in runs if not item.empty()]


def short(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()


def find_result(run: Run, piece: str, used: set[int]) -> tuple[float, str] | None:
    for index, (name, sec, outcome) in enumerate(run.results):
        if index in used or name != piece:
            continue
        used.add(index)
        return sec, outcome
    return None


def show_run(run: Run, number: int, total: int) -> None:
    print(f"Диктовка {number}/{total}: {run.stamp}" + (f" – {run.end}" if run.end else ""))
    if run.target:
        hwnd, title, klass = run.target
        print(f"  целевое окно: hwnd={hwnd}, «{short(title, 60)}», класс={klass}")
    if run.saved:
        print(f"  запись: {run.saved}")

    if run.model:
        first, last = run.model[0], run.model[-1]
        print(
            f"  обновлений текста модели: {len(run.model)} "
            f"({first[0]:.1f}–{last[0]:.1f} с от F7), "
            f"звука в модели {last[1]:.1f} с"
        )
        print(f"  первое: «{short(first[2], 60)}»")
        print(f"  последнее: «{short(last[2], 60)}»")
    else:
        print("  обновлений текста модели: 0")
    print(f"  вызовов обработчика текста: {len(run.handlers)}")
    for failure in run.handler_fail:
        print(f"  СБОЙ ОБРАБОТЧИКА: {failure}")
    for failure in run.failures:
        print(f"  СБОЙ ЖИВОГО ПУТИ: {short(failure, 120)}")

    print()
    print("  Приписки по ходу речи:")
    headers = ["кусок", "модель, с", "попытка, с", "задержка, с", "итог"]
    widths = [38, 10, 11, 11, 40]
    print("  " + _row(headers, widths))
    print("  " + "-" * (sum(widths) + 2 * (len(widths) - 1)))

    used: set[int] = set()
    rows = 0
    for piece, model_sec in run.queued:
        result = find_result(run, piece, used)
        deferrals = [item for item in run.deferred if item[0] == piece]
        attempt = "—"
        delay = "—"
        if result is not None:
            attempt_sec, outcome = result
            attempt = f"{attempt_sec:.1f}"
            delay = f"{attempt_sec - model_sec:.1f}"
            verdict = "вставлено" if outcome == "да" else "НЕ вставлено"
        else:
            verdict = "нет вставки"
            if deferrals:
                verdict = f"отложена: {short(deferrals[-1][2], 30)}"
        print(
            "  "
            + _row(
                [short(piece, 38), f"{model_sec:.1f}", attempt, delay, verdict],
                widths,
            )
        )
        rows += 1
    if not rows:
        print("  (пусто: в очередь ничего не попало)")

    if run.pastes:
        print()
        print("  Передний план в момент вставки:")
        for paste in run.pastes:
            same = paste["fg_hwnd"] == paste["target_hwnd"]
            mark = "то же окно" if same else "ДРУГОЕ ОКНО"
            print(
                f"  {paste['sec']:.1f} с: hwnd={paste['fg_hwnd']} "
                f"«{short(paste['fg'], 50)}» — {mark}"
            )

    print()
    print("  Вердикт: " + verdict_text(run))
    if number != total:
        print()


def verdict_text(run: Run) -> str:
    if not run.model and not run.handlers:
        return (
            "модель не дала ни одного чтения — рвётся до неё: "
            "микрофон → Recorder → feed."
        )
    if run.handler_fail:
        last = run.handler_fail[-1]
        after = len(run.model) - len(run.handlers)
        tail = (
            f" После сбоя обработчик отключён; чтений модели после этого: {after}."
            if after > 0
            else ""
        )
        return (
            f"обработчик текста модели падает ({last}), приписок нет — "
            f"рвётся в on_text / emit_pending.{tail}"
        )
    if not run.queued:
        return (
            "модель даёт текст, обработчик зовётся, но в очередь typing[0] "
            "ничего не попало — рвётся между on_text и очередью."
        )
    if not run.results:
        reasons = sorted({item[2] for item in run.deferred})
        if reasons:
            return (
                "приписки попали в очередь, но не вставлены; причины: "
                + "; ".join(short(reason, 60) for reason in reasons)
            )
        return "приписки попали в очередь, но вставки не было — рвётся в _typing_worker."
    pasted = sum(1 for _name, _sec, outcome in run.results if outcome == "да")
    if pasted:
        return f"цепочка живая: вставлено приписок {pasted} из {len(run.queued)}."
    return "приписки дошли до вставки, но paste_text вернул «нет»: рвётся на вставке."


def main(argv: list[str]) -> int:
    # На всякий случай: незнакомый знак в консоли не должен ронять разбор.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001 — старый Python или подменённый вывод
        pass
    show_all = "--all" in argv
    args = [arg for arg in argv if not arg.startswith("--")]
    path = Path(args[0]) if args else DEFAULT_LOG
    if not path.is_file():
        print(f"нет журнала: {path}")
        return 1
    runs = parse_runs(read_lines(path))
    if not runs:
        print(f"в журнале нет диктовок с новыми строками: {path}")
        print("сделайте диктовку (F7, говорить 15–20 с) и повторите разбор")
        return 1
    print(f"Журнал: {path}")
    print(f"Диктовок с измерениями: {len(runs)}")
    print()
    selected = runs if show_all else runs[-1:]
    for index, run in enumerate(selected, 1):
        show_run(run, index, len(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
