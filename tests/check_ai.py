"""Проверка обработки диктовки через ИИ без запуска приложения.

Запуск: `.venv\\Scripts\\python.exe tests\\check_ai.py`

Что проверяется:
- промпт читается, сохраняется и восстанавливается;
- запрос уходит на нужный адрес с нужными заголовками и телом;
- ответ модели возвращается как текст, ошибки не роняют диктовку;
- текст без ключа возвращается нетронутым.

Сеть не нужна: HTTP-вызовы подменяются. Живая проверка ключа — отдельно,
флагом `--live`.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation import ai, prompt  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ок   {name}")
    else:
        print(f"  ПЛОХО {name} {detail}")
        FAILURES.append(name)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def with_urlopen(handler):
    original = urllib.request.urlopen
    urllib.request.urlopen = handler
    return original


def test_prompt_roundtrip() -> None:
    print("промпт:")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        value = prompt.load_prompt(root)
        check("отсутствующий файл даёт текст по умолчанию", value == prompt.DEFAULT_PROMPT)
        check("файл создан на диске", prompt.prompt_path(root).exists())

        prompt.save_prompt(root, "свой промпт\n")
        check("сохранённый промпт читается обратно", prompt.load_prompt(root) == "свой промпт\n")

        prompt.prompt_path(root).write_text("   \n", encoding="utf-8")
        check("пустой файл заменяется дефолтом", prompt.load_prompt(root) == prompt.DEFAULT_PROMPT)

        prompt.save_prompt(root, "испорченный\n")
        restored = prompt.restore_prompt(root)
        check("восстановление возвращает дефолт в памяти", restored == prompt.DEFAULT_PROMPT)
        check(
            "восстановление записано в файл",
            prompt.load_prompt(root) == prompt.DEFAULT_PROMPT,
        )

    check("в промпте запрещено менять смысл", "менять, добавлять или убирать смысл" in prompt.DEFAULT_PROMPT)
    check("промпт требует вернуть только текст", "Верни только итоговый текст" in prompt.DEFAULT_PROMPT)


def test_request_shape() -> None:
    print("запрос:")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"choices": [{"message": {"content": "  Привет, мир.  "}}]})

    original = with_urlopen(fake_urlopen)
    try:
        text, error = ai.process_text(
            "ну э-э привет мир",
            prompt="промпт",
            api_key="sk-test-key",
            base_url="https://example.test/v1",
            model="deepseek-v4-flash",
            timeout_s=12.0,
        )
    finally:
        urllib.request.urlopen = original

    check("ошибки нет", error is None, str(error))
    check("ответ обрезан по краям", text == "Привет, мир.", repr(text))
    check("адрес собран верно", captured["url"] == "https://example.test/v1/chat/completions", captured["url"])
    check("ключ ушёл в Authorization", captured["auth"] == "Bearer sk-test-key", captured["auth"])
    check("таймаут передан", captured["timeout"] == 12.0, str(captured["timeout"]))
    check("модель в теле", captured["body"]["model"] == "deepseek-v4-flash")
    check("промпт первым сообщением", captured["body"]["messages"][0]["content"] == "промпт")
    check("диктовка вторым сообщением", captured["body"]["messages"][1]["content"] == "ну э-э привет мир")
    check("роль системы", captured["body"]["messages"][0]["role"] == "system")
    check("стрим выключен", captured["body"]["stream"] is False)
    check("temperature 0", captured["body"]["temperature"] == 0)
    check("запас токенов не меньше минимума", captured["body"]["max_tokens"] >= 512)
    check("размышления выключены", captured["body"].get("thinking") == {"type": "disabled"})


def test_thinking_only_for_deepseek() -> None:
    print("режим размышлений:")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"choices": [{"message": {"content": "ок"}}]})

    original = with_urlopen(fake_urlopen)
    try:
        # Чужой совместимый сервис на незнакомое поле может ответить отказом.
        ai.process_text("текст", "промпт", "k", base_url="https://example.test/v1", model="gpt-4o-mini")
    finally:
        urllib.request.urlopen = original
    check("чужой модели поле не отправлено", "thinking" not in captured["body"], str(captured["body"].keys()))


def test_base_url_without_v1() -> None:
    print("адрес:")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return FakeResponse({"choices": [{"message": {"content": "ок"}}]})

    original = with_urlopen(fake_urlopen)
    try:
        ai.process_text("текст", "промпт", "k", base_url="https://example.test/v1/")
    finally:
        urllib.request.urlopen = original
    check("двойной слэш не появляется", captured["url"] == "https://example.test/v1/chat/completions", captured["url"])


def test_failures_keep_text() -> None:
    print("сбои:")
    cases = [
        (urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"")), "ключ отклонён"),
        (urllib.error.HTTPError("u", 402, "no", {}, io.BytesIO(b"")), "закончились средства"),
        (urllib.error.HTTPError("u", 429, "no", {}, io.BytesIO(b"")), "слишком много запросов"),
        (urllib.error.HTTPError("u", 503, "no", {}, io.BytesIO(b"")), "сервис недоступен"),
        (urllib.error.URLError("нет сети"), "нет связи с сервисом"),
        (TimeoutError(), "сервис не ответил вовремя"),
    ]
    for exc, expected in cases:
        def raiser(request, timeout=None, _exc=exc):
            raise _exc

        original = with_urlopen(raiser)
        try:
            text, error = ai.process_text("важный текст", "промпт", "k")
        finally:
            urllib.request.urlopen = original
        check(f"{expected}: текст сохранён", text == "важный текст", repr(text))
        check(f"{expected}: причина названа", error == expected, repr(error))

    for payload, expected in [
        ({"choices": []}, "пустой ответ сервиса"),
        ({"choices": [{"message": {"content": "   "}}]}, "пустой ответ сервиса"),
        ({}, "пустой ответ сервиса"),
    ]:
        def fake(request, timeout=None, _p=payload):
            return FakeResponse(_p)

        original = with_urlopen(fake)
        try:
            text, error = ai.process_text("текст", "промпт", "k")
        finally:
            urllib.request.urlopen = original
        check(f"{expected}: текст сохранён", text == "текст", repr(text))
        check(f"{expected}: причина названа", error == expected, repr(error))

    text, error = ai.process_text("важный текст", "промпт", "")
    check("без ключа запрос не уходит", error == "не задан ключ", repr(error))
    check("без ключа текст сохранён", text == "важный текст", repr(text))

    text, error = ai.process_text("важный текст", "  ", "k")
    check("с пустым промптом запрос не уходит", error == "пустой промпт", repr(error))

    text, error = ai.process_text("   ", "промпт", "k")
    check("пустой текст не отправляется", text == "   " and error is None, repr(error))

    # Ключ с кириллицей ломает HTTP-заголовок: причина должна быть названа,
    # а текст — сохранён. Так выглядит ключ, скопированный вместе с русским текстом.
    text, error = ai.process_text("важный текст", "промпт", "sk-ключ-с-кириллицей")
    check("ключ с кириллицей не роняет диктовку", text == "важный текст", repr(text))
    check(
        "ключ с кириллицей назван причиной",
        error == "ключ содержит недопустимые знаки",
        repr(error),
    )


def test_max_tokens_scaling() -> None:
    print("запас токенов:")
    check("короткий текст получает минимум", ai._max_tokens_for("привет") == 512)
    check("длинный текст получает больше", ai._max_tokens_for("а" * 3000) > 3000)
    check("очень длинный текст обрезан потолком", ai._max_tokens_for("а" * 100000) == 4096)


def test_config_defaults() -> None:
    print("настройки:")
    from dictation import config

    check("обработка по умолчанию выключена", config.DEFAULTS["ai_enabled"] is False)
    check("адрес по умолчанию — Token Seller", config.DEFAULTS["ai_base_url"] == ai.DEFAULT_BASE_URL)
    check("ключ по умолчанию пуст", config.DEFAULTS["ai_api_key"] == "")
    check("модель по умолчанию задана", bool(config.DEFAULTS["ai_model"]))
    check(
        "адрес по умолчанию — федота.рф",
        "xn--80ahc1bui.xn--p1ai" in config.DEFAULTS["ai_base_url"],
        config.DEFAULTS["ai_base_url"],
    )


def test_live() -> None:
    print("живая проверка ключа:")
    from dictation import config

    cfg = config.load_config()
    key = str(cfg.get("ai_api_key", ""))
    if not key:
        print("  пропуск: в config.json нет ai_api_key")
        return
    error = ai.check_connection(
        key,
        base_url=str(cfg.get("ai_base_url", ai.DEFAULT_BASE_URL)),
        model=str(cfg.get("ai_model", ai.DEFAULT_MODEL)),
    )
    check("ключ принят сервисом", error is None, str(error))


def main() -> int:
    test_prompt_roundtrip()
    test_request_shape()
    test_base_url_without_v1()
    test_thinking_only_for_deepseek()
    test_failures_keep_text()
    test_max_tokens_scaling()
    test_config_defaults()
    if "--live" in sys.argv:
        test_live()

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
