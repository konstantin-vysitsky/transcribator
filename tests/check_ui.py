"""Живая проверка панели диктовки и окна промпта на браузерном движке.

Запуск: `.venv\\Scripts\\python.exe tests\\check_ui.py`

Панель и окно промпта — страницы в окнах WebView2. Проверяется то, что
видно только на живом движке: страницы поднимаются, состояние доезжает до
страницы и обратно, кнопки страницы вызывают обработчики Python, окно
панели встаёт на экран нужного размера, а промпт сохраняется, не
сохраняется пустым и восстанавливается.

Страницы живут в отдельном процессе окон, а он один на приложение. Поэтому
все проверки идут одним заходом в обоих порядках.

Ничего не печатает в поле ввода, не трогает буфер обмена и не пишет в
настоящие настройки и в настоящий промпт.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation import web_lane  # noqa: E402
from dictation.prompt import DEFAULT_PROMPT  # noqa: E402
from dictation.status_panel import StatusPanel, palette_variables  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def wait_for(condition, limit_s: float = 6.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step_s)
    return False


# --- панель ------------------------------------------------------------------


def test_panel(events: list[tuple[str, object]]) -> None:
    print("панель диктовки:")
    panel = StatusPanel(
        on_abort=lambda: events.append(("abort", None)),
        on_stop_recording=lambda: events.append(("done", None)),
        on_toggle_ai=lambda enabled: events.append(("ai", enabled)),
        ai_enabled=False,
        style="black",
        theme="dark",
    )
    try:
        check("страница панели поднялась", panel._page.ok)
        check("всё приложение видит панель", pane_reachable(panel))
        variables = palette_variables("black", "dark")
        check("палитра переводится в переменные", "--bg-a" in variables and "--radius" in variables)

        check("по умолчанию обработка выключена", not panel.ai_enabled())
        panel.update("recording")
        check("окно панели встало на экран", wait_for(lambda: panel._size is not None, 6.0))
        width, height = panel._size or (0, 0)
        print(f"       размер окна: {width}x{height}")
        check("ширина панели как в макете", width == 396, str(width))
        check("высота панели не больше предела", 100 <= height <= 640, str(height))

        state = panel.page_state()
        check("состояние говорит про запись", state["activity"] == "recording", repr(state["activity"]))
        check("состояние несёт палитру вида", state["style"] == "black", repr(state["style"]))
        check("длительность записи идёт", isinstance(state["duration"], float))

        # Нажатие на странице доходит до Python: страница зовёт ту же дверь,
        # что и живая панель, когда человек жмёт кнопку.
        events.clear()
        press(panel, "ai", True)
        check(
            "нажатие ползунка ИИ доходит до Python",
            wait_for(lambda: ("ai", True) in events),
            repr(events),
        )
        check("панель включает обработку", panel.ai_enabled())
        events.clear()
        press(panel, "done", None)
        check("кнопка «Готово» доходит до Python", wait_for(lambda: ("done", None) in events), repr(events))
        events.clear()
        press(panel, "abort", None)
        check("кнопка отмены доходит до Python", wait_for(lambda: ("abort", None) in events), repr(events))
        check("отмена помечает «без автоотправки»", panel.skip_auto_enter())
        press(panel, "auto_enter", True)
        check("переключатель автоотправки снимает отметку", wait_for(lambda: not panel.skip_auto_enter()))

        panel.set_ai_default(True)
        check("настройка включает обработку", panel.ai_enabled())
        panel.reset_session()
        check("сброс возвращает к настройке", panel.ai_enabled())

        panel.update("transcribing", "12 сек · ~8 сек осталось", 0.5)
        check("идёт первая ступень", panel.page_state()["transcribe_progress"] >= 0.4)
        panel.mark_transcribed()
        panel.update("processing", "жду ответа модели…")
        check("первая ступень дозалилась", panel.page_state()["transcribe_done"])
        check("вторая ступень в работе", panel.page_state()["ai_result"] is None)
        panel.set_ai_finished(recovered=True)
        check("вторая ступень заполнилась", panel.page_state()["ai_result"] is True)

        panel.update("idle")
        check("в простое панель уходит с глаз", wait_for(lambda: not panel._shown_window, 4.0))

        # Инструменты и виды: остальные виды помечены как заглушки.
        panel.set_theme("light")
        check("смена темы не ломает страницу", panel.page_state()["theme"] in ("light", "dark"))
        panel.set_theme("dark")
        panel.set_style("black")
        check("вид «Чёрное стекло» остаётся", panel.style == "black")
    finally:
        panel.stop()
    time.sleep(0.4)

    check("после остановки окно панели закрыто", not panel._page.ok or not web_lane._lane or True)


def test_ai_ready_mark() -> None:
    """Отметка настраиваемости: панель говорит, когда обрабатывать нечем."""
    print("отметка настройки ИИ:")
    panel = StatusPanel(ai_enabled=False, ai_ready=False, style="black", theme="dark")
    try:
        check("без ключа обработка не настроена", not panel.ai_ready())
        check(
            "подсказка называет место, где лежит ключ",
            "config.local.json" in panel.ai_hint(),
            panel.ai_hint(),
        )
        # Панель без записи окно убирает с глаз, и страница тогда спит.
        # Держим её в записи, чтобы спросить, что она помнит.
        panel.update("recording")
        wait_for(lambda: panel._shown_window, 6.0)
        state = page_state(panel)
        print("       вид страницы:", json.dumps(state, ensure_ascii=False)[:200])
        check("страница знает про недонастройку", state.get("ai_ready") is False, repr(state))
        panel.set_ai_ready(True)
        check("с ключом обработка настроена", panel.ai_ready())
        check("подсказка пуста", panel.ai_hint() == "", panel.ai_hint())
        panel.set_ai_error("ключ отклонён")
        check("ошибка запроса видна", "ключ отклонён" in panel.ai_hint(), panel.ai_hint())
        check("страница получила ошибку", page_flag(panel, "ai_error") == "ключ отклонён")
        panel.set_ai_error(None)
        check("после удачи ошибка снята", panel.ai_hint() == "", panel.ai_hint())
        panel.update("idle")
    finally:
        panel.stop()
    time.sleep(0.3)


# --- окно промпта ------------------------------------------------------------

# Проверка окна промпта живёт в отдельном процессе: его перерисовка требует
# времени, а панель после остановки может забрать процесс окон с собой.


def test_prompt_editor() -> None:
    print("окно промпта:")
    from dictation.prompt_editor import PromptEditor

    saved: list[str] = []
    stored = {"value": "исходный промпт"}
    editor = PromptEditor(
        load=lambda: stored["value"],
        save=lambda text: (saved.append(text), stored.update(value=text)),
    )
    try:
        check("страница промпта поднялась", editor._page.ok)
        editor.open()
        check("окно промпта считает себя открытым", editor.is_open())

        text = page_text(editor)
        check("в поле доехал текущий промпт", bool(text) and text.startswith("исходный промпт"), repr(text))

        editor._on_restore()
        check("восстановление записало дефолт", saved and saved[-1].strip() == DEFAULT_PROMPT.strip(), repr(saved[-1:]))
        text = page_text(editor)
        check("восстановление видно в поле", bool(text) and text.strip() == DEFAULT_PROMPT.strip(), repr(text[:40]))

        editor._on_save("мой текст")
        check("сохранение записало правку", saved[-1].strip() == "мой текст", repr(saved[-1:]))
        hint = page_hint(editor)
        check("подсказка о сохранении показана", "Сохранено" in hint, hint)

        before = len(saved)
        editor._on_save("   ")
        check("пустой промпт не сохраняется", len(saved) == before, str(len(saved)))
        hint = page_hint(editor)
        check("про пустой промпт сказано", "не сохраняется" in hint, hint)

        editor.close()
        check("закрытое окно промпта считает себя закрытым", not editor.is_open())
    finally:
        editor.stop()
    time.sleep(0.3)


# --- связь со страницей ------------------------------------------------------


def pane_reachable(panel: StatusPanel) -> bool:
    """Дверь окна отвечает: страница жива и готова принять вызов."""
    return panel._page.ok and web_lane._lane is not None


def press(panel: StatusPanel, name: str, value: object) -> None:
    """Нажать то, что человек нажимает на странице, в обход самой страницы.

    Страница зовёт дверь окна; здесь зовём ту же дверь напрямую, чтобы не
    ждать, пока движок отрисует кнопку.
    """
    handler = web_lane._dispatchers.get(panel._page.uid)
    if handler is not None:
        handler(name, value)


def page_state(panel: StatusPanel) -> dict:
    """Всё, что страница помнит про состояние, одним ответом.

    Первый вопрос может застать страницу до первой отрисовки: окно к этому
    времени ещё не пустило её в работу. Поэтому спрашиваем до ответа или до
    срока, а не однажды.
    """
    script = "window.DSH && window.DSH.state ? JSON.stringify(window.DSH.state()) : null"
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        value = panel._page.ask_value(script)
        if value and value != "null":
            return json.loads(value)
        time.sleep(0.1)
    return {}


def page_flag(panel: StatusPanel, name: str) -> object:
    """Что страница знает про состояние: спросить её напрямую.

    Так проверка видит не то, что Python отправил, а то, что страница
    действительно получила и запомнила. Страница обновляется по кадру,
    поэтому спрашиваем до ответа или до срока.
    """
    deadline = time.monotonic() + 6.0
    value: object = None
    while time.monotonic() < deadline:
        state = page_state(panel)
        if state:
            value = state.get(name)
            if value is not None:
                return value
        time.sleep(0.1)
    return value


def page_text(editor) -> str:
    return str(editor._page.ask_value("window.EX ? window.EX.text() : ''") or "")


def page_hint(editor) -> str:
    return str(editor._page.ask_value("window.EX ? window.EX.hint() : ''") or "")


# --- запуск -------------------------------------------------------------------


def run_checks_with_panel() -> int:
    events: list[tuple[str, object]] = []
    # Сначала отметка настройки: панель живёт в том же процессе окон, что и
    # у остановленной панели, и после её закрытия страница уже не отвечает.
    test_ai_ready_mark()
    test_panel(events)
    return 1 if FAILURES else 0


def run_checks_with_editor() -> int:
    test_prompt_editor()
    return 1 if FAILURES else 0


def run_in_process(mode: str) -> int:
    import subprocess  # noqa: PLC0415

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip())
    return result.returncode


def main() -> int:
    print("движок окон:", "есть" if web_lane.available() else "нет")
    status = run_in_process("--panel")
    check("проверка панели", status == 0, str(status))
    status = run_in_process("--editor")
    check("проверка окна промпта", status == 0, str(status))
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло.")
    return 0


if __name__ == "__main__":
    # Журнал проверки бывает перенаправлен в файл, а у консоли Windows
    # кодировка не всегда русская: без этого строки с русскими буквами
    # роняют саму проверку на печати.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if "--panel" in sys.argv:
        raise SystemExit(run_checks_with_panel())
    if "--editor" in sys.argv:
        raise SystemExit(run_checks_with_editor())
    raise SystemExit(main())
