"""Обработка распознанного текста через LLM.

Запрос идёт в OpenAI-совместимый `/v1/chat/completions`. По умолчанию — наш
Token Seller (`https://федота.рф/v1`), но адрес и модель задаются в
`config.json`, так что сервис можно поменять на любой совместимый.

Смысл обработки: привести диктовку к чёткому читаемому виду. Модель правит
мусор, повторы и пунктуацию, но не переписывает мысль и ничего не добавляет.
Инструкция для неё лежит отдельным файлом — `prompt.txt` в корне проекта;
человек правит его из окна диктовки.

Никаких исключений наружу: если сервис недоступен или ключ неверен, диктовка
должна вставиться как есть, а не потеряться. Ошибка возвращается текстом.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# Ответ модели вставляется вместо диктовки, поэтому он должен быть готовым
# текстом без служебных украшений. Своё «не могу» модель тоже вернёт текстом,
# и он попадёт в поле ввода — это заметно и правится одним нажатием.
DEFAULT_BASE_URL = "https://xn--80ahc1bui.xn--p1ai/v1"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT_S = 30.0
# Служебный запас поверх текста: промпт и ответ по токенам считаются отдельно.
_MAX_TOKENS_FLOOR = 512
_MAX_TOKENS_CEILING = 4096


def _max_tokens_for(text: str) -> int:
    """Запас на ответ: обработка почти всегда короче исходника.

    Кириллица стоит примерно один токен на символ, поэтому длина текста —
    оценка сверху. Слишком маленький предел обрезал бы длинную диктовку.
    """
    estimate = int(len(text) * 1.5) + 256
    return max(_MAX_TOKENS_FLOOR, min(_MAX_TOKENS_CEILING, estimate))


def _error_text(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 401:
            return "ключ отклонён"
        if exc.code == 402:
            return "закончились средства"
        if exc.code == 429:
            return "слишком много запросов"
        if exc.code == 400:
            return "сервис отклонил запрос"
        if exc.code >= 500:
            return "сервис недоступен"
        return f"ошибка сервиса {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "нет связи с сервисом"
    if isinstance(exc, UnicodeEncodeError):
        # Ключ попал в буфер обмена с кириллицей или другим не-ASCII знаком.
        return "ключ содержит недопустимые знаки"
    if isinstance(exc, TimeoutError):
        return "сервис не ответил вовремя"
    return f"ошибка обработки: {exc.__class__.__name__}"


def _without_thinking(payload: dict[str, object], model: str) -> dict[str, object]:
    """Выключить режим размышлений у моделей DeepSeek.

    У DeepSeek он включён по умолчанию, а чистке диктовки рассуждать не о чем:
    размышления только тратят время и токены. Поле понимают лишь модели
    DeepSeek, поэтому чужому совместимому сервису его не отправляем.
    """
    if model.strip().lower().startswith("deepseek"):
        payload["thinking"] = {"type": "disabled"}
    return payload


def _headers(api_key: str) -> dict[str, str]:
    """Заголовки запроса.

    HTTP-заголовки передаются побайтово и не знают кириллицы. Ключ, скопированный
    вместе с русским текстом, уронил бы запрос с `UnicodeEncodeError` — поэтому
    проверяем заранее и говорим об этом понятно, а не «ошибкой обработки».
    """
    key = api_key.strip()
    try:
        key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("ключ содержит недопустимые знаки") from exc
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def process_text(
    text: str,
    prompt: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[str, str | None]:
    """Прогнать текст через модель. Возвращает (текст, ошибка).

    При ошибке первым элементом идёт исходный текст: вызывающий код вставляет
    его без изменений и только показывает, что обработка не удалась.
    """
    if not text.strip():
        return text, None
    if not api_key.strip():
        return text, "не задан ключ"
    if not prompt.strip():
        return text, "пустой промпт"
    try:
        headers = _headers(api_key)
    except ValueError as exc:
        return text, str(exc)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": _max_tokens_for(text),
        "stream": False,
    }
    payload = _without_thinking(payload, model)
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — диктовка не должна теряться
        return text, _error_text(exc)

    choices = body.get("choices") or []
    if not choices:
        return text, "пустой ответ сервиса"
    content = (choices[0].get("message") or {}).get("content") or ""
    result = str(content).strip()
    if not result:
        return text, "пустой ответ сервиса"
    return result, None


def check_connection(
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout_s: float = 20.0,
) -> str | None:
    """Короткая проверка ключа и адреса. Возвращает текст ошибки или None."""
    if not api_key.strip():
        return "не задан ключ"
    try:
        headers = _headers(api_key)
    except ValueError as exc:
        return str(exc)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ответь одним словом: ок"}],
        "max_tokens": 8,
        "stream": False,
    }
    payload = _without_thinking(payload, model)
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc)
    return None
