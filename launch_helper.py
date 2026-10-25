from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent


def quiet_kwargs() -> dict:
    """Не дать Windows открыть окно консоли у дочернего процесса.

    `python.exe` запускает пакетный файл, `cmd`, `taskkill` и прочие консольные
    программы через окно консоли, если её не отключить явно. Окно живёт пару
    секунд и мигает поверх всего экрана — это и был видимый всплеск.
    """
    if sys.platform != "win32":
        return {}
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE: окно просится скрытым
    kwargs["startupinfo"] = startupinfo
    return kwargs


def venv_env(root: Path | None = None) -> dict[str, str]:
    base = (root or project_root()).resolve()
    venv = base / ".venv"
    site_packages = venv / "Lib" / "site-packages"
    env = dict(os.environ)
    env["VIRTUAL_ENV"] = str(venv)
    scripts = str(venv / "Scripts")
    path = env.get("PATH", "")
    if scripts.lower() not in path.lower():
        env["PATH"] = f"{scripts};{path}" if path else scripts
    if site_packages.is_dir():
        env["PYTHONPATH"] = str(site_packages)
    return env


def venv_python(root: Path | None = None) -> Path:
    """Интерпретатор, который умеет запускать код проекта.

    Запуск базовым `python.exe` без окружения venv обходит мост `.venv` и
    теряет пакеты проекта — так падает всё, что запущено через `-m`.
    """
    base = (root or project_root()).resolve()
    venv_python = base / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file():
        return venv_python.resolve()
    return Path(sys.executable).resolve()


def spawn_detached(cmd: list[str], root: Path | None = None) -> subprocess.Popen:
    """Запустить копию приложения, которая переживёт вызвавшего.

    Широкое отключение окон (`DETACHED_PROCESS`) действует на всю порождённую
    от копии цепочку процессов: у неё не остаётся ни окна, ни собственной
    консоли. Первое же окно, которое ей понадобится, — служебное окно Tk с
    панелью — Windows тогда показывает на панели задач отдельной кнопкой, и
    закреплённый значок теряет полоску работы. Поэтому копия запускается с
    обычной, но скрытой консолью.
    """
    base = (root or project_root()).resolve()
    return subprocess.Popen(
        cmd,
        cwd=str(base),
        env=venv_env(base),
        close_fds=True,
        **quiet_kwargs(),
    )


def resolve_pythonw(root: Path | None = None) -> Path:
    """Return pythonw.exe that runs this project's venv.

    Важно: в pyvenv.cfg строка ``executable`` указывает на базовый Python
    (например ``C:\\Program Files\\Python312\\python.exe``), а не на venv.
    Запуск такого pythonw.exe идёт без моста venv: `python -m` не находит
    пакеты проекта (gigaam, pystray) и приложение молча падает. Поэтому берём
    pythonw.exe самого venv. Он же в самом venv ссылается на python.exe venv,
    то есть мост на месте.
    """
    base = (root or project_root()).resolve()
    venv_pythonw = base / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.is_file():
        return venv_pythonw.resolve()
    venv_python = base / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file():
        return venv_python.resolve()
    return Path(sys.executable).with_name("pythonw.exe").resolve()
