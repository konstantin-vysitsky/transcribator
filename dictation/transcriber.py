from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from dictation.chunking import format_text, join_texts, split
from dictation.cleanup import load_fillers, remove_fillers
from dictation.recorder import SAMPLE_RATE
from transcribe import load_model, transcribe_gigaam


# Громкость, при которой модель понимает речь. Громкая запись пиковой
# громкостью до 0.9 (около 90 % шкалы) распознаётся только на части уровней
# громкости: кусок с «раз два три» давался пустым при полной шкале и давал
# слова при 12 %. Поэтому громкая запись успокаивается до этого уровня, а
# если вышло пусто — пробуются и другие уровни.
_CALM_LEVEL = 0.6
_GAIN_ATTEMPTS = (1.0, 0.3, 0.12)


def _gain_audio(audio: np.ndarray, gain: float) -> np.ndarray:
    """Запись той же громкости, но на новой шкале.

    Дробная громкость тянет деликатные звуки речи вверх: у GigaAM на записи
    от 4 ноября это и отличало пустой ответ от слов.
    """
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak <= 0:
        return audio
    if gain == 1.0:
        if peak <= _CALM_LEVEL:
            return audio
        gain = _CALM_LEVEL / peak
    return np.clip(audio.astype(np.float32) * gain, -1.0, 1.0)


# Ответ на шум: одиночные междометия и служебные словечки. Если модель
# ответила только ими, а другой уровень громкости даёт слова — берутся слова.
_NOISE_WORDS = frozenset(
    {"э", "угу", "ага", "мм", "м", "да", "нет", "три", "раз", "два", "почему", "так", "ну"}
)


def _useful(text: str) -> bool:
    """Есть ли в ответе модели слово, а не только междометие.

    «Три.» и «Э-э» — обычный ответ на шум. Ради такого не стоит жечь время
    на повторное распознавание: текст уже есть, и человеку он нужен.
    """
    words = [word for word in text.replace("ё", "е").split() if any(ch.isalpha() for ch in word)]
    if len(words) >= 2:
        return True
    if not words:
        return False
    word = words[0].strip(".,!?-…").lower()
    return len(word) > 2 and word not in _NOISE_WORDS


class Transcriber:
    def __init__(
        self,
        model: str = "v3_e2e_rnnt",
        cpu_threads: int = 0,
        remove_fillers_enabled: bool = True,
    ) -> None:
        self._model_name = model
        self._remove_fillers = remove_fillers_enabled
        self._fillers = load_fillers() if remove_fillers_enabled else []
        self._lock = threading.Lock()
        self._model, self._device, self._compute_type = load_model(model, cpu_threads)

    def _recognize(self, audio: np.ndarray) -> str:
        """Распознать одну запись; пусто — не сказало ничего.

        Сначала как есть. Если вышло пусто или одно междометие, запись идёт
        на другие уровни громкости: так модель понимает то же самое иначе.
        """
        if not len(audio):
            return ""
        first = ""
        for index, gain in enumerate(_GAIN_ATTEMPTS):
            text = transcribe_gigaam(self._model, _gain_audio(audio, gain))
            text = self._postprocess(text)
            if index == 0:
                first = text
            # Пустота и слова-однодневки («э-э», «угу») не считаются: на них
            # перебор продолжается. Иначе пустой текст от первого прохода
            # уходил бы в журнал как «распознан пустой текст».
            if text and (index == 0 or _useful(text)):
                return text
        return first

    @property
    def device(self) -> str:
        return self._device

    def runtime_label(self) -> str:
        return f"gigaam/{self._model_name}, {self._device}"

    def _postprocess(self, text: str) -> str:
        text = text.strip()
        if self._remove_fillers and text:
            text = remove_fillers(text, self._fillers)
        return text

    def transcribe(
        self,
        audio: np.ndarray,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Распознать запись.

        Длинная запись режется на куски: поданная целиком, она не даёт текста
        вовсе — модель обучена на коротких фразах, и обработка на этом встаёт.
        `on_progress` получает (готово кусков, всего кусков): на длинной
        диктовке это единственный признак, что работа идёт, а не зависла.
        """
        with self._lock:
            pieces = split(audio, SAMPLE_RATE)
            if not pieces:
                return ""
            # Текст строчными буквами возвращает сама модель, поэтому
            # предложения собираются здесь: иначе вставленная диктовка
            # выглядит как одна сплошная строчка без заглавных и точек.
            if len(pieces) == 1:
                return self._postprocess(format_text(self._recognize(pieces[0])))

            parts: list[str] = []
            total = len(pieces)
            for index, piece in enumerate(pieces, start=1):
                parts.append(format_text(self._recognize(piece)))
                if on_progress is not None:
                    try:
                        on_progress(index, total)
                    except Exception:
                        pass
            return self._postprocess(join_texts(parts))
