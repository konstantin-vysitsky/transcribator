from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_pythonw(root: Path | None = None) -> Path:
    """Return pythonw.exe that runs this project's venv.

    Важно: в pyvenv.cfg строка ``executable`` указывает на базовый Python
    (например ``C:\\Program Files\\Python312\\python.exe``), а не на venv.
    Запуск такого pythonw.exe идёт без пакетов проекта (gigaam, pystray)
    и приложение молча падает. Поэтому берём pythonw.exe самого venv.
    """
    base = (root or project_root()).resolve()
    venv_pythonw = base / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.is_file():
        return venv_pythonw.resolve()
    venv_python = base / ".venv" / "Scripts" / "python.exe"
    if venv_python.is_file():
        return venv_python.resolve()
    return Path(sys.executable).with_name("pythonw.exe").resolve()


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
