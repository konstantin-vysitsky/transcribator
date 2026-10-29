"""Проверка: брошенный запрет не мешает запуску.

Файл с pid переживает аварийное падение владельца. Если верить только ему,
новая копия считает, что приложение уже работает, и молча выходит — а
снаружи это «запустил, а его нет».

Проверка подкладывает в файл pid заведомо мёртвого процесса и убеждается,
что запрет не считается занятым.

Запуск: .venv\\Scripts\\python.exe tests\\check_stale_lock.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation import single_instance  # noqa: E402

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

# Чужой процесс: живёт недолго и завершается сам, оставляя после себя
# переиспользуемый pid.
SHORT_LIVED = "import time; time.sleep(0.2)"

IS_HOLDER = """
import sys
sys.path.insert(0, r"{root}")
from dictation.single_instance import _holder_is_dictation
print("YES" if _holder_is_dictation() else "NO", flush=True)
"""


def main() -> int:
    failures = []

    # 1. Владелец умер, файл остался.
    dead = subprocess.Popen([str(PYTHON), "-c", SHORT_LIVED])
    dead_pid = dead.pid
    dead.wait()
    single_instance.LOCK_PATH.write_text(str(dead_pid), encoding="utf-8")

    holder = single_instance._holder_is_dictation()
    print(f"мёртвый pid {dead_pid}: считается держателем? {holder}")
    if holder:
        failures.append("мёртвый владелец считается живым")

    # 2. Живой, но чужой процесс в файле.
    alive = subprocess.Popen([str(PYTHON), "-c", "import time; time.sleep(30)"])
    single_instance.LOCK_PATH.write_text(str(alive.pid), encoding="utf-8")
    holder2 = single_instance._holder_is_dictation()
    print(f"живой чужой pid {alive.pid}: считается держателем? {holder2}")
    if holder2:
        failures.append("чужой живой процесс считается приложением")
    alive.kill()
    alive.wait()

    # 3. Живой процесс диктовки обязан считаться держателем.
    mine = subprocess.Popen(
        [str(PYTHON), "-c", "import time; time.sleep(30)",
         "-m", "dictation.main", "--stub"],
    )
    single_instance.LOCK_PATH.write_text(str(mine.pid), encoding="utf-8")
    holder3 = single_instance._holder_is_dictation()
    print(f"процесс диктовки {mine.pid}: считается держателем? {holder3}")
    if not holder3:
        failures.append("настоящее приложение не считается держателем")
    mine.kill()
    mine.wait()

    single_instance.LOCK_PATH.unlink(missing_ok=True)

    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: брошенный запрет не блокирует запуск, чужой pid не путается с приложением")
    return 0


if __name__ == "__main__":
    sys.exit(main())
