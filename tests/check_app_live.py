"""Проверка всего пути диктовки: от звука до приписки в поле.

Запуск: `.venv\\Scripts\\python.exe tests\\check_app_live.py`

Поднимается само приложение (`dictation.main`), но микрофон подменён:
вместо звуковой карты отдаётся записанный wav, кусками, в настоящем
времени. Приложение получает звук как обычно и должно дописывать слова
в поле ПО ХОДУ, а не только в конце.

Вставка при этом не проверяется на глаз — важно, что она вызывается.
Поэтому в журнале ищутся строки «приписка … вставлено=да» с секундами
меньше длительности записи.

Запускать при остановленном приложении: порт 38721 должен быть свободен.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

FAILURES: list[str] = []
LOG = ROOT / "dictation.log"


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  {'ок' if ok else 'НЕТ'}  {name}" + ("" if ok else f"  {detail}"))
    if not ok:
        FAILURES.append(name)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def pick_recording() -> Path:
    files = sorted(
        (ROOT / "записи").glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True
    )
    for path in files:
        seconds = (path.stat().st_size - 44) / 2 / 16000
        if 8 < seconds < 20 and float(np.abs(read_wav(path)).max()) > 0.005:
            return path
    raise SystemExit("нет записи со звуком 8–20 секунд в «записи»")


class FakeRecorder:
    """Микрофон из файла: отдаёт звук кусками, как настоящая карта."""

    CHUNK = 320  # 20 мс при 16 кГц

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.on_level = None
        self.on_audio_chunk = None
        self._recording = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._recording:
            return False
        self._recording = True
        self._thread = threading.Thread(target=self._feed, daemon=True, name="fake-mic")
        self._thread.start()
        return True

    def _feed(self) -> None:
        started = time.monotonic()
        sent = 0
        while self._recording and sent < len(self.audio):
            piece = self.audio[sent : sent + self.CHUNK]
            sent += len(piece)
            if self.on_audio_chunk is not None:
                try:
                    self.on_audio_chunk(piece)
                except Exception:
                    pass
            # Держим настоящий темп: 20 мс звука за 20 мс времени.
            delay = started + sent / 16000.0 - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def stop_with_noise(self):
        self._recording = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return self.audio.copy(), None

    def stop(self):
        return self.stop_with_noise()[0]

    def is_recording(self) -> bool:
        return self._recording

    def is_silent(self) -> bool:
        return False

    def current_level(self) -> float:
        return 0.5

    def set_device(self, _choice) -> None:
        pass

    def input_device_name(self) -> str:
        return "проверка (запись из файла)"

    def close(self) -> None:
        self._recording = False


def send_toggle() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"toggle", ("127.0.0.1", 38721))
    sock.close()


def main() -> int:
    if "--in-app" in sys.argv:
        # Второй заход в тот же файл: здесь уже поднимается приложение.
        import dictation.main as app

        app.Recorder = lambda *_a, **_kw: FakeRecorder(AUDIO)
        app.main()
        return 0

    if "--live" not in sys.argv:
        # Проверка вставляет настоящий текст в переднее окно. Если человек
        # в это время играет, слова уедут в игру — так уже было один раз.
        # Поэтому она запускается только по явной просьбе.
        print("пропущено: живая проверка запускается с --live")
        return 0

    recording = pick_recording()
    audio = read_wav(recording)
    duration = len(audio) / 16000
    print(f"запись: {recording.name}, {duration:.1f} с")

    before = read_log().count("\n")

    # Приложение поднимается отдельным процессом: его выход не должен
    # утащить за собой проверку.
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "tests" / "_app_child.py"), str(recording)],
        cwd=str(ROOT),
    )
    print("ждём загрузки модели…")
    deadline = time.monotonic() + 40
    loaded = False
    while time.monotonic() < deadline:
        if "модель загружена" in new_lines(before):
            loaded = True
            break
        time.sleep(0.5)
    check("приложение поднялось и загрузило модель", loaded)
    if not loaded:
        proc.kill()
        return 1
    time.sleep(1.0)

    print("нажимаем F7 (через UDP) и говорим записанным голосом…")
    send_toggle()
    time.sleep(duration + 1.0)
    print("останавливаем: второй F7")
    send_toggle()

    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if "обработка закончена" in new_lines(before):
            break
        time.sleep(0.5)
    time.sleep(1.0)
    lines = new_lines(before)
    proc.kill()

    appended = [
        line for line in lines.splitlines()
        if "приписка" in line and "вставлено=да" in line
    ]
    deferred = [line for line in lines.splitlines() if "отложена" in line]
    final = [line for line in lines.splitlines() if "в поле дописано" in line]

    print(f"приписок в поле: {len(appended)}")
    for line in appended[:5]:
        print("   ", line.strip())
    if deferred:
        print(f"отложено: {len(deferred)}")

    check("по ходу речи были приписки в поле", len(appended) >= 3, len(appended))
    seconds = []
    for line in appended:
        marker = line.split(": ", 1)[-1].split(" с,")[0]
        try:
            seconds.append(float(marker.replace(",", ".")))
        except ValueError:
            pass
    check(
        "приписки шли, пока человек говорил",
        bool(seconds) and min(seconds) < duration * 0.6,
        (min(seconds) if seconds else None, round(duration, 1)),
    )
    check("в конце приписка тоже была", bool(final), final[:1])

    if FAILURES:
        print(f"\nПРОВАЛЕНО: {len(FAILURES)}: {FAILURES}")
        return 1
    print("\nВсё прошло: приложение дописывает слова в поле по ходу речи.")
    return 0


def read_log() -> str:
    """Журнал целиком: он в UTF-8, но старые строки бывали в cp1251."""
    data = LOG.read_bytes()
    for codec in ("utf-8", "cp1251"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def new_lines(mark: int) -> str:
    text = read_log()
    return "\n".join(text.splitlines()[mark:])


if __name__ == "__main__":
    sys.exit(main())
