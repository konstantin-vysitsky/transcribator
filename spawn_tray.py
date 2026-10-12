from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from launch_helper import project_root, resolve_pythonw, venv_env

LOG_NAME = "dictation.log"


def _log(root: Path, message: str) -> None:
    try:
        with (root / LOG_NAME).open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch dictation tray in background")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    root = project_root()
    interpreter = resolve_pythonw(root)
    cmd = [str(interpreter), "-m", "dictation.main", "--tray"]
    if args.config:
        cmd.extend(["--config", args.config])

    _log(root, f"запуск: {' '.join(cmd)}")

    env = venv_env(root)
    # Признак ошибки запуска: без него падение дочернего процесса выглядит
    # снаружи как «нажал F7 и ничего не произошло».
    env.pop("PYTHONPATH", None)

    kwargs: dict = {
        "cwd": str(root),
        "env": env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    handle = subprocess.Popen(cmd, **kwargs)
    _log(root, f"процесс запущен, pid={handle.pid}")


if __name__ == "__main__":
    main()
