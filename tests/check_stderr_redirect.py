"""Проверка: вывод упавшего дочернего процесса попадает в файл.

Приложение запускается скрытым и без консоли. До правок traceback интерпретатора
уходил в никуда, и падение выглядело как «нажал F7 — ничего не произошло».
Здесь запускается процесс, который падает нарочно, и проверяется, что причина
оказалась в dictation-stderr.log.

Запуск: .venv\\Scripts\\python.exe tests\\check_stderr_redirect.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from launch_helper import redirected_output  # noqa: E402

MARKER = "нарочный сбой запуска"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="stderr-redirect-"))
    out = redirected_output(tmp)
    child = subprocess.run(
        [sys.executable, "-c", f'raise RuntimeError("{MARKER}")'],
        cwd=str(tmp),
        **out,
    )
    for stream in out.values():
        if hasattr(stream, "close"):
            stream.close()

    log = tmp / "dictation-stderr.log"
    text = ""
    if log.is_file():
        # Интерпретатор печатает traceback в кодировке консоли (на русской
        # Windows это cp1251), а не в UTF-8. Читаем с запасом, иначе сама
        # проверка падает на разборе файла.
        raw = log.read_bytes()
        for encoding in ("utf-8", "cp1251", "cp866"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")

    failures = []
    if child.returncode == 0:
        failures.append("дочерний процесс не упал — проверять нечего")
    if MARKER not in text:
        failures.append("причина падения не попала в файл")
    if "Traceback" not in text:
        failures.append("в файле нет traceback")

    print(text)
    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: падение дочернего процесса записано в dictation-stderr.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
