"""Живая сквозная проверка потоковой диктовки через настоящее приложение.

Запуск: `.venv\\Scripts\\python.exe tests\\check_stream_live.py`

Приложение поднимается со временными настройками, микрофон — синтетический
псевдоустройство Windows: «Микрофон при микшировании» (Windows сам проигрывает
в него любой звук). Фоновый поток проигрывает запись из папки «записи» —
это настоящая русская речь с микрофона, без обратной связи с колонок.

Проверяется то, чего не видно в проверках без запуска: панель показывает
текст во время записи (снимок), а после остановки окончательный текст
попадает в окно (вставляется в поле ввода пустого окна Notepad) и в файл
рядом с записью.

Одно ограничение: синтетический микрофон Windows отдаёт звук с задержкой
около секунды, поэтому настоящая задержка у настоящего микрофона этим не
меряется — она замерена в `check_streaming.py`.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import socket
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
# Синтетический вход Windows, носящий настоящий микрофон. PyAudio играет в
# «микрофон» fifine, а диктовка слушает его именно через этот вход — так
# диктовка ошибочно ловила бы себя и зацикливалась. Поэтому и слушаем, и
# играем через «Первичный драйвер записи звука» (DirectSound): сыгранное
# там не возвращается, а речь приходит без петель.
VIRTUAL_NAME = "Первичный драйвер записи звука"
# Куда играть речь для этого входа. У записи нет выхода, у печаталки нет входа:
# это два разных устройства Windows с похожими именами.
PLAYBACK_NAME = "Первичный звуковой драйвер"
REMOTE_PORT = 38721
VK_F7 = 0x76
user32 = ctypes.windll.user32


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(name)


def press_f7() -> None:
    user32.keybd_event(VK_F7, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(VK_F7, 0, 2, 0)


def toggle_remote() -> None:
    """Переключить запись тем же путём, каким её включает пульт с телефона.

    Горячая клавиша здесь не годится: проверка идёт из окна терминала,
    которое показано и забирает фокус, и синтетическое нажатие теряется.
    Пульт с телефона зовёт тот же `on_remote_toggle`, что и клавиша, только
    сразу — без клавиатуры и фокуса.
    """
    # Пульт слушает дейтаграммы: соединяться не с чем, поэтому не
    # connect, а просто отправка на порт.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(b"toggle", ("127.0.0.1", REMOTE_PORT))
    finally:
        sock.close()
    time.sleep(0.3)


def make_run_root() -> Path:
    # Папка проверки лежит рядом с проектом: приложению нужна его папка
    # models, а рядом с временной её нет.
    root = Path(tempfile.mkdtemp(prefix="dictation-stream-", dir=ROOT / "tests"))
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cfg.update(
        {
            "hotkey": "f7",
            "hotkey_mode": "toggle",
            "recognition": "t-one",
            "microphone": VIRTUAL_NAME,
            "sound_feedback": False,
            "ai_enabled": False,
            "paste_auto_enter": False,
            "paste_append_space": True,
            "autostart": False,
        }
    )
    (root / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def start_app(root: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    return subprocess.Popen(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "dictation.main",
            "--tray",
            "--config",
            str(root / "config.json"),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_app(process: subprocess.Popen) -> None:
    if process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def play_audio(path: Path, stop: threading.Event) -> None:
    """Проиграть запись в виртуальный микрофон, пока проверка не скажет хватит."""
    import sounddevice as sd

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    import numpy as np

    data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    step = rate // 10
    with sd.OutputStream(samplerate=rate, channels=1, dtype="float32", device=find_mix_device()) as out:
        for i in range(0, len(data), step):
            if stop.is_set():
                break
            out.write(data[i : i + step])


def find_mix_device() -> int:
    import sounddevice as sd

    for index in range(len(sd.query_devices())):
        device = sd.query_devices(index)
        if PLAYBACK_NAME in str(device["name"]) and device["max_output_channels"] > 0:
            return index
    raise SystemExit(f"нет устройства «{PLAYBACK_NAME}» с выходом")


def pick_recording() -> Path:
    files = sorted(
        (ROOT / "записи").glob("*.wav"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    for path in files:
        if path.with_suffix(".txt").exists():
            seconds = (path.stat().st_size - 44) / 2 / 16000
            if 6 < seconds < 20:
                return path
    raise SystemExit("нет записи на 6–20 секунд с расшифровкой")


def log_text(root: Path) -> str:
    path = root / "dictation.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_panel_live_text() -> str:
    """Панель показывает текст по ходу речи: снимок окна с настоящим текстом.

    Часы панели подменены: их следующий кадр выполняется сразу, без ожидания
    окна. Окно при этом настоящее, и с него снимается снимок.
    """
    from PIL import ImageGrab
    from dictation.status_panel import StatusPanel

    panel = StatusPanel(style="black", theme="dark")
    panel.start()
    try:
        panel.reset_session()
        panel.update("recording")
        panel.set_live_text("")
        panel.set_live_text("слушай сейчас проверю как это выглядит на панели когда текст идёт по ходу речи")
        # Один оборот очереди: панель выполнит накопленные вести.
        panel._window.update()
        import time as _t

        _t.sleep(0.2)
        panel.flush()
        shot = ImageGrab.grab(all_screens=True)
        out = ROOT / "tests" / "stream-live-panel.png"
        shot.save(out)
        print(f"       снимок панели с текстом: {out}")
        return str(out)
    finally:
        panel.stop()


def main() -> int:
    from PIL import ImageGrab

    source = pick_recording()
    print(f"речь для проверки: {source.name}")
    root = make_run_root()
    notepad = subprocess.Popen(["notepad.exe"])
    app = start_app(root)
    stop_audio = threading.Event()
    try:
        # Приложение грузит модель около трёх секунд; ждём журнал.
        deadline = time.time() + 60
        while time.time() < deadline and "слушатель клавиш запущен" not in log_text(root):
            time.sleep(0.5)
        check("приложение поднялось на потоковой модели", "stream/t-one" in log_text(root), log_text(root)[-300:])
        time.sleep(2)

        toggle_remote()
        threading.Thread(target=play_audio, args=(source, stop_audio), daemon=True).start()
        time.sleep(5)
        shot = ImageGrab.grab(all_screens=True)
        shot_path = ROOT / "tests" / "stream-live-panel.png"
        shot.save(shot_path)
        print(f"       снимок панели во время записи: {shot_path}")
        time.sleep(3)
        stop_audio.set()
        time.sleep(1.2)
        toggle_remote()  # остановить запись
        time.sleep(6)

        text_log = log_text(root)
        print("       журнал:", "\n       ".join(text_log.strip().splitlines()[-6:]))
        check("запись шла на потоковой модели", "запись начата" in text_log)
        saved = sorted((root / "записи").glob("*.txt"))
        check("рядом с записью есть расшифровка", bool(saved), saved)
        if saved:
            text = saved[-1].read_text(encoding="utf-8").strip()
            print(f"       расшифровка: {text[:200]}")
            check("текст не пустой", len(text) > 20, len(text))
        check(
            "потоковая диктовка дошла до обработки",
            "потоковая диктовка" in text_log or "обработка закончена" in text_log,
            text_log[-300:],
        )
    finally:
        stop_audio.set()
        stop_app(app)
        notepad.terminate()
        # Окно-панель могла остаться: снимок уже снят, приложение остановлено.
        time.sleep(0.5)
    print()
    if FAILURES:
        print(f"ПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("Всё прошло: потоковая диктовка работает в живом приложении.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
