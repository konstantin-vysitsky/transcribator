"""Живая проверка окна промпта и панели статуса.

Запуск: `.venv\\Scripts\\python.exe tests\\check_ui.py`

Открывает окно правки промпта и панель статуса так же, как это делает
приложение, ждёт и закрывает. Проверяется то, что видно только на живом
Tk: окно строится, текст промпта доезжает до поля, кнопки на панели
существуют и обработчик нажатия вызывается.

Ничего не печатает в поле ввода и не трогает буфер обмена.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation.prompt import DEFAULT_PROMPT, load_prompt  # noqa: E402
from dictation.prompt_editor import PromptEditor  # noqa: E402
from dictation.status_panel import (  # noqa: E402
    _AI_BAD_BG,
    _AI_BG as _AI_ON_BG,
    _BTN_OFF_BG as _AI_OFF_BG,
    StatusPanel,
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"))
    if not condition:
        FAILURES.append(name)


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

        editor.stop()
        time.sleep(0.3)


def test_status_panel() -> None:
    print("панель статуса:")
    toggles: list[bool] = []

    panel = StatusPanel(
        on_toggle_ai=lambda enabled: toggles.append(enabled),
        ai_enabled=False,
    )
    root_widget = panel._root
    check("панель построена", root_widget is not None)
    check("кнопка ИИ создана", panel._ai_btn is not None)
    check("кнопки промпта на панели нет", not hasattr(panel, "_prompt_btn"))
    check("кнопка автоотправки на месте", panel._auto_enter_btn is not None)
    check("кнопка Готово на месте", panel._stop_btn is not None)
    check("кнопка отмены на месте", panel._abort_btn is not None)

    check("по умолчанию обработка выключена", not panel.ai_enabled())

    panel.update("recording")
    time.sleep(0.4)
    window = panel._window
    check("панель показана", window is not None and window.winfo_viewable())

    if window is not None:
        width = window.winfo_width()
        height = window.winfo_height()
        print(f"       размер окна: {width}x{height}")
        check("ширина разумная", 200 < width < 600, str(width))
        check("высота разумная", 100 < height < 400, str(height))

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

    run_on_ui(panel._on_toggle_ai)
    check("нажатие кнопки включает обработку", panel.ai_enabled())
    check("наружу сказано о включении", toggles == [True], repr(toggles))
    button = panel._ai_btn
    check(
        "надпись на кнопке сменилась",
        button is not None and "вкл" in button._text,
        button._text if button else "",
    )
    check(
        "кнопка перекрашена во включённое",
        button is not None and button._bg == _AI_ON_BG,
        button._bg if button else "",
    )

    run_on_ui(panel._on_toggle_ai)
    check("повторное нажатие выключает", not panel.ai_enabled())
    check("наружу сказано о выключении", toggles == [True, False], repr(toggles))
    check(
        "надпись вернулась",
        button is not None and "выкл" in button._text,
        button._text if button else "",
    )
    check(
        "кнопка перекрашена в выключенное",
        button is not None and button._bg == _AI_OFF_BG,
        button._bg if button else "",
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
        "кнопка показывает включённое",
        button is not None and "вкл" in button._text,
        button._text if button else "",
    )

    panel.update("processing", "отправляю текст модели…")
    time.sleep(0.4)
    status = panel._status_label
    check(
        "видно обработку через ИИ",
        status is not None and "ИИ" in status.cget("text"),
        status.cget("text") if status else "",
    )

    panel.reset_session()
    time.sleep(0.4)
    check("сброс вернул обработку к настройке", panel.ai_enabled())

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
        check("кнопка построена", button is not None)
        check("без ключа обработка не настроена", not panel.ai_ready())
        check(
            "на кнопке сказано про недонастройку",
            button is not None and "не настроен" in button._text,
            button._text if button else "",
        )
        check(
            "кнопка перекрашена в предупреждение",
            button is not None and button._bg == _AI_BAD_BG,
            button._bg if button else "",
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
            button is not None and "не настроен" not in button._text,
            button._text if button else "",
        )
        check("подсказка пуста", panel.ai_hint() == "", panel.ai_hint())

        panel.set_ai_error("ключ отклонён")
        time.sleep(0.4)
        check(
            "ошибка запроса видна на кнопке",
            button is not None and "ошибка" in button._text,
            button._text if button else "",
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
            button is not None and "ошибка" not in button._text,
            button._text if button else "",
        )

        panel.set_ai_ready(False)
        panel.set_ai_default(True)
        time.sleep(0.4)
        check("без ключа кнопка снова предупреждает", "не настроен" in (button._text if button else ""))
    finally:
        panel.stop()
        time.sleep(0.3)


def main() -> int:
    test_prompt_editor()
    test_status_panel()
    test_ai_ready_mark()
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Всё прошло.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
