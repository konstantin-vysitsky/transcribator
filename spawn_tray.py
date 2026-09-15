from __future__ import annotations

import argparse
import os
import subprocess
import sys

from launch_helper import project_root, resolve_pythonw, venv_env


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch dictation tray in background")
    parser.add_argument("--config", type=str, default=None)
    args = parser.parse_args()

    root = project_root()
    cmd = [str(resolve_pythonw(root)), "-m", "dictation.main", "--tray"]
    if args.config:
        cmd.extend(["--config", args.config])

    kwargs: dict = {
        "cwd": str(root),
        "env": venv_env(root),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(cmd, **kwargs)


if __name__ == "__main__":
    main()
