"""Чистка распознанного текста: слова-паразиты и следы после их удаления.

Список — консервативный: только то, что не бывает смыслом. «Ну», «вот»,
«значит» сюда не входят — они слишком часто настоящие слова. Свой список
правится в ~/.transcribator/fillers.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HOME = Path.home() / ".transcribator"
FILLERS_PATH = HOME / "fillers.json"

DEFAULT_FILLERS = [
    "э", "э-э", "э-э-э", "эм", "эмм", "эммм", "ээ", "эээ",
    "а-а", "а-а-а", "м-м", "м-м-м", "мм", "ммм", "мхм", "угу",
    "как бы", "типа", "это самое", "короче говоря", "так сказать",
    "в общем-то", "ну-у", "ну-у-у",
]


def _ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)


def load_fillers() -> list[str]:
    _ensure_home()
    if FILLERS_PATH.exists():
        try:
            data = json.loads(FILLERS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x).strip().lower() for x in data if str(x).strip()]
        except (json.JSONDecodeError, OSError):
            pass
    save_fillers(DEFAULT_FILLERS)
    return list(DEFAULT_FILLERS)


def save_fillers(words: list[str]) -> None:
    _ensure_home()
    FILLERS_PATH.write_text(json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8")


def _pattern(words: list[str]) -> re.Pattern | None:
    words = sorted({w for w in words if w}, key=len, reverse=True)
    if not words:
        return None
    alt = "|".join(re.escape(w) for w in words)
    return re.compile(
        rf"(?P<pre>(?:^|(?<=\s))?,?\s*)(?<![\w-])(?P<w>{alt})(?![\w-])(?P<post>\s*,?)",
        re.IGNORECASE,
    )


def _replace(m: re.Match) -> str:
    pre, post = m.group("pre"), m.group("post")
    if "," in pre and "," in post:
        return " "
    if "," in pre:
        return ""
    if "," in post:
        return " " if pre.strip() == "" and m.start() > 0 else ""
    return " "


def remove_fillers(text: str, words: list[str] | None = None) -> str:
    pat = _pattern(words if words is not None else load_fillers())
    if not text or pat is None:
        return text
    out = pat.sub(_replace, text)
    out = re.sub(r"\s*,\s*,", ",", out)
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    out = re.sub(r"([,;:])\s*(?=[,.!?;:])", "", out)
    out = re.sub(r"^[\s,;:–—-]+", "", out)
    out = out.strip()
    out = re.sub(r"(^|[.!?]\s+)([а-яёa-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out
