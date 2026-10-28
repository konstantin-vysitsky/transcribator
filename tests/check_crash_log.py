"""Проверка: падение приложения оставляет причину в журнале.

Приложение живёт в трее без консоли, и до этих правок необработанное
исключение уносило процесс, не оставив в dictation.log ни строки. Проверка
поднимает оба пути — главный поток и фоновый — и убеждается, что причина
записана.

Запуск: .venv\\Scripts\\python.exe tests\\check_crash_log.py
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation.main import _install_crash_logging  # noqa: E402


def _read(root: Path) -> str:
    log = root / "dictation.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


def _crash_in_thread() -> None:
    def boom() -> None:
        raise RuntimeError("нарочный сбой в потоке")

    thread = threading.Thread(target=boom, name="process-audio")
    thread.start()
    thread.join()


def main() -> int:
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="crash-log-"))

    _install_crash_logging(tmp)

    # 1. Фоновый поток: там идёт распознавание.
    _crash_in_thread()
    text = _read(tmp)
    if "нарочный сбой в потоке" not in text:
        failures.append("падение в потоке не записано")
    elif "process-audio" not in text:
        failures.append("в записи нет имени потока")
    elif "Traceback" not in text:
        failures.append("нет traceback: причина без места падения бесполезна")

    # 2. Главный поток: stderr брать некуда, поэтому подменяем печать.
    real_excepthook = sys.__excepthook__
    printed = []
    try:
        sys.__excepthook__ = lambda *a: printed.append(a)
        try:
            raise ValueError("нарочный сбой в главном потоке")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.__excepthook__ = real_excepthook

    text = _read(tmp)
    if "нарочный сбой в главном потоке" not in text:
        failures.append("падение в главном потоке не записано")
    if "ValueError" not in text:
        failures.append("не записан тип исключения")

    # 3. Многострочная запись не должна ломать разбор журнала.
    if "\n" in text:
        for line in text.splitlines():
            if not line.strip():
                continue
            if not (line[:4].isdigit() or line.startswith(" ")):
                failures.append(f"строка журнала без времени и отступа: {line!r}")
                break

    print(text)
    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: обе причины падения попадают в журнал")
    return 0


if __name__ == "__main__":
    sys.exit(main())
