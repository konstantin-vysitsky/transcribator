"""Разведка рабочего стола для живой проверки: окна, мышь, клавиши.

Запуск: `.venv\\Scripts\\python.exe tests\\check_live.py`

Часть «разведка» ничего не запускает и не нажимает: она печатает, какие
окна есть на машине, куда идут строки и куда станут окна приложения. Живая
проверка стартует приложение со своими временными настройками и своим
журналом; настоящие `config.json`, `config.local.json`, `записи/` и
`prompt.txt` не трогаются.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
LOG_NAME = "dictation.log"
STARTED_PIDS: list[int] = []

_user32 = ctypes.WinDLL("user32", use_last_error=True)


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ок   " if condition else "  ПЛОХО ") + name + ("" if condition else f" {detail}"), flush=True)
    if not condition:
        FAILURES.append(name)


def wait_for(condition, limit_s: float = 6.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step_s)
    return False


# --- рабочий стол -------------------------------------------------------------


def _windows_of(pid: int) -> list[dict[str, Any]]:
    from ctypes import wintypes

    found: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner))
        if pid and owner.value != pid:
            return True
        title = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(wintypes.HWND(hwnd), title, 512)
        rect = wintypes.RECT()
        _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        found.append(
            {
                "hwnd": int(hwnd),
                "pid": int(owner.value),
                "title": title.value,
                "visible": bool(_user32.IsWindowVisible(wintypes.HWND(hwnd))),
                "rect": (rect.left, rect.top, rect.right, rect.bottom),
            }
        )
        return True

    _user32.EnumWindows(_callback, 0)
    return found


def screen_rect() -> tuple[int, int, int, int]:
    from ctypes import wintypes

    rect = wintypes.RECT()
    _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return (rect.left, rect.top, rect.right, rect.bottom)


def foreground() -> dict[str, Any] | None:
    from ctypes import wintypes

    hwnd = wintypes.HWND(_user32.GetForegroundWindow())
    owner = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    title = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, title, 512)
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "hwnd": int(hwnd.value or 0),
        "pid": int(owner.value),
        "title": title.value,
        "visible": bool(_user32.IsWindowVisible(hwnd)),
        "rect": (rect.left, rect.top, rect.right, rect.bottom),
    }


def press_f7() -> None:
    """Нажать и подержать F7: так нажимает человек в режиме удержания."""
    _user32.keybd_event(0x70, 0, 0, 0)
    time.sleep(0.8)
    _user32.keybd_event(0x70, 0, 2, 0)


def click(x: int, y: int) -> None:
    from ctypes import wintypes

    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    _user32.mouse_event(0x0002, 0, 0, 0, ctypes.c_void_p(0))
    time.sleep(0.06)
    _user32.mouse_event(0x0004, 0, 0, 0, ctypes.c_void_p(0))


# --- своё приложение ----------------------------------------------------------


def make_run_root() -> Path:
    """Временная папка приложения: настройки, журнал, промпт и записи."""
    root = Path(tempfile.mkdtemp(prefix="dictation-live-"))
    real = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cfg = dict(real)
    cfg.update(
        {
            "hotkey": "f7",
            "hotkey_mode": "hold",
            "paste_auto_enter": False,
            "paste_append_space": True,
            "sound_feedback": False,
            "ai_enabled": True,
            "autostart": False,
            "panel_style": "black",
            "panel_theme": "dark",
        }
    )
    (root / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "config.local.json").unlink(missing_ok=True)
    return root


def start_app(root: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    command = [
        str(ROOT / ".venv" / "Scripts" / "python.exe"),
        "-m",
        "dictation.main",
        "--tray",
        "--config",
        str(root / "config.json"),
    ]
    # Рабочая папка — корень проекта: в нём лежит `launch_helper`, без
    # которого приложение не находит интерпретатор для перезапуска.
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    STARTED_PIDS.append(process.pid)
    return process


def stop_app(process: subprocess.Popen) -> None:
    """Остановить только тот экземпляр, который запустила проверка.

    Приложение разрешает один экземпляр на машину: диктовку и панель поднимает
    один из двух процессов запуска, а другой только сторожит. Поэтому убиваем
    пару процессов именно этого запуска: запускающая копия останавливается
    первой, сторожащая — второй. Чужие процессы не трогаются.
    """
    if process.poll() is not None:
        return
    for pid in (process.pid, *_children(process.pid)):
        if _looks_like_ours(pid):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _children(parent: int) -> list[int]:
    """Дочерние процессы: пару процессов одного запуска приложения."""
    command = (
        "(Get-CimInstance Win32_Process -Filter \"ParentProcessId="
        + str(parent)
        + "\").ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in result.stdout.split() if line.isdigit()]


def _looks_like_ours(pid: int) -> bool:
    """Тот ли это процесс: тот же способ запуска и те же настройки."""
    command = (
        "(Get-CimInstance Win32_Process -Filter \"ProcessId="
        + str(pid)
        + "\").CommandLine"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "dictation.main" in result.stdout and "dictation-live-" in result.stdout


def log_text(root: Path) -> str:
    try:
        return (root / LOG_NAME).read_text(encoding="utf-8")
    except OSError:
        return ""


def visible_panels(pid: int) -> list[dict[str, Any]]:
    """Окна панели: видимые и шириной ближе к макету панели, чем к другим окнам."""
    return [
        window
        for window in _windows_of(pid)
        if window["visible"] and 300 <= window["rect"][2] - window["rect"][0] <= 520
    ]


# --- проверки -----------------------------------------------------------------


def explore() -> None:
    print("рабочий стол:")
    print(f"       экран: {screen_rect()}")
    current = foreground()
    print(f"       на переднем плане: {current}")
    print("       окна с заголовком:")
    for window in _windows_of(0):
        if window["visible"] and window["title"]:
            print(f"         {window['pid']:>6} {window['title'][:60]!r} {window['rect']}")


def test_start(root: Path) -> subprocess.Popen | None:
    print("запуск в трее:")
    process = start_app(root)
    ready = wait_for(lambda: "слушатель клавиш запущен" in log_text(root), 180.0, 0.3)
    if not ready:
        # Второй экземпляр приложение не запускает: запрет держит живая
        # диктовка. Её окна не трогаем, а причину говорим прямо.
        check(
            "приложение поднялось и слышит клавишу",
            False,
            "работает другой экземпляр: " + log_text(root)[-200:],
        )
        return None
    check("приложение поднялось и слышит клавишу", True)
    time.sleep(2.0)
    check("панель сама не появилась", not visible_panels(process.pid), repr(visible_panels(process.pid)))
    check("в журнале нет сбоев интерфейса", "интерфейс:" not in log_text(root), log_text(root)[-300:])
    return process


def _panel_place(window: dict[str, Any]) -> str:
    left, top, right, bottom = window["rect"]
    _sl, _st, _sr, _sb = screen_rect()
    return f"{right - left}x{bottom - top} в {left},{top}; до правого края {_sr - right}, до нижнего {_sb - bottom}"


def test_record_and_cancel(process: subprocess.Popen, root: Path) -> None:
    """Запись по F7, панель на месте, крестик отменяет вставку."""
    print("запись по F7:")
    press_f7()
    shown = wait_for(lambda: visible_panels(process.pid), 10.0, 0.1)
    check("панель появилась на записи", shown, repr(visible_panels(process.pid)))
    if not shown:
        return
    panel = visible_panels(process.pid)[0]
    print(f"       панель: {_panel_place(panel)}")
    left, top, right, bottom = panel["rect"]
    width, height = right - left, bottom - top
    check("ширина панели по макету", 300 <= width <= 520, str(width))
    check("высота панели по макету", 90 <= height <= 400, str(height))
    check("запись началась", "запись начата" in log_text(root), log_text(root)[-200:])

    time.sleep(1.5)
    press_f7()
    journal_before = log_text(root)
    # Крестик: правый верхний угол панели, 16 точек от края.
    scale = max(1.0, width / 396.0)
    click(right - round(24 * scale), top + round(31 * scale))
    gone = wait_for(lambda: not visible_panels(process.pid), 8.0, 0.1)
    check("после крестика панель ушла", gone, repr(visible_panels(process.pid)))
    time.sleep(1.5)
    check("после крестика в журнале нет вставки", "вставлено" not in log_text(root)[len(journal_before):])


def test_done_button(process: subprocess.Popen, root: Path) -> None:
    """«Готово» доводит запись до распознавания и вставки."""
    print("кнопка «Готово»:")
    press_f7()
    shown = wait_for(lambda: visible_panels(process.pid), 10.0, 0.1)
    check("панель появилась снова", shown, repr(visible_panels(process.pid)))
    if not shown:
        return
    panel = visible_panels(process.pid)[0]
    left, top, right, bottom = panel["rect"]
    width = right - left
    scale = max(1.0, width / 396.0)
    # «Готово»: 32x32 точки в 8 точках левее крестика.
    time.sleep(1.5)
    click(right - round(24 * scale) - round(8 * scale) - round(16 * scale), top + round(31 * scale))
    stopped = wait_for(lambda: "запись сохранена" in log_text(root), 8.0, 0.1)
    check("«Готово» закончило запись", stopped, log_text(root)[-200:])
    check("после «Готово» панель ушла", wait_for(lambda: not visible_panels(process.pid), 8.0, 0.1))


def test_prompt_window(root: Path) -> None:
    print("окно промпта:")
    prompt = root / "prompt.txt"
    text = prompt.read_text(encoding="utf-8") if prompt.exists() else ""
    check("промпт заведён при запуске", bool(text.strip()))
    check("записи легли во временную папку", (root / "записи").is_dir(), str(root / "записи"))


def main(mode: str) -> int:
    if mode == "explore":
        explore()
        return 0
    root = make_run_root()
    print(f"временная папка: {root}")
    process = test_start(root)
    if process is not None:
        try:
            if mode == "start":
                print("окна приложения:", _windows_of(process.pid))
                print("на переднем плане:", foreground())
                print("журнал:", log_text(root).strip().splitlines()[-4:])
            else:
                test_prompt_window(root)
                test_record_and_cancel(process, root)
                time.sleep(1.0)
                test_done_button(process, root)
                print("журнал:", log_text(root).strip().splitlines()[-8:])
        finally:
            stop_app(process)
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}")
        return 1
    print("Всё прошло.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
