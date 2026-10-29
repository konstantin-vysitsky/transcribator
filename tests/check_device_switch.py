"""Проверка: запись подхватывает смену микрофона без перезапуска.

Микрофон вынимают и вставляют, Windows переключает устройство по умолчанию.
Старый поток при этом остаётся привязанным к прежнему железу и пишет тишину
либо ничего. Снаружи это «диктовка перестала понимать», и лечилось только
перезапуском приложения.

Проверка подменяет «устройство по умолчанию» и убеждается, что запись
открывает поток заново, а не держится за прежний.

Запуск: .venv\\Scripts\\python.exe tests\\check_device_switch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dictation import recorder as rec_mod  # noqa: E402


class FakeStream:
    """Поток, который считает, сколько раз его открывали."""

    opened: list[str] = []

    def __init__(self, samplerate, channels, dtype, callback, **kwargs) -> None:
        self.samplerate = samplerate
        self.active = False
        self.callback = callback
        self.device = 0

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


def main() -> int:
    failures = []
    current = {"name": "Микрофон (fifine Microphone)"}
    opens = {"count": 0}

    class CountingStream(FakeStream):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            opens["count"] += 1

    real_input_stream = rec_mod.sd.InputStream
    real_query = rec_mod.sd.query_devices
    real_rec = rec_mod.sd.rec

    def fake_query(*args, **kwargs):
        if kwargs.get("kind") == "input":
            return {"name": current["name"]}
        return {"name": current["name"]}

    rec_mod.sd.InputStream = CountingStream
    rec_mod.sd.query_devices = fake_query
    try:
        rec = rec_mod.Recorder(min_duration_s=0.0, preroll=False)

        rec.start()
        rec.stop()
        first = opens["count"]
        if first != 1:
            failures.append(f"первая запись открыла поток {first} раз вместо 1")

        # Микрофон выдернули, Windows переключилась на другой.
        current["name"] = "Микрофон (C922 Pro Stream Webcam)"

        rec.start()
        rec.stop()
        second = opens["count"]
        if second != 2:
            failures.append(
                f"после смены устройства поток не переоткрылся "
                f"(всего открытий {second}, ожидалось 2)"
            )

        # Смена не повторилась — поток должен переиспользоваться.
        rec.start()
        rec.stop()
        third = opens["count"]
        if third != 2:
            failures.append(f"поток переоткрыт без нужды (всего {third}, ожидалось 2)")

        rec.close()
        if rec._stream is not None:
            failures.append("после close поток остался")
        if rec._opened_signature:
            failures.append("после close остался признак устройства")

        print(f"открытий потока: первая={first} после смены={second} повтор={third}")
    finally:
        rec_mod.sd.InputStream = real_input_stream
        rec_mod.sd.query_devices = real_query
        rec_mod.sd.rec = real_rec

    if failures:
        for f in failures:
            print("ПРОВАЛ:", f)
        return 1
    print("ОК: смена микрофона подхватывается, лишних переоткрытий нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
