"""Дописывание в поле по ходу речи.

Модель зовёт `submit` из своего потока на каждое законченное слово, а
`type_live` в отдельном потоке ставит это слово в поле. Отдельный поток
нужен, потому что вставка через буфер обмена занимает десятки
миллисекунд: в потоке модели она тормозила бы распознавание, и текст
отставал бы от речи.

Здесь же вся защита от лишнего: в поле ничего не стирается и не
переписывается, а если человек ушёл в другое окно, приписка не делается —
иначе текст уехал бы не туда.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

# Сколько знаков приписки показывать в журнале: слово, иногда два.
_LOG_CHARS = 48


def _clip(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= _LOG_CHARS else clean[: _LOG_CHARS - 1] + "…"


def type_live(
    queue_of_pieces: queue.Queue,
    state: dict,
    nonce: int,
    *,
    paste: Callable[..., tuple[bool, bool]],
    foreground: Callable[[int | None], bool],
    target_window: Callable[[], int | None],
    append_space: bool,
    log: Callable[[str], None],
    name: str = "",
    stop: threading.Event | None = None,
) -> None:
    """Дописывать в поле приписки из очереди, пока не придёт `None`.

    `state` — общий на диктовку словарь: `nonce` отличает новую диктовку
    от прошлой, `ever` — было ли уже вставлено хоть что-то, `len` — сколько
    знаков вставлено в последний раз.
    """
    while True:
        if stop is not None and stop.is_set():
            return
        item = queue_of_pieces.get()
        if item is None:
            return
        if state.get("nonce") != nonce:
            # Диктовку отменили или начали новую: старое не дописываем.
            continue
        piece = item
        seconds = time.monotonic() - float(state.get("started") or time.monotonic())
        target = target_window()
        ever = bool(state.get("ever"))
        if ever and not foreground(target):
            # Человек ушёл в другое окно. По ходу речи туда не пишем:
            # текст без спроса появлялся бы не в том поле. Окончательный
            # текст вставится в конце, в то окно, что на экране.
            log(f"приписка «{_clip(piece)}»: {seconds:.1f} с, отложена — другое окно")
            continue
        try:
            pasted, _enter = paste(
                piece,
                append_space=append_space,
                auto_enter=False,
                target_hwnd=target,
                only_if_foreground=ever,
            )
        except Exception as exc:  # noqa: BLE001 — звук и модель важнее приписки
            log(f"сбой дописывания по ходу речи: {type(exc).__name__}: {exc}")
            continue
        log(
            f"приписка «{_clip(piece)}»: {seconds:.1f} с, "
            f"вставлено={'да' if pasted else 'нет'}"
            + (f", поток={name}" if name else "")
        )
        state["ever"] = ever or pasted
        state["pasted"] = pasted
        state["len"] = len(piece) + (1 if append_space and piece else 0)
