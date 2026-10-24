"""Панель: полосы хода начинают каждую диктовку с нуля.

Забыть сброс легко: `_render` рисует только то, что лежит в полях, и
заполненная полоса прошлой диктовки переезжает в новую.
"""

import time

from dictation.status_panel import _TkStatusPanel


class _Panel(_TkStatusPanel):
    """Панель без окна: только поля хода и их сброс."""

    def __init__(self) -> None:  # noqa: D107
        self._transcribe_done = True
        self._ai_result = True
        self._progress = 1.0
        self._shown = 1.0
        self._ai_progress = 1.0
        self._ai_shown = 1.0
        self._ai_note = "готово"
        self._ai_elapsed = 12.0
        self._ai_started = 0.0

    def _render(self) -> None:  # noqa: D102
        return


def _clear(panel: _Panel) -> None:
    _TkStatusPanel._clear_stages(panel)


def _reset(panel: _Panel) -> None:
    _TkStatusPanel._reset_stages(panel, "recording")


def test_clear_stages_zeroes_every_bar() -> None:
    panel = _Panel()
    _clear(panel)
    assert panel._shown == 0.0
    assert panel._progress == 0.0
    assert panel._ai_shown == 0.0
    assert panel._ai_progress == 0.0
    assert panel._transcribe_done is False
    assert panel._ai_result is None
    assert panel._ai_started > 0.0
    assert panel._ai_started <= time.monotonic()


def test_page_reports_empty_bars_after_clear() -> None:
    panel = _Panel()
    _clear(panel)
    assert panel._shown == 0.0 and panel._ai_shown == 0.0


def test_other_state_change_clears_bars() -> None:
    panel = _Panel()
    _reset(panel)
    assert panel._shown == 0.0
    assert panel._ai_shown == 0.0


def test_step_change_keeps_bars() -> None:
    panel = _Panel()
    _TkStatusPanel._reset_stages(panel, "transcribing")
    assert panel._shown == 1.0
    assert panel._ai_shown == 1.0
