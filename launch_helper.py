from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_pythonw(root: Path | None = None) -> Path:
    """Return pythonw.exe that runs this venv as a single process on Windows."""
    base = (root or project_root()).resolve()
    cfg = base / ".venv" / "pyvenv.cfg"
    if cfg.is_file():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^executable\s*=\s*(.+)$", line.strip())
            if match:
                pythonw = Path(match.group(1).strip()).with_name("pythonw.exe")
                if pythonw.is_file():
                    return pythonw.resolve()
    fallback = base / ".venv" / "Scripts" / "pythonw.exe"
    if fallback.is_file():
        return fallback.resolve()
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
