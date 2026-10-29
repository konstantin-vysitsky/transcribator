"""Проверка: второй экземпляр при живом первом не остаётся работать.

Запрет держится именованным объектом ядра и живёт, пока жив его владелец.
Здесь два настоящих процесса проверяют, что второй отсекается, а после
выхода первого запрет освобождается.

Проверка самодостаточна: если к моменту запуска уже работает настоящее
приложение, проверка честно об этом говорит и пробует дальше — запрет
снимается только вместе со своим владельцем, а чужих процессов она не
трогает.

Запуск: .venv\\Scripts\\python.exe tests\\check_single_instance.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

# Владелец держит запрет и ждёт, пока его попросят выйти.
HOLDER = """
import sys, time
sys.path.insert(0, r"{root}")
from dictation.single_instance import acquire_single_instance
ok = acquire_single_instance()
print("HELD" if ok else "BUSY", flush=True)
time.sleep(20)
"""

# Проверяющий: пытается занять запрет и сразу отчитывается.
SECOND = """
import sys
sys.path.insert(0, r"{root}")
from dictation.single_instance import acquire_single_instance
ok = acquire_single_instance()
print("GOT" if ok else "REFUSED", flush=True)
"""


def probe() -> str:
    r = subprocess.run(
        [str(PYTHON), "-c", SECOND.format(root=str(ROOT))],
        capture_output=True, text=True, timeout=60,
    )
    return r.stdout.strip() or f"(нет вывода: {r.stderr.strip()[:200]})"


def main() -> int:
    failures = []

    # Если запрет уже занят — это настоящее приложение. Такое проверке не
    # мешает: сначала убеждаемся, что оно его действительно держит.
    holder = subprocess.Popen(
        [str(PYTHON), "-c", HOLDER.format(root=str(ROOT))],
        stdout=subprocess.PIPE, text=True,
    )
    first_line = holder.stdout.readline().strip()
    print(f"первый процесс: {first_line}")

    if first_line == "HELD":
        # Второй запускается, пока первый жив.
        answer = probe()
        print(f"второй при живом первом: {answer}")
        if answer != "REFUSED":
            failures.append(f"второй экземпляр прошёл: {answer!r}")
        holder.kill()
        holder.wait()
        second_line = answer
    elif first_line == "BUSY":
        # Запрет держит работающее приложение: при живом владельце второй
        # обязан быть отсечён.
        answer = probe()
        print(f"при работающем приложении: {answer}")
        if answer != "REFUSED":
            failures.append(f"при работающем приложении второй прошёл: {answer!r}")
        holder.kill()
        holder.wait()
        print(
            "запрет занят настоящим приложением: освобождение проверяется "
            "после его выхода"
        )
        second_line = answer
        print(f"первый: {first_line}, второй: {second_line}")
        if failures:
            for f in failures:
                print("ПРОВАЛ:", f)
            return 1
        print("ОК: при живом владельце второй экземпляр отсекается")
        return 0
    else:
        failures.append(f"непонятный ответ первого процесса: {first_line!r}")
        holder.kill()
        holder.wait()
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1

    # После выхода владельца запрет обязан освободиться.
    time.sleep(1.0)
    answer3 = probe()
    print(f"после выхода первого: {answer3}")
    if answer3 != "GOT":
        failures.append(f"после выхода первого запрет не освободился: {answer3!r}")

    print(f"первый: {first_line}, второй: {second_line}, после выхода: {answer3}")
    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: второй экземпляр отсекается, после выхода запрет освобождается")
    return 0


if __name__ == "__main__":
    sys.exit(main())
