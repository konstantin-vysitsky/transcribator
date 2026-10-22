from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _pythonw(project_root: Path) -> Path:
    from launch_helper import resolve_pythonw

    return resolve_pythonw(project_root)


def relaunch(
    project_root: Path,
    config_path: Path | None = None,
    *,
    delay_s: float = 0.8,
    parent_pid: int | None = None,
) -> None:
    """Запускает новый экземпляр приложения и завершает текущий."""
    root = project_root.resolve()
    launcher = (root / ".venv" / "Scripts" / "pythonw.exe").resolve()
    if not launcher.is_file():
        launcher = _pythonw(root)
    cmd = [str(launcher), str(root / "spawn_tray.py")]
    if config_path is not None:
        cmd.extend(["--config", str(config_path.resolve())])
    if parent_pid is not None:
        cmd.extend(["--parent-pid", str(parent_pid)])

    time.sleep(delay_s)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS

    from launch_helper import venv_env

    subprocess.Popen(
        cmd,
        cwd=str(root),
        env={**os.environ, **venv_env(root)},
        creationflags=creationflags,
        close_fds=True,
    )
