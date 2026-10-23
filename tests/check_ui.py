"""Живая проверка окна промпта и панели статуса.

Запуск: `.venv\\Scripts\\python.exe tests\\check_ui.py`

Открывает окно правки промпта и панель статуса так же, как это делает
приложение, ждёт и закрывает. Проверяется то, что видно только на живом
Tk: окно строится и встаёт по макету, текст промпта доезжает до поля, кнопки
на панели нажимаются и доходят до Python, вид и тема меняются без
перезапуска, а две ступени хода работы заполняются.

Панель и окно снова на встроенной графике Tk, как было до переноса на
страницу. Панели проверяются отдельными процессами: у процесса Tk один
корень, и второй делит с первым картинки.

Ничего не печатает в поле ввода, не трогает буфер обмена и не пишет в
настоящие настройки и в настоящий промпт.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation import panel_style  # noqa: E402
from dictation.prompt import DEFAULT_PROMPT, load_prompt  # noqa: E402
from dictation.prompt_editor import PromptEditor  # noqa: E402
from dictation.status_panel import StatusPanel  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


def stop_panel(panel: StatusPanel) -> None:
    """Остановить панель и дать потоку Tk спокойно выйти.

    Панель живёт в своём потоке и строит свой корень Tk. Второй корень в том
    же процессе Tk делит с первым картинки и не всегда стартует чисто, поэтому
    каждая панель в проверке работает отдельным процессом.
    """
    panel.stop()
    time.sleep(0.4)


def test_prompt_editor() -> None:
    print("окно промпта:")
    saved: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stored = {"value": "исходный промпт"}

        editor = PromptEditor(
            load=lambda: stored["value"],
            save=lambda text: (saved.append(text), stored.update(value=text)),
        )
        check("объект построен", editor._root is not None)
        check("окно существует", editor._window is not None)
        check("поле текста существует", editor._text is not None)
        check("окно дождалось готовности", editor.wait_ready(0.1))
        check("на старте окно закрыто", not editor.is_open())

        editor.open()
        time.sleep(0.4)
        root_widget = editor._root
        assert root_widget is not None

        def run_on_ui(action) -> None:
            done = threading.Event()

            def wrapper() -> None:
                try:
                    action()
                finally:
                    done.set()

            root_widget.after(0, wrapper)
            if not done.wait(timeout=3.0):
                raise AssertionError("действие в потоке Tk не выполнилось")

        text_value: list[str] = []
        run_on_ui(lambda: text_value.append(editor._text.get("1.0", "end")))
        check(
            "окно считает себя открытым",
            editor.is_open(),
        )
        window = editor._window
        if window is not None:
            width = window.winfo_width()
            height = window.winfo_height()
            print(f"       размер окна промпта: {width}x{height}")
            check("окно промпта показано", window.winfo_viewable())
            check("ширина окна по макету", 600 <= width <= 1600, str(width))
            check("высота окна по макету", 500 <= height <= 1500, str(height))
        check(
            "в поле доехал текущий промпт",
            bool(text_value) and text_value[0].startswith("исходный промпт"),
            repr(text_value),
        )

        run_on_ui(editor._on_restore)
        check("восстановление записало дефолт", saved and saved[-1].strip() == DEFAULT_PROMPT.strip(), repr(saved[-1:]))

        restored: list[str] = []
        run_on_ui(lambda: restored.append(editor._text.get("1.0", "end")))
        check(
            "восстановление видно в поле",
            bool(restored) and restored[0].strip() == DEFAULT_PROMPT.strip(),
            repr(restored[:1]),
        )

        def set_text(value: str) -> None:
            editor._text.delete("1.0", "end")
            editor._text.insert("1.0", value)

        run_on_ui(lambda: set_text("мой текст"))
        run_on_ui(editor._on_save)
        check("сохранение записало правку", saved[-1].strip() == "мой текст", repr(saved[-1:]))

        hint = editor._hint
        check("подсказка о сохранении показана", hint is not None and "Сохранено" in hint.cget("text"))

        run_on_ui(lambda: set_text("   "))
        before = len(saved)
        run_on_ui(editor._on_save)
        check("пустой промпт не сохраняется", len(saved) == before, str(len(saved)))
        check(
            "про пустой промпт сказано",
            hint is not None and "не сохраняется" in hint.cget("text"),
            hint.cget("text") if hint else "",
        )

        editor.close()
        time.sleep(0.3)
        check("закрытое окно промпта считает себя закрытым", not editor.is_open())
        check("закрытое окно спрятано", window is not None and not window.winfo_viewable())

        editor.stop()
        time.sleep(0.3)


def test_status_panel() -> None:
    print("панель статуса:")
    toggles: list[bool] = []
    clicks: list[str] = []

    panel = StatusPanel(
        on_abort=lambda: clicks.append("abort"),
        on_stop_recording=lambda: clicks.append("done"),
        on_toggle_ai=lambda enabled: toggles.append(enabled),
        ai_enabled=False,
    )
    root_widget = panel._root
    check("панель построена", root_widget is not None)
    check("панель на встроенной графике", type(panel).__name__ == "_TkStatusPanel", type(panel).__name__)
    check("кнопка ИИ создана", panel._ai_btn is not None)
    check("кнопки промпта на панели нет", not hasattr(panel, "_prompt_btn"))
    check("кнопка автоотправки на месте", panel._auto_enter_btn is not None)
    check("кнопка Готово на месте", panel._stop_btn is not None)
    check("кнопка отмены на месте", panel._abort_btn is not None)

    check("по умолчанию обработка выключена", not panel.ai_enabled())

    window = panel._window
    check("в простое панель не видна", window is not None and not window.winfo_viewable())

    panel.update("recording")
    time.sleep(0.4)
    check("панель показана на записи", window is not None and window.winfo_viewable())

    if window is not None and window.winfo_viewable():
        width = window.winfo_width()
        height = window.winfo_height()
        print(f"       размер окна панели: {width}x{height}")
        check("ширина разумная", 200 < width < 600, str(width))
        check("высота разумная", 100 < height < 400, str(height))

    state = panel.page_state()
    check("состояние говорит про запись", state["activity"] == "recording", repr(state["activity"]))
    check("состояние несёт вид", state["style"] == "black", repr(state["style"]))
    check("время записи идёт", state["duration"] > 0, repr(state["duration"]))
    check("обработка из настроек видна в состоянии", state["ai_enabled"] is False)

    assert root_widget is not None

    def run_on_ui(action) -> None:
        """Выполнить действие в потоке Tk и дождаться, пока оно отработает.

        `after(0, ...)` лишь ставит задачу в очередь: без ожидания проверка
        успевает прочитать состояние до того, как обработчик его поменяет.
        """
        done = threading.Event()

        def wrapper() -> None:
            try:
                action()
            finally:
                done.set()

        root_widget.after(0, wrapper)
        if not done.wait(timeout=3.0):
            raise AssertionError("действие в потоке Tk не выполнилось")

    panel.set_level(0.7)
    check("громкость микрофона принимается", True)

    canvas = panel._canvas
    check("холст панели есть", canvas is not None)
    run_on_ui(lambda: press(panel, "stop", canvas))
    check("кнопка «Готово» доходит до Python", clicks == ["done"], repr(clicks))
    run_on_ui(lambda: press(panel, "abort", canvas))
    check("крестик доходит до Python", clicks == ["done", "abort"], repr(clicks))
    check("крестик помечает «без автоотправки»", panel.skip_auto_enter())
    run_on_ui(panel._on_toggle_auto_enter)
    check("переключатель автоотправки снимает отметку", not panel.skip_auto_enter())
    run_on_ui(panel._on_toggle_auto_enter)
    check("переключатель автоотправки ставит отметку", panel.skip_auto_enter())
    run_on_ui(panel._on_toggle_auto_enter)
    check("переключатель автоотправки снова снимает отметку", not panel.skip_auto_enter())

    run_on_ui(panel._on_toggle_ai)
    check("нажатие кнопки включает обработку", panel.ai_enabled())
    check("наружу сказано о включении", toggles == [True], repr(toggles))
    button = panel._ai_btn
    check(
        "надпись на кнопке сменилась",
        button is not None and "вкл" not in button.text and "ИИ" in button.text,
        button.text if button else "",
    )
    time.sleep(0.3)
    check(
        "ползунок ушёл во включённое",
        button is not None and button.progress > 0.9,
        button.progress if button else "",
    )

    run_on_ui(panel._on_toggle_ai)
    check("повторное нажатие выключает", not panel.ai_enabled())
    check("наружу сказано о выключении", toggles == [True, False], repr(toggles))
    time.sleep(0.3)
    check(
        "надпись говорит про выключенное",
        button is not None and "выкл" in button.text,
        button.text if button else "",
    )
    check(
        "ползунок ушёл в выключенное",
        button is not None and button.progress < 0.1,
        button.progress if button else "",
    )

    run_on_ui(panel._on_toggle_ai)
    check("третье нажатие снова включает", panel.ai_enabled(), "переключатель залип")
    check("наружу сказано о включении снова", toggles == [True, False, True], repr(toggles))

    # Каждое нажатие обязано менять состояние. Раньше флагов было два, и
    # каждое второе нажатие проходило вхолостую: человек жмёт, ничего не
    # меняется, и это выглядит как залипшая кнопка.
    for index in range(8):
        before = panel.ai_enabled()
        run_on_ui(panel._on_toggle_ai)
        if panel.ai_enabled() == before:
            check(f"нажатие {index + 4} не холостое", False, "состояние не изменилось")
            break
    else:
        check("восемь нажатий подряд все рабочие", True)
    check(
        "наружу ушло по сообщению на каждое нажатие",
        len(toggles) == 11,
        str(len(toggles)),
    )
    check("кнопка вернулась во включённое", panel.ai_enabled())

    panel.set_ai_default(True)
    time.sleep(0.4)
    check("настройка включает обработку", panel.ai_enabled())
    check(
        "ползунок показывает включённое",
        button is not None and button.progress > 0.9,
        button.progress if button else "",
    )

    panel.update("processing", "отправляю текст модели…")
    time.sleep(0.4)
    check(
        "видно обработку через ИИ",
        panel._state == "processing",
        panel._state,
    )

    panel.reset_session()
    time.sleep(0.4)
    check("сброс вернул обработку к настройке", panel.ai_enabled())

    # Вид и тема меняются без перезапуска: рисунок берёт цвета из палитры.
    panel.set_theme("light")
    time.sleep(0.4)
    check("тема сменилась без перезапуска", panel.page_state()["theme"] == "light", panel.page_state()["theme"])
    panel.set_theme("dark")
    time.sleep(0.4)
    check("тема вернулась", panel.page_state()["theme"] == "dark", panel.page_state()["theme"])
    for style in ("glow", "smoke", "frost", "black"):
        panel.set_style(style)
        time.sleep(0.25)
        check(f"вид {style} встал без перезапуска", panel.style == style, panel.style)
    check("неизвестный вид не роняет панель", panel_style.normalize_style("чушь") == "black")
    panel.set_style("чушь")
    time.sleep(0.25)
    check("опечатка в виде заменяется видом по умолчанию", panel.style == "black", panel.style)

    # Две ступени хода: распознавание, затем обработка. Ступень видна не по
    # подписи, а по заполнению — поэтому проверяется доля, а не текст.
    panel.update("transcribing", "12 сек · ~8 сек осталось", 0.5)
    time.sleep(0.5)
    check(
        "идёт первая ступень",
        panel._progress > 0.4 and not panel._transcribe_done,
        f"{panel._progress:.2f}",
    )
    panel.mark_transcribed()
    panel.update("processing", "жду ответа модели…")
    time.sleep(0.5)
    check("первая ступень дозалилась", panel._shown > 0.9, f"{panel._shown:.2f}")
    check("вторая ступень в работе", panel._transcribe_done and panel._ai_result is None)
    check(
        "вторая ступень пошла вперёд без вестей о доле",
        panel._ai_shown > 0.02,
        f"{panel._ai_shown:.2f}",
    )
    panel.set_ai_finished(recovered=True)
    time.sleep(0.4)
    check("вторая ступень заполнилась", panel._ai_result is True and panel._ai_shown == 1.0)

    panel.update("idle")
    time.sleep(0.3)
    check("панель спрятана в простое", window is not None and not window.winfo_viewable())

    panel.stop()
    time.sleep(0.3)


def test_ai_ready_mark() -> None:
    """Отметка настраиваемости: кнопка говорит, когда обрабатывать нечем.

    Человек включает обработку, диктовка вставляется необработанной, и без
    отметки это выглядит как поломка. Кнопка обязана сказать, что не готово.
    """
    print("отметка настройки ИИ:")
    panel = StatusPanel(ai_enabled=False, ai_ready=False)
    time.sleep(0.4)
    button = panel._ai_btn
    try:
        check("ползунок построен", button is not None)
        check("без ключа обработка не настроена", not panel.ai_ready())
        check(
            "на ползунке сказано про недонастройку",
            button is not None and "не настроен" in button.text,
            button.text if button else "",
        )
        check(
            "подсказка называет место, где лежит ключ",
            "config.local.json" in panel.ai_hint(),
            panel.ai_hint(),
        )

        panel.set_ai_ready(True)
        time.sleep(0.4)
        check("с ключом обработка настроена", panel.ai_ready())
        check(
            "надпись стала обычной",
            button is not None and "не настроен" not in button.text,
            button.text if button else "",
        )
        check("подсказка пуста", panel.ai_hint() == "", panel.ai_hint())

        panel.set_ai_error("ключ отклонён")
        time.sleep(0.4)
        check(
            "ошибка запроса видна на ползунке",
            button is not None and "ошибка" in button.text,
            button.text if button else "",
        )
        check(
            "подсказка объясняет ошибку",
            "ключ отклонён" in panel.ai_hint(),
            panel.ai_hint(),
        )

        panel.set_ai_error(None)
        time.sleep(0.4)
        check(
            "после удачной обработки ошибка снята",
            button is not None and "ошибка" not in button.text,
            button.text if button else "",
        )

        panel.set_ai_ready(False)
        panel.set_ai_default(True)
        time.sleep(0.4)
        check("без ключа ползунок снова предупреждает", "не настроен" in (button.text if button else ""))
    finally:
        panel.stop()
        time.sleep(0.3)


def test_panel_failure_is_logged() -> None:
    """Панель не поднялась — причина в журнале, приложение живо.

    Окно панели строится в своём потоке. Исключение из него питоном
    печатается как поломка процесса. Проверяем, что причина уходит в журнал
    строкой и что приложение продолжает работать без панели.
    """
    print("сбой панели:")
    global _PANEL_CANVAS
    import dictation.status_panel as status_panel_module  # noqa: PLC0415

    messages: list[str] = []
    original_fonts = status_panel_module._fonts
    original_logger = status_panel_module._log

    def broken_fonts(*_args, **_kwargs):
        raise RuntimeError("шрифты не нашлись")

    status_panel_module.set_logger(messages.append)
    status_panel_module._fonts = broken_fonts
    try:
        panel = status_panel_module.StatusPanel(ai_enabled=False)
        check("панель отвечает, что окна нет", panel._root is None)
        check(
            "причина сбоя записана в журнал",
            any("не поднялась" in message for message in messages),
            repr(messages),
        )
        panel.update("recording", "проверка")
        panel.set_theme("light")
        panel.set_style("frost")
        panel.set_ai_default(True)
        panel.set_ai_ready(False)
        panel.set_ai_error("проверка")
        panel.mark_transcribed()
        panel.set_ai_finished(False)
        panel.set_level(0.4)
        panel.reset_session()
        print("       состояние без окна:", panel.page_state()["activity"], panel.page_state()["style"])
        check("запросы без окна не роняют приложение", True)
        panel.stop()
    finally:
        status_panel_module._fonts = original_fonts
        status_panel_module._log = original_logger


def press(panel: StatusPanel, name: str, canvas) -> None:
    """Нажать кнопку панели в потоке её окна так, как это делает человек.

    Живой клик приходит событием мыши: панель сама смотрит, куда он попал,
    и зовёт свой обработчик. Здесь событие такое же, поэтому проверяется
    настоящий путь нажатия, а не вызов обработчика в обход.
    """
    box = panel._hit_stop if name == "stop" else panel._hit_abort
    panel._on_mouse_down(_click(box, canvas))


def _click(box: tuple[int, int, int, int] | None, canvas):
    """Нажатие по середине области панели: так же приходит живой клик."""
    if box is None:
        raise AssertionError("у панели нет области для этого нажатия")
    left, top, right, bottom = box

    class _Event:
        x = (left + right) // 2
        y = (top + bottom) // 2
        x_root = x
        y_root = y
        widget = canvas

    return _Event()


_SECOND_PANEL_CODE = None


def run_panel_checks() -> int:
    """Проверки одной панели: запускаются в отдельном процессе.

    Корень Tk у процесса один; второй корень делит с первым картинки и
    стартует не всегда. Поэтому каждая проверка панели идёт своим процессом.
    """
    test_status_panel()
    test_ai_ready_mark()
    return 1 if FAILURES else 0


def main() -> int:
    test_prompt_editor()
    # Панели проверяются в отдельных процессах: у процесса один корень Tk.
    for run_number in (1, 2):
        status = run_panel_checks_subprocess()
        if status != 0:
            check(f"проверка панели, процесс {run_number}", False, str(status))
        else:
            check(f"проверка панели, процесс {run_number}", True)
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло.")
    return 0


def run_panel_checks_subprocess() -> int:
    import subprocess  # noqa: PLC0415

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--panel"],
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


if __name__ == "__main__":
    if "--panel" in sys.argv:
        raise SystemExit(run_panel_checks())
    raise SystemExit(main())
