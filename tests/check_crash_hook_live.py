"""Проверка: поставленный хук действительно записывает падение.

В отличие от check_crash_log.py (тот проверяет сам хук на временном каталоге),
здесь берётся настоящий проект и его dictation.log — так проверяется, что
установка хука в живом приложении делает ровно то, что нужно.

Запуск: .venv\\Scripts\\python.exe tests\\check_crash_hook_live.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation.main import LOG_NAME, _install_crash_logging  # noqa: E402

MARKER = "нарочная проверка хука"


def _read_log(log: Path) -> str:
    """Прочитать журнал, не спотыкаясь о старое битое место.

    В журнале лежит строка от 24 октября с двойной перекодировкой (байт 0x8C
    вне UTF-8), и строгое чтение всего файла падает на ней. Приложение пишет
    корректный UTF-8 — проверять нужно только свежие записи, поэтому мусор на
    месте битых байтов не мешает.
    """
    if not log.is_file():
        return ""
    return log.read_bytes().decode("utf-8", errors="replace")


def main() -> int:
    log = ROOT / LOG_NAME
    original = log.read_bytes() if log.is_file() else b""
    before = len(original.decode("utf-8", errors="replace").splitlines())

    _install_crash_logging(ROOT)

    def boom() -> None:
        raise RuntimeError(MARKER)

    thread = threading.Thread(target=boom, name="process-audio")
    thread.start()
    thread.join()

    text = _read_log(log)
    new = text.splitlines()[before:]
    body = "\n".join(new)

    if MARKER not in body:
        print("ПРОВАЛ: падение не записано в", log)
        return 1
    if "process-audio" not in body:
        print("ПРОВАЛ: не указан поток")
        return 1
    print("\n".join(new[:3]))
    print("ОК: падение записано в настоящий dictation.log")

    # Убираем нарочную запись: журнал рабочий, следов проверки в нём быть не
    # должно. Файл возвращается байт в байт из снимка, сделанного до
    # проверки, — иначе чужая битая строка (24 октября) испортилась бы при
    # перезаписи через текст.
    log.write_bytes(original)
    print("журнал возвращён к состоянию до проверки")
    return 0


if __name__ == "__main__":
    sys.exit(main())
