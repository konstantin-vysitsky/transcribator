"""Нарезка длинной записи на куски для распознавания.

Модель GigaAM обучена на коротких фразах. Если подать ей запись целиком —
несколько минут подряд, — она не выдаёт текст: обработка идёт, но результата
нет, и снаружи это выглядит как зависание. Короткие фразы при этом работают
нормально, поэтому поломка проявлялась только на длинной диктовке.

Резать по паузам, а не по секундам: разрез посреди слова теряет его окончание.
Внутри окна ищется самое тихое место — там шанс разорвать фразу наименьший.
"""
from __future__ import annotations

import numpy as np

# Границы куска. Меньше — теряется контекст и растёт число прогонов модели;
# больше — падает качество и растёт время на кусок.
MIN_CHUNK_S = 8.0
MAX_CHUNK_S = 30.0
# Запись короче этого идёт в модель целиком: резать нечего.
SINGLE_CHUNK_S = 32.0
# Шаг поиска тишины и длина окна оценки громкости.
PROBE_S = 0.02
# Куски короче этого отбрасываются: обычно это остаток тишины в конце.
MIN_KEEP_S = 0.5


def _level(audio: np.ndarray, start: int, window: int) -> float:
    piece = audio[start:start + window]
    if len(piece) == 0:
        return 1.0
    return float(np.mean(np.abs(piece)))


def find_cuts(audio: np.ndarray, sample_rate: int) -> list[int]:
    """Позиции разрезов в отсчётах. Пустой список — резать не нужно."""
    total = len(audio)
    min_step = int(MIN_CHUNK_S * sample_rate)
    max_step = int(MAX_CHUNK_S * sample_rate)
    window = max(1, int(PROBE_S * sample_rate))

    cuts: list[int] = []
    position = 0
    while total - position > max_step:
        limit = position + max_step
        # Ищем самое тихое место от минимальной длины куска до предела.
        best_position = position + min_step
        best_level: float | None = None
        probe = position + min_step
        while probe < limit:
            level = _level(audio, probe, window)
            if best_level is None or level < best_level:
                best_level = level
                best_position = probe
            probe += window
        if best_position <= position:
            break
        cuts.append(best_position)
        position = best_position
    return cuts


def split(audio: np.ndarray, sample_rate: int) -> list[np.ndarray]:
    """Разбить запись на куски. Короткую возвращает одним куском."""
    if len(audio) <= int(SINGLE_CHUNK_S * sample_rate):
        return [audio] if len(audio) else []

    pieces: list[np.ndarray] = []
    previous = 0
    for cut in find_cuts(audio, sample_rate):
        pieces.append(audio[previous:cut])
        previous = cut
    pieces.append(audio[previous:])

    minimum = int(MIN_KEEP_S * sample_rate)
    return [piece for piece in pieces if len(piece) >= minimum]


def join_texts(parts: list[str]) -> str:
    """Склеить распознанные куски в один текст.

    Пробелы и повторы пробелов убираются: модель на границе куска может начать
    или закончить текст пробелом, а лишние пробелы в середине заметны.
    """
    text = " ".join(part.strip() for part in parts if part and part.strip())
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()
