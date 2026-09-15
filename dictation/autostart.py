from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

STARTUP_DIR = (
    Path(os.environ["APPDATA"])
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "Startup"
)
AUTOSTART_LINK = STARTUP_DIR / "Dictation.lnk"
LEGACY_AUTOSTART_VBS = STARTUP_DIR / "dictation_autostart.vbs"
TASK_NAME = "Dictation"


def _powershell_exe() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )


def _hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW),
    }


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _scheduled_task_exists() -> bool:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        check=False,
        **_hidden_subprocess_kwargs(),
    )
    return result.returncode == 0


def is_autostart_enabled() -> bool:
    return AUTOSTART_LINK.is_file() or _scheduled_task_exists()


def _register_scheduled_task(project_root: Path) -> bool:
    vbs = (project_root / "autostart-launch.vbs").resolve()
    if not vbs.is_file():
        return False

    vbs_arg = _ps_quote(str(vbs))
    ps = (
        f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue; "
        f"$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '\"\"{vbs_arg}\"\"'; "
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; "
        "$trigger.Delay = 'PT20S'; "
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null"
    )
    result = subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ps,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        **_hidden_subprocess_kwargs(),
    )
    return result.returncode == 0


def _create_startup_shortcut(project_root: Path) -> None:
    root = project_root.resolve()
    vbs = (root / "autostart-launch.vbs").resolve()
    if not vbs.is_file():
        raise FileNotFoundError(f"Не найден скрипт автозапуска: {vbs}")

    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    if LEGACY_AUTOSTART_VBS.is_file():
        LEGACY_AUTOSTART_VBS.unlink()

    link = _ps_quote(str(AUTOSTART_LINK))
    target = _ps_quote(str(vbs))
    workdir = _ps_quote(str(root))
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('"
        f"{link}"
        "'); "
        "$s.TargetPath = 'wscript.exe'; "
        f"$s.Arguments = '\"\"{target}\"\"'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        "$s.WindowStyle = 7; "
        "$s.Description = 'Голосовая диктовка'; "
        "$s.Save()"
    )
    result = subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            ps,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        **_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Не удалось создать ярлык автозапуска: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def enable_autostart(project_root: Path) -> None:
    root = project_root.resolve()
    pythonw = (root / ".venv" / "Scripts" / "pythonw.exe").resolve()
    if not pythonw.is_file():
        raise FileNotFoundError(f"Не найден интерпретатор: {pythonw}")

    if _register_scheduled_task(root):
        if AUTOSTART_LINK.is_file():
            AUTOSTART_LINK.unlink()
        return

    _create_startup_shortcut(root)


def disable_autostart() -> None:
    subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false -ErrorAction SilentlyContinue",
        ],
        capture_output=True,
        check=False,
        **_hidden_subprocess_kwargs(),
    )
    if AUTOSTART_LINK.is_file():
        AUTOSTART_LINK.unlink()
    if LEGACY_AUTOSTART_VBS.is_file():
        LEGACY_AUTOSTART_VBS.unlink()
