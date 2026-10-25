from __future__ import annotations

import time
from pathlib import Path


def relaunch(
    project_root: Path,
    config_path: Path | None = None,
    *,
    delay_s: float = 0.8,
    parent_pid: int | None = None,
) -> None:
    """Запускает новый экземпляр приложения и завершает текущий."""
    from launch_helper import spawn_detached

    root = project_root.resolve()
    cmd = [str(root / ".venv" / "Scripts" / "pythonw.exe"), str(root / "spawn_tray.py")]
    if config_path is not None:
        cmd.extend(["--config", str(config_path.resolve())])
    if parent_pid is not None:
        cmd.extend(["--parent-pid", str(parent_pid)])

    time.sleep(delay_s)

    # Отсоединённый запуск (`DETACHED_PROCESS`) отнимает окна у новой копии
    # целиком — и панель диктовки Windows начинает держать на панели задач
    # отдельной кнопкой. `spawn_detached` скрывает окно, но оставляет консоль.
    spawn_detached(cmd, root)
