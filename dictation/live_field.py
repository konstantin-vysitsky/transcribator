"""Дописывание в поле: общая часть для всех моделей.

Модель отдаёт свой текст, а здесь решается, что из него уже можно писать
в поле и что ещё рано. Правило одно: в поле только дописывается, ничего
не стирается и не переписывается — на переписывании текст дёргался и
отставал от речи.

Класс общий, потому что моделей стало три: потоковая sherpa (t-one),
GigaAM и Whisper. Текст они отдают по-разному, а в поле он должен
попадать одинаково.
"""

from __future__ import annotations

from typing import Callable

# Знаки, которые модель приставляет к слову. При сверке снимаются: иначе
# запятая посреди диктовки выглядела как другое слово и поле замирало.
_EDGE_PUNCT = ".,!?;:—–-«»\"'()…"

# Сколько знаков приписки показывать в журнале: слово, иногда два.
_LOG_CHARS = 48


def clip(text: str, limit: int = _LOG_CHARS) -> str:
    clean = " ".join(str(text).split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


class FieldTyping:
    """Дописывание слов в поле, где стоит курсор."""

    def _init_field_typing(self) -> None:
        # Дописывание в поле по ходу речи: только в то окно, где стоял
        # курсор. На панели управления текст не показывается.
        self._typing = False
        self._on_append: Callable[[str], None] | None = None
        # Что уже отдано в поле. Слова хранятся в нижнем виде и без знаков —
        # так сверка не зависит от запятых и заглавных, которые модель
        # ставит по ходу. Список, а не строка: по индексу слова сразу
        # находится его написание со знаками, и в поле уходит именно оно.
        self._field_words: list[str] = []
        # Сколько раз модель поменяла уже записанное: по числу в журнале
        # видно, много ли правок и не пора ли их убирать.
        self.revisions = 0

    def set_typing(self, enabled: bool, on_append: Callable[[str], None] | None = None) -> None:
        """Дописывать в поле по ходу речи. `on_append` получает только новое."""
        self._typing = enabled
        self._on_append = on_append

    def _log_safe(self, message: str) -> None:
        """Записать строку измерений, не давая журналу уронить диктовку."""
        log = getattr(self, "_log", None)
        if log is None:
            return
        try:
            log(message)
        except Exception:  # noqa: BLE001 — журнал не важнее звука
            pass

    def _final_piece(self, text: str) -> str:
        """Текст на конец диктовки. Модель со знаками отдаёт как есть."""
        return " ".join(text.split())

    def _typing_piece(self, text: str) -> str:
        """Что уже готово в поле. Модель, которая ставит знаки сама,
        ничего не переписывает: только лишние пробелы."""
        return " ".join(text.split())

    @staticmethod
    def _normal_words(text: str) -> list[str]:
        """Слова для сверки: нижним регистром, без знаков вокруг слова.

        Знаки модель ставит и снимает по ходу, и сверка по тексту с ними
        останавливала поле. Буква «ё» считается за «е»: модель пишет то
        так, то так. Пустые места не выбрасываются: список идёт слово в
        слово с настоящим текстом, и по его номеру находится написание со
        знаками.
        """
        return [word.strip(_EDGE_PUNCT).lower().replace("ё", "е") for word in text.split()]

    @staticmethod
    def _place(line: list[str], field: list[str]) -> int | None:
        """Где в чтении начинается то, чего в поле нет.

        Сверяется хвост того, что стоит в поле: сдвиг в пару слов туда-сюда
        допускается. None — сверить не удалось, ждём следующего чтения.
        Врать наугад нельзя: ошибка здесь приписывает в поле кусок заново.
        """
        if not field:
            return 0
        tail = min(3, len(field))
        for shift in (0, -1, 1, -2, 2):
            spot = len(field) + shift
            if 0 <= spot <= len(line) and line[spot - tail:spot] == field[-tail:]:
                return spot
        return None

    def _print_ready(self, text: str, release_last: bool = False) -> None:
        """Дописать в поле слова, которых в нём ещё нет.

        В поле идёт настоящее написание слова — с запятыми, точками и
        большой буквой, как их поставила модель. Для сверки рядом считается
        то же слово без знаков: иначе запятая выглядела бы новым словом.

        `release_last` — пауза в речи: последнее слово тоже отдаётся, хотя
        модель ещё может его поменять. Иначе конец фразы висел до
        следующей фразы.
        """
        if not self._typing or self._on_append is None:
            return
        piece = self._typing_piece(text)
        raw = piece.split()
        if not raw:
            return
        norms = self._normal_words(piece)
        # Готовы все слова, кроме последнего: после него модель ещё не
        # начала следующее, и оно может поменяться.
        count = len(raw)
        if not release_last and not piece.endswith(" "):
            count -= 1
        if count <= 0:
            return
        start = self._place(norms[:count], self._field_words)
        if start is None:
            return
        if norms[:start] != self._field_words[:start]:
            self.revisions += 1
        chunk = raw[start:count]
        if not chunk:
            return
        try:
            self._on_append(" ".join(chunk) + " ")
        except Exception as exc:  # noqa: BLE001 — поле важнее, но причину в журнал
            self._log_safe(f"сбой дописывания в поле: {type(exc).__name__}: {exc}")
            self._on_append = None
            return
        self._field_words = norms[:count]

    def _append_words(self, words: list[str]) -> None:
        """Дописать в поле готовые слова, ровно как их сказала модель.

        Здесь нет сверки с полем: слова уже отобраны (подтверждённое начало
        фразы), и их надо положить как есть — со знаками и заглавными.
        """
        if not words or not self._typing or self._on_append is None:
            return
        norms = self._normal_words(" ".join(words))
        if norms and self._field_words[-len(norms):] == norms:
            # Эти слова уже стоят в поле: второй раз не печатаем. Так бывает,
            # когда модель отдаёт одну и ту же фразу дважды.
            return
        try:
            self._on_append(" ".join(words) + " ")
        except Exception as exc:  # noqa: BLE001 — поле важнее, но причину в журнал
            self._log_safe(f"сбой дописывания в поле: {type(exc).__name__}: {exc}")
            self._on_append = None
            return
        self._field_words.extend(norms)

    def final_append(self) -> str | None:
        """Приписка в конец диктовки: остаток текста.

        Вызывается после остановки, когда модель дочитала хвост: последнее
        слово и точка. Дописывается только то, чего в поле ещё нет —
        старое не стирается и не повторяется.
        """
        text = getattr(self, "_partial", "")
        if not self._typing or not text.strip():
            return None
        piece = self._final_piece(text)
        if not piece.strip():
            return None
        raw = piece.split()
        norms = self._normal_words(piece)
        start = self._place(norms, self._field_words)
        if start is None:
            start = len(self._field_words)
        new_words = raw[min(start, len(raw)):]
        self._field_words = norms
        if not new_words:
            return None
        return " ".join(new_words)
