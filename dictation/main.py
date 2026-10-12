from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

from dictation import ai, autostart, config
from dictation.hotkey import HotkeyListener, format_hotkey_label
from dictation.single_instance import acquire_single_instance
from dictation.remote_trigger import UdpToggleServer
from dictation.paster import capture_foreground_window, paste_text
from dictation.prompt import load_prompt
from dictation.recorder import SAMPLE_RATE, Recorder
from dictation.sounds import quiet_beep_start, quiet_beep_stop
from dictation.timing import (
    TranscribeTimer,
    estimate_transcribe_seconds,
    record_speed_sample,
)
from dictation.transcriber import Transcriber

LOG_NAME = "dictation.log"


def _log_line(project_root: Path, message: str) -> None:
    """Записать строку в журнал приложения.

    Приложение запускается без консоли, и без журнала у него нет ни одного
    способа сказать, что оно не поднялось. Нажатие горячей клавиши при этом
    выглядит как «ничего не произошло», и причину искать негде.
    """
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (project_root / LOG_NAME).open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {message}\n")
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Голосовой ввод (диктовка)")
    parser.add_argument("--config", type=Path, default=None, help="Путь к config.json")
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Иконка в системном трее (без окна консоли)",
    )
    args = parser.parse_args()
    if args.config is not None:
        config.CONFIG_PATH = args.config.resolve()

    if not acquire_single_instance():
        # Второй экземпляр молча выходит — а снаружи это выглядит как
        # «приложение не отвечает». Пишем причину в журнал.
        _log_line(config.CONFIG_PATH.parent, "второй экземпляр уже запущен, выход")
        return

    cfg = config.load_config()
    project_root = config.CONFIG_PATH.parent
    use_tray = args.tray
    busy = threading.Event()
    _log_line(project_root, "запуск приложения")

    # Промпт создаётся сразу при запуске, а не при первом обращении: иначе
    # файл появляется только после первой диктовки с обработкой, и найти его
    # заранее нечем.
    load_prompt(project_root)

    if cfg.get("autostart") and not autostart.is_autostart_enabled():
        autostart.enable_autostart(project_root)

    stop_event = threading.Event()
    transcribe_cancel = threading.Event()
    restart_requested = False
    tray_app = None
    hotkey: HotkeyListener | None = None
    recorder = Recorder(min_duration_s=cfg["min_utterance_s"])
    record_target_hwnd: int | None = None

    def request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        stop_event.set()

    def reset_tray_idle() -> None:
        if tray_app is not None:
            tray_app.set_activity("idle")

    def finish_recording_from_ui() -> None:
        if hotkey is not None:
            hotkey.reset_state()
        on_record_stop()

    def abort_transcription() -> None:
        transcribe_cancel.set()
        if hotkey is not None:
            hotkey.reset_state()
        if recorder.is_recording():
            if cfg.get("sound_feedback"):
                try:
                    quiet_beep_stop()
                except Exception:
                    pass
            recorder.stop()
            busy.clear()
            reset_tray_idle()
            return
        if tray_app is not None:
            tray_app.set_activity("transcribing", "остановка…")

    if use_tray:
        from dictation.tray import TrayApp

        tray_app = TrayApp(
            project_root=project_root,
            status_text="Диктовка: загрузка модели...",
            on_exit=stop_event.set,
            on_restart=request_restart,
            on_abort=abort_transcription,
            on_stop_recording=finish_recording_from_ui,
        )
        tray_app.start()

    print("Загрузка модели распознавания речи...")
    print(f"Потоки CPU в настройках: {cfg['cpu_threads']} (применяются при запуске)")
    _log_line(project_root, "загрузка модели распознавания речи")
    transcriber = Transcriber(
        model=cfg["model"],
        cpu_threads=cfg["cpu_threads"],
        remove_fillers_enabled=cfg.get("remove_fillers", True),
    )
    print(f"Модель загружена ({transcriber.runtime_label()})")
    _log_line(project_root, f"модель загружена: {transcriber.runtime_label()}")
    if tray_app is not None:
        tray_app.set_runtime_info(transcriber.runtime_label())

    def on_record_start() -> bool:
        nonlocal record_target_hwnd
        if busy.is_set():
            return False
        record_target_hwnd = capture_foreground_window()
        busy.set()
        transcribe_cancel.clear()
        try:
            if cfg.get("sound_feedback"):
                try:
                    quiet_beep_start()
                except Exception:
                    pass
            print("Запись...")
            if tray_app is not None:
                tray_app.reset_session()
                tray_app.set_activity("recording")
            recorder.start()
            _log_line(project_root, "запись начата")
            return True
        except Exception as exc:
            busy.clear()
            reset_tray_idle()
            print(f"Ошибка записи: {exc}")
            _log_line(project_root, f"ошибка записи: {exc}")
            return False

    def process_audio(audio: np.ndarray) -> None:
        timer: TranscribeTimer | None = None
        progress_thread: threading.Thread | None = None
        stop_progress = threading.Event()

        def _progress_loop() -> None:
            while not stop_progress.wait(0.5):
                if timer is None or tray_app is None:
                    continue
                tray_app.set_activity(
                    "transcribing",
                    timer.detail(),
                    timer.progress(),
                )

        try:
            print("Распознаю...")
            audio_duration = len(audio) / SAMPLE_RATE
            estimate = estimate_transcribe_seconds(
                audio_duration,
                transcriber.device,
            )
            timer = TranscribeTimer(estimate)
            if tray_app is not None:
                tray_app.set_activity("transcribing", timer.detail(), 0.0)
                progress_thread = threading.Thread(
                    target=_progress_loop,
                    daemon=True,
                    name="transcribe-progress",
                )
                progress_thread.start()

            if transcribe_cancel.is_set():
                return

            text = transcriber.transcribe(audio)
            if transcribe_cancel.is_set():
                return

            if not text or not text.strip():
                print("Слишком короткая фраза, пропуск")
                return

            raw_text = text
            ai_error: str | None = None
            # Кнопка на панели важнее настройки: она переключает обработку на
            # текущую сессию, а `ai_enabled` задаёт поведение по умолчанию.
            use_ai = cfg.get("ai_enabled", False)
            if tray_app is not None:
                use_ai = tray_app.ai_enabled()
            if use_ai and not transcribe_cancel.is_set():
                if tray_app is not None:
                    tray_app.set_activity("processing", "отправляю текст модели…")
                text, ai_error = ai.process_text(
                    text,
                    prompt=load_prompt(project_root),
                    api_key=str(cfg.get("ai_api_key", "")),
                    base_url=str(cfg.get("ai_base_url", ai.DEFAULT_BASE_URL)),
                    model=str(cfg.get("ai_model", ai.DEFAULT_MODEL)),
                    timeout_s=float(cfg.get("ai_timeout_s", ai.DEFAULT_TIMEOUT_S)),
                )
                if transcribe_cancel.is_set():
                    return
                if ai_error is not None:
                    # Обработка не удалась — вставляем то, что распознали.
                    # Потерять диктовку из-за чужого сбоя нельзя.
                    print(f"Обработка через ИИ не удалась ({ai_error}), вставляю как есть")
                else:
                    print("Текст обработан через ИИ")

            auto_enter = cfg.get("paste_auto_enter", False)
            if tray_app is not None and tray_app.should_skip_auto_enter():
                auto_enter = False

            pasted, enter_sent = paste_text(
                text,
                append_space=cfg["paste_append_space"],
                auto_enter=auto_enter,
                target_hwnd=record_target_hwnd,
            )
            if tray_app is not None:
                tray_app.add_transcription(text)
                if ai_error is not None:
                    tray_app.notify_ai_error(ai_error)

            if pasted:
                suffix = " + Enter" if enter_sent else ""
                print(f"Вставлено{suffix}: {text}")
            else:
                print("Не удалось вставить текст")
                _log_line(project_root, "не удалось вставить текст")
                if ai_error is None and text != raw_text:
                    print(f"Было распознано: {raw_text}")

            _log_line(project_root, f"вставлено ({len(text)} знаков)")

            if timer is not None:
                record_speed_sample(
                    transcriber.device,
                    audio_duration,
                    timer.elapsed(),
                )
        finally:
            stop_progress.set()
            if progress_thread is not None:
                progress_thread.join(timeout=1.0)
            if hotkey is not None:
                hotkey.reset_state()
            busy.clear()
            reset_tray_idle()

    def on_record_stop() -> bool:
        if not recorder.is_recording():
            return False
        if cfg.get("sound_feedback"):
            try:
                quiet_beep_stop()
            except Exception:
                pass
        audio = recorder.stop()
        if audio is None:
            busy.clear()
            reset_tray_idle()
            return True
        threading.Thread(
            target=process_audio,
            args=(audio,),
            daemon=True,
            name="process-audio",
        ).start()
        return True

    hotkey = HotkeyListener(
        hotkey=cfg["hotkey"],
        mode=cfg["hotkey_mode"],
        on_record_start=on_record_start,
        on_record_stop=on_record_stop,
    )

    def on_remote_toggle() -> None:
        if recorder.is_recording():
            on_record_stop()
            return
        if busy.is_set():
            return
        on_record_start()

    remote_trigger = UdpToggleServer(on_remote_toggle)

    hotkey_label = format_hotkey_label(cfg["hotkey"])

    def tray_status_text(mode: str) -> str:
        mode_hint = "удерживайте" if mode == "hold" else "нажмите"
        return f"Диктовка: {mode_hint} {hotkey_label}"

    def on_hotkey_mode_change(mode: str) -> None:
        cfg["hotkey_mode"] = mode
        hotkey.set_mode(mode)
        if tray_app is not None:
            tray_app.set_tooltip(tray_status_text(mode))

    mode_hint = "удерживайте" if cfg["hotkey_mode"] == "hold" else "нажмите"
    ready_msg = f"Готов. {mode_hint.capitalize()} {hotkey_label} для диктовки."
    if use_tray:
        tray_msg = tray_status_text(cfg["hotkey_mode"])
        if tray_app is not None:
            tray_app.set_hotkey_mode_handler(on_hotkey_mode_change)
            tray_app.set_hotkey_mode(cfg["hotkey_mode"])
            tray_app.set_tooltip(tray_msg)
        print(ready_msg)
    else:
        print(f"{ready_msg} Ctrl+C — выход.")

    hotkey.start()
    remote_trigger.start()
    if hotkey.is_listening():
        _log_line(project_root, f"слушатель клавиш запущен: {cfg['hotkey']} ({cfg['hotkey_mode']})")
    else:
        # Слушатель клавиш может не подняться, если приложение с большими
        # правами перехватывает ввод. Без этой записи поломка выглядит как
        # «нажимаю — ничего», и связи с правами не видно.
        message = (
            "слушатель клавиш НЕ запустился: горячая клавиша не сработает. "
            "Причина обычно в другом приложении, запущенном от администратора, "
            "либо в перехвате ввода."
        )
        print(message)
        _log_line(project_root, message)
        if tray_app is not None:
            tray_app.notify_hotkey_failed()
    try:
        while not stop_event.wait(timeout=0.5):
            pass
    except KeyboardInterrupt:
        print("\nВыход...")
    finally:
        remote_trigger.stop()
        hotkey.stop()
        recorder.close()
        if tray_app is not None:
            tray_app.stop()
        if restart_requested:
            from dictation.restart import relaunch

            relaunch(project_root, config.CONFIG_PATH)
        sys.exit(0)


if __name__ == "__main__":
    main()
