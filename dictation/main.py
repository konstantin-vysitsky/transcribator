from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from dictation import ai, autostart, config
from dictation.hotkey import HotkeyListener, format_hotkey_label
from dictation.single_instance import wait_single_instance
from dictation.remote_trigger import UdpToggleServer
from dictation.paster import capture_foreground_window, paste_text
from dictation.prompt import load_prompt
from dictation.prompt_editor import set_logger as set_editor_logger
from dictation.recorder import SAMPLE_RATE, Recorder, peak_percent
from dictation.recording_store import RecordingStore
from dictation.sounds import quiet_beep_start, quiet_beep_stop
from dictation.status_panel import set_logger as set_panel_logger
from dictation.timing import (
    TranscribeTimer,
    estimate_transcribe_seconds,
    record_speed_sample,
)
from dictation.transcriber import Transcriber

LOG_NAME = "dictation.log"

# Подписи моделей распознавания такие же, как в меню трея: по ним в меню
# видно, какая модель работает сейчас.
_STREAM_TRAY_LABELS = {
    "t-one": "T-one, по ходу речи",
    "zipformer-small": "Zipformer-small, по ходу речи",
    "zipformer-small-int8": "Zipformer-small, по ходу речи",
}


def stream_tray_label(recognition: str) -> str:
    return _STREAM_TRAY_LABELS.get(recognition, "GigaAM (после остановки)")


def load_stream_model(recognition: str, cpu_threads: int):
    """Загрузить потоковую модель. None — работать на GigaAM.

    Модель не грузится (нет файлов, не хватает памяти) — приложение не
    встаёт: GigaAM на месте, и диктовка остаётся. Причина видна в журнале.
    """
    if recognition == "gigaam":
        return None
    try:
        from dictation.streaming import _recognizer

        return _recognizer(recognition, max(1, cpu_threads))
    except Exception as exc:  # noqa: BLE001 — потоковая модель это опыт
        print(f"Потоковая модель не загрузилась: {exc}")
        return None


def _log_line(project_root: Path, message: str) -> None:
    """Записать строку в журнал приложения.

    Приложение запускается без консоли, и без журнала у него нет ни одного
    способа сказать, что оно не поднялось. Нажатие горячей клавиши при этом
    выглядит как «ничего не произошло», и причину искать негде.
    """
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        # многострочные сообщения (traceback) пишутся с отступом под временем,
        # иначе строки теряют привязку к событию и журнал читается как разные
        lines = str(message).splitlines() or [""]
        with (project_root / LOG_NAME).open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {lines[0]}\n")
            for extra in lines[1:]:
                handle.write(f"{' ' * 21}{extra}\n")
    except OSError:
        pass


def _install_crash_logging(project_root: Path) -> None:
    """Записать в журнал причину падения, а не оставить после себя тишину.

    Приложение живёт в трее и консоли не имеет, а запускается оно через
    `spawn_tray.py`, где stderr никто не читает. Поэтому необработанное
    исключение раньше уносило процесс целиком, не оставив в журнале ни
    строки: последняя запись — удачная диктовка, дальше обрыв, и по журналу
    невозможно было понять ни что случилось, ни когда. Именно так диктовка
    «спонтанно отключилась» в ночь на 28 октября.

    Ловим оба пути: главный поток (sys.excepthook) и фоновые
    (threading.excepthook) — распознавание идёт в отдельном потоке, и его
    падение выглядело бы ровно так же.
    """

    def _format(exc_type, exc, tb, where: str) -> str:
        text = "".join(traceback.format_exception(exc_type, exc, tb)).rstrip()
        return f"СБОЙ {where}: {exc_type.__name__}: {exc}\n{text}"

    def _main_hook(exc_type, exc, tb) -> None:
        _log_line(project_root, _format(exc_type, exc, tb, "главного потока"))
        # Прежнее поведение всё же не молчит там, где stderr всё-таки есть
        # (запуск из консоли при разработке).
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args) -> None:
        if args.exc_type is SystemExit:
            return
        where = f"потока «{args.thread.name if args.thread else '?'}»"
        _log_line(project_root, _format(args.exc_type, args.exc_value, args.exc_traceback, where))

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


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

    # До всего остального: иначе первое же исключение ниже унесёт процесс и
    # не оставит в журнале ни строки (см. _install_crash_logging).
    _install_crash_logging(config.CONFIG_PATH.parent)

    if not wait_single_instance():
        # Второй экземпляр молча выходит — а снаружи это выглядит как
        # «приложение не отвечает». Пишем причину в журнал.
        _log_line(config.CONFIG_PATH.parent, "второй экземпляр уже запущен, выход")
        return

    cfg = config.load_config()
    # Номер потоковой диктовки: по нему поток дописывания в поле отличает
    # свою диктовку от уже отменённой.
    typing_nonce = 0
    project_root = config.CONFIG_PATH.parent
    use_tray = args.tray
    busy = threading.Event()
    _log_line(project_root, "запуск приложения")

    # Панель и окно живут в своих потоках. Если что-то не поднялось,
    # причина ложится в журнал строкой с приставкой «интерфейс:», а диктовка
    # работает дальше без этой части.
    def _interface(message: str) -> None:
        _log_line(project_root, f"интерфейс: {message}")

    set_panel_logger(_interface)
    set_editor_logger(_interface)

    # Промпт создаётся сразу при запуске, а не при первом обращении: иначе
    # файл появляется только после первой диктовки с обработкой, и найти его
    # заранее нечем.
    load_prompt(project_root)

    if cfg.get("autostart") and not autostart.is_autostart_enabled():
        autostart.enable_autostart(project_root)

    stop_event = threading.Event()
    transcribe_cancel = threading.Event()
    # Отдельная отмена для потоковой диктовки: на панели есть кнопка
    # «отмена», и она должна отменять текущую запись, а не весь текст.
    stream_cancel = threading.Event()
    # Текущая потоковая диктовка: куда звук идёт прямо сейчас. None —
    # обычная диктовка, распознаём после остановки.
    stream_live: list[object | None] = [None]
    # Вставка по ходу речи: очередь того, что печатать, и счёт последней
    # вставки. Очередь — на одну диктовку, `typing[1]` — её состояние.
    # Очередь того, что дописать в поле, состояние вставки и приписка на
    # конец диктовки: её считает _stop_stream_live после остановки.
    typing: list = [queue.Queue(), {}, None]
    restart_requested = False
    tray_app = None
    hotkey: HotkeyListener | None = None
    recorder = Recorder(
        min_duration_s=cfg["min_utterance_s"],
        device=cfg.get("microphone", ""),
    )
    store = RecordingStore(project_root)
    record_target_hwnd: int | None = None
    # Поток, который сейчас разбирает запись. Нужен, чтобы отличить честную
    # занятость от залипшего признака: если поток уже кончился, а флаг остался,
    # приложение навсегда перестаёт начинать запись.
    processing_thread: list[threading.Thread | None] = [None]

    def _set_processing_thread(thread: threading.Thread | None) -> None:
        processing_thread[0] = thread

    def _processing_thread_alive() -> bool:
        thread = processing_thread[0]
        return thread is not None and thread.is_alive()

    def request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        stop_event.set()

    def reset_tray_idle() -> None:
        # Во время распознавания и обработки панель продолжает показывать ход
        # работы. Её окно раньше звали на экран заново на каждом шаге, и оно
        # мелькало. Поэтому «простой» ставится, только когда работы нет.
        if tray_app is not None and not busy.is_set():
            tray_app.set_activity("idle")

    def finish_recording_from_ui() -> None:
        if hotkey is not None:
            hotkey.reset_state()
        on_record_stop()

    def abort_transcription() -> None:
        transcribe_cancel.set()
        stream_cancel.set()
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
            on_microphone_change=recorder.set_device,
        )
        tray_app.start()

    print("Загрузка модели распознавания речи...")
    print(f"Потоки CPU в настройках: {cfg['cpu_threads']} (применяются при запуске)")
    _log_line(project_root, "загрузка модели распознавания речи")
    # Какая модель работает: GigaAM (после остановки) или потоковая
    # (по ходу речи). Задаётся в меню трея и переживает перезапуск;
    # потоковая не заменяет GigaAM, а стоит рядом с ней.
    recognition = cfg.get("recognition", "gigaam")
    # Модель держим отдельно от счёта: счёт на каждую диктовку свой, а
    # модель, загруженная один раз, живёт до перезапуска.
    stream_model = load_stream_model(recognition, cfg["cpu_threads"])
    if stream_model is None:
        # Нет модели или она не поднялась: диктовка остаётся на GigaAM.
        # Опечатка в настройках уже заменена GigaAM при чтении, так что
        # сюда приходит только настоящая поломка.
        if recognition != "gigaam":
            _log_line(
                project_root,
                f"потоковая модель «{recognition}» не загрузилась — работаю на GigaAM",
            )
        recognition = "gigaam"
        cfg["recognition"] = "gigaam"
        transcriber = Transcriber(
            model=cfg["model"],
            cpu_threads=cfg["cpu_threads"],
            remove_fillers_enabled=cfg.get("remove_fillers", True),
        )
    else:
        transcriber = None
    runtime_label = (
        f"stream/{recognition}, cpu" if stream_model is not None else transcriber.runtime_label()
    )
    print(f"Модель загружена ({runtime_label})")
    _log_line(project_root, f"модель загружена: {runtime_label}")
    if tray_app is not None:
        tray_app.set_runtime_info(runtime_label)
        tray_app.set_stream_label(stream_tray_label(cfg.get("recognition", "gigaam")))
    if use_tray:
        # Громкость видна на панели только во время записи.
        recorder.on_level = tray_app.set_microphone_level if tray_app is not None else None

    def _stream_on_text(raw: str) -> None:
        """Модель поняла новые слова — дописать готовое в поле.

        В поле ничего не стирается и не пишется заново: дописываются только
        слова, которые модель закончила. Незаконченное слово придерживается
        до остановки. Вставку делает отдельный поток: звук её не ждёт.
        """
        live = stream_live[0]
        if live is None:
            return
        piece = live.emit_pending()
        if piece:
            typing[0].put(piece)

    def _typing_worker(nonce: int) -> None:
        """Дописывать в поле по ходу речи. Живёт до конца диктовки."""
        queue = typing[0]
        while True:
            item = queue.get()
            if item is None:
                return
            face = typing[1]
            if face.get("nonce") != nonce:
                # Диктовку отменили: остаток очереди выбрасываем.
                continue
            piece = item
            try:
                from dictation.paster import foreground_is

                target = record_target_hwnd
                if face.get("ever") and not foreground_is(target):
                    # Человек ушёл в другое окно: по ходу речи туда не пишем,
                    # окончательный текст допишется в конце.
                    continue
                pasted, _ = paste_text(
                    piece,
                    append_space=cfg["paste_append_space"],
                    auto_enter=False,
                    target_hwnd=target,
                    only_if_foreground=face.get("ever", False),
                )
                face["ever"] = face.get("ever") or pasted
                face["pasted"] = pasted
                face["len"] = len(piece) + (1 if cfg["paste_append_space"] and piece else 0)
            except Exception as exc:  # noqa: BLE001 — звук и панель важнее
                _log_line(
                    project_root, f"сбой дописывания по ходу речи: {type(exc).__name__}: {exc}"
                )

    def _start_stream_live() -> None:
        """Начать потоковую диктовку: новый счёт для новой фразы."""
        from dictation.streaming import StreamingTranscriber

        # Модель загружена одна; счёт создаётся заново, и прошлый текст в
        # новую диктовку не попадает.
        stream_cancel.clear()
        live = StreamingTranscriber(
            model=recognition,
            cpu_threads=cfg["cpu_threads"],
            on_text=_stream_on_text,
            model_ref=stream_model,
        )
        # Дописывание в поле: модель зовёт приписку слова из своего потока,
        # `on_append=None` — значит поле ведёт `_stream_on_text` через очередь.
        live.set_typing(True, None)
        # Новая вставка на новую диктовку: прошлые счётчики не годятся.
        while not typing[0].empty():
            typing[0].get_nowait()
        nonlocal typing_nonce
        typing_nonce += 1
        nonce = typing_nonce
        typing[1] = {
            "nonce": nonce,
            "printed": "",
            "pasted": False,
            "len": 0,
            "ever": False,
            "final": False,
        }
        stream_live[0] = live
        live.start()
        # Поток вставки — свой: модель зовёт его из своего потока и ждать его
        # не должна.
        threading.Thread(
            target=_typing_worker, args=(nonce,), daemon=True, name="stream-type"
        ).start()
        recorder.on_audio_chunk = live.feed
        if tray_app is not None:
            tray_app.set_live_text("", active=True)


    def _stop_stream_live(tail: np.ndarray | None = None) -> str | None:
        """Закончить потоковую диктовку и вернуть сказанное одной строкой.

        `tail` — звук, записанный, но пришедший из кольца уже после
        остановки: в записи он есть, а в модель ещё не попал.
        """
        live = stream_live[0]
        stream_live[0] = None
        recorder.on_audio_chunk = None
        if live is None:
            return None
        if stream_cancel.is_set():
            live.cancel()
            return None
        if tail is not None and len(tail):
            live.feed(tail)
        text = live.finish()
        # В поле уже стоит всё, кроме последнего слова и точки: их считаем
        # здесь, пока модель дочитала хвост записи.
        typing[2] = live.final_append()
        return text

    def _deliver_text(text: str, raw_text: str, use_ai: bool) -> bool:
        """Вставить готовый текст в окно и записать это в историю панели.

        Одно место для обеих моделей: вставка, Enter, история и строки
        журнала одинаковы, чем бы текст ни распознали.
        """
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
        if pasted:
            suffix = " + Enter" if enter_sent else ""
            print(f"Вставлено{suffix}: {text}")
        else:
            print("Не удалось вставить текст")
            _log_line(project_root, "не удалось вставить текст")
            if text != raw_text:
                print(f"Было распознано: {raw_text}")
        _log_line(
            project_root,
            f"{'вставлено' if pasted else 'НЕ вставлено'} ({len(text)} знаков)",
        )
        return pasted

    def _process_stream(
        text: str,
        audio: np.ndarray,
        saved_path: Path | None,
        started_at: float,
    ) -> None:
        """Потоковая диктовка закончена: дочистить текст и вставить.

        Сборка, чистка, обработка ИИ и вставка — те же, что у GigaAM.
        Отличие одно: текст мог уже появиться на панели по ходу, и здесь
        он заменяется окончательным.
        """
        from dictation.streaming import merged_text

        raw_text = merged_text(text)
        if not raw_text.strip():
            _log_line(
                project_root,
                (
                    "потоковое распознавание дало пустой текст, "
                    f"громкость {peak_percent(audio)}% шкалы, "
                    f"микрофон «{recorder.input_device_name() or 'как в Windows'}»"
                ),
            )
            if tray_app is not None:
                tray_app.set_live_text("", active=False)
            return
        text = raw_text
        if cfg.get("remove_fillers", True):
            from dictation.cleanup import load_fillers, remove_fillers

            text = remove_fillers(text, load_fillers())
        if tray_app is not None:
            tray_app.set_live_text(text, active=False)
            tray_app.mark_transcribed()
        use_ai = cfg.get("ai_enabled", False)
        if tray_app is not None:
            use_ai = tray_app.ai_enabled()
        # Итог обработки ИИ. Когда она выключена, итога нет — и ниже это
        # проверяется явно: без такой строки обращение к `ai_error` падало
        # с UnboundLocalError уже после вставки текста.
        ai_error: str | None = None
        if use_ai and not stream_cancel.is_set():
            if tray_app is not None:
                tray_app.set_activity("processing", "жду ответа модели…")
            text, ai_error = ai.process_text(
                text,
                prompt=load_prompt(project_root),
                api_key=str(cfg.get("ai_api_key", "")),
                base_url=str(cfg.get("ai_base_url", ai.DEFAULT_BASE_URL)),
                model=str(cfg.get("ai_model", ai.DEFAULT_MODEL)),
                timeout_s=float(cfg.get("ai_timeout_s", ai.DEFAULT_TIMEOUT_S)),
            )
            if tray_app is not None:
                tray_app.finish_ai_stage(recovered=ai_error is None)
            if ai_error is not None:
                _log_line(project_root, f"обработка через ИИ не удалась: {ai_error}")
            if stream_cancel.is_set():
                return
        if stream_cancel.is_set():
            return
        # Файл с текстом остаётся только в папке записей; саму запись
        # убирает `store.trim`, она же чистит старые.
        store.save_text(saved_path, text)
        store.keep(saved_path)
        face = typing[1]
        auto_enter = cfg.get("paste_auto_enter", False)
        if tray_app is not None and tray_app.should_skip_auto_enter():
            auto_enter = False
        tail = typing[2]
        if use_ai and tail is not None:
            # Обработка через ИИ переписывает слово в уже напечатанном:
            # дописать нечем, ставим её ответ целиком вместо черновика.
            # Это единственный случай, когда вставленное заменяется.
            if face.get("pasted"):
                try:
                    from dictation.paster import erase_pasted

                    erase_pasted(int(face.get("len", 0) or 0), record_target_hwnd)
                except Exception:  # noqa: BLE001 — окончательная вставка важнее
                    pass
            typed_ok = _deliver_text(text, raw_text, use_ai)
        elif use_ai:
            # ИИ упал: в поле уже стоит сказанное, вставлять второй раз нечего.
            typed_ok = True
        elif tail is not None:
            # Ничего не стираем: дописываем только то, чего в поле ещё нет —
            # незаконченное слово и точку. Ошибки модели в уже напечатанных
            # словах остаются как есть: черновик, который стирался и
            # переписывался, человеку мешал больше, чем помогал.
            pasted, enter_sent = paste_text(
                tail,
                append_space=cfg["paste_append_space"],
                auto_enter=auto_enter,
                target_hwnd=record_target_hwnd,
                only_if_foreground=bool(face.get("ever")),
            )
            typed_ok = pasted
            if pasted:
                _log_line(
                    project_root,
                    f"в поле дописано {len(tail)} знаков" + (" + Enter" if enter_sent else ""),
                )
            else:
                # Пока говорили, человек ушёл в другое окно — туда по ходу
                # речи не писали. Вставляем в то окно, что на экране сейчас.
                pasted, enter_sent = paste_text(
                    text,
                    append_space=cfg["paste_append_space"],
                    auto_enter=auto_enter,
                    target_hwnd=record_target_hwnd,
                )
                typed_ok = pasted
                if tray_app is not None:
                    tray_app.add_transcription(text)
                if pasted:
                    _log_line(project_root, f"окно сменилось: вставлено целиком ({len(text)} знаков)")
        else:
            # По ходу речи ничего не вставлялось: обычная вставка целиком.
            typed_ok = _deliver_text(text, raw_text, use_ai)
        pasted = typed_ok
        if tray_app is not None:
            tray_app.set_live_text(text, active=False)
            tray_app.mark_transcribed()
            if ai_error is not None:
                tray_app.report_ai_error(ai_error)
            elif use_ai:
                tray_app.clear_ai_error()
        if not pasted:
            _log_line(
                project_root,
                f"потоковая диктовка не вставлена, текст: {text[:200]}",
            )
        # Сколько шло после отпускания клавиши: потоковая модель дочищает
        # хвост и вставляет текст, и по этой цифре видно, стоит ли ждать.
        _log_line(
            project_root,
            f"потоковая диктовка: {len(audio) / SAMPLE_RATE:.1f} с речи, "
            f"{time.monotonic() - started_at:.1f} с на всё (включая вставку)"
            + (
                f", модель поправила уже написанное {live.revisions} раз"
                if live is not None and live.revisions
                else ""
            ),
        )

    def on_record_start() -> bool:
        nonlocal record_target_hwnd, cfg
        if recorder.is_recording():
            # Уже пишем: второе нажатие не должно начинать заново и стирать
            # записанное. Пусть решает обработчик остановки.
            # В журнал — только когда горячая клавиша думает, что не пишет:
            # это значит, что состояния разошлись, и молчание тут скрывает
            # поломку целиком.
            if hotkey is not None and not hotkey.is_active():
                _log_line(
                    project_root,
                    "нажатие при «записи» в микрофоне, о которой клавиша не знает: сбрасываю",
                )
                recorder.stop()
                busy.clear()
                return False
            return False
        if busy.is_set():
            if not _processing_thread_alive():
                # Признак занятости сам признаёт, что работы нет: поток
                # обработки кончился, а флаг остался. Такое уже случалось —
                # приложение «слушало» и молча ничего не делало, и лечилось
                # только перезапуском. Снимаем флаг и работаем дальше.
                message = "признак занятости залип, поток обработки уже завершился: снимаю"
                print(message)
                _log_line(project_root, message)
                busy.clear()
                if hotkey is not None:
                    hotkey.reset_state()
                reset_tray_idle()
            else:
                _log_line(project_root, "предыдущая диктовка ещё обрабатывается, запись не начата")
                return False
        # Настройки перечитываются перед каждой записью: ключ к сервису
        # обработки правят руками в файле, а приложение живёт без окна.
        # С загрузки прошли часы, и старое значение врало бы дальше.
        cfg = config.load_config()
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
                tray_app.refresh_ai_settings()
                # Панель и звук — сперва, микрофон — сразу за ними: панель
                # должна показаться в момент нажатия, а не после открытия
                # микрофона, которое занимает время на старте.
                tray_app.set_activity("recording")
                # Громкость на момент нажатия: обработчик звука мерил её и до
                # этого, поэтому столбик сразу стоит по входу, а не на нуле.
                tray_app.set_microphone_level(recorder.current_level())
                # Сброс и первый рисунок — на экран здесь же: иначе панель
                # успевает показаться с картинкой прошлой диктовки — с
                # полосой под конец — и только потом её перерисовывает.
                tray_app.flush()
            # Потоковая диктовка: текст на панели появляется по ходу речи.
            # Поднимается до открытия микрофона, чтобы первый же кусок звука
            # попал в модель, а не в пустоту.
            if stream_model is not None:
                _start_stream_live()
            if not recorder.start():
                # Запись уже идёт: второе нажатие не должно её стирать.
                # Показываем это человеку, чтобы не выглядело как зависание.
                message = "запись уже идёт, повторный запуск пропущен"
                print(message)
                _log_line(project_root, message)
                busy.clear()
                if tray_app is not None:
                    tray_app.set_activity("recording")
                return False
            # Какое устройство открыто: при пропаже микрофона Windows берёт
            # другой, и он может молчать. Строка при записи экономит разбор:
            # по ней видно, слушали ли fifine или веб-камеру.
            device_name = recorder.input_device_name()
            _log_line(
                project_root,
                f"запись начата: {device_name}" if device_name else "запись начата",
            )
            return True
        except BaseException as exc:
            # Любой выход, даже не «ошибка», обязан оставить приложение
            # готовым: иначе признак занятости переживёт сбой, и запись больше
            # не начнётся без перезапуска.
            busy.clear()
            reset_tray_idle()
            print(f"Ошибка записи: {exc}")
            _log_line(project_root, f"ошибка записи: {type(exc).__name__}: {exc}")
            return False

    def process_audio(
        audio: np.ndarray,
        saved_path: Path | None = None,
        noise: np.ndarray | None = None,
    ) -> None:
        timer: TranscribeTimer | None = None
        progress_thread: threading.Thread | None = None
        stop_progress = threading.Event()
        # Был ли текст вставлен. По этому признаку в конце решается, о чём
        # честно писать в журнал, если запись осталась на диске.
        text_delivered = False

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

            def on_chunk(done: int, total: int) -> None:
                # На длинной записи это единственный признак движения: без
                # него видно только «Распознаю…» и решается, что всё встало.
                if tray_app is not None:
                    tray_app.set_activity(
                        "transcribing",
                        f"часть {done} из {total}",
                        done / total,
                    )

            _log_line(project_root, f"распознаю {audio_duration:.1f} с")
            silent_input = recorder.is_silent()
            if silent_input:
                # Ни одного ненулевого отсчёта: дело не в модели и не в
                # громкости речи, а во входе. Называем это до распознавания,
                # чтобы причину не искать в модели и в тексте.
                _log_line(
                    project_root,
                    (
                        f"вход отдал полную тишину за {audio_duration:.1f} с, "
                        f"микрофон «{recorder.input_device_name() or 'как в Windows'}»: "
                        "проверить, воткнут ли он и не выбран ли молчащий вход"
                    ),
                )
            text = transcriber.transcribe(audio, on_progress=on_chunk)
            if transcribe_cancel.is_set():
                return

            if not text or not text.strip():
                print("Слишком короткая фраза, пропуск")
                # Громкость и микрофон рядом: по ним видно, молчал человек
                # или запись пришла пустой с другого входа.
                _log_line(
                    project_root,
                    (
                        f"распознан пустой текст, запись оставлена: "
                        f"{saved_path.name if saved_path else 'файл не сохранён'}, "
                        f"громкость {peak_percent(audio)}% шкалы, "
                        f"микрофон «{recorder.input_device_name() or 'как в Windows'}»"
                    ),
                )
                return
            # Записи остаются на диске: папка «записи» нужна ещё и как собрание
            # своей речи. Текст кладём рядом, чтобы было понятно, что за звук.
            store.save_text(saved_path, text)
            store.keep(saved_path)
            saved_path = None

            # Распознавание закончено — гасим счётчик сразу. Его поток иначе
            # продолжает каждые полсекунды ставить статус «Распознаю…» и
            # затирает «Обрабатываю через ИИ…», пока ждём ответ модели.
            stop_progress.set()
            if progress_thread is not None:
                progress_thread.join(timeout=1.0)
                progress_thread = None
            use_ai = cfg.get("ai_enabled", False)
            if tray_app is not None:
                use_ai = tray_app.ai_enabled()
            if tray_app is not None and not (use_ai and not transcribe_cancel.is_set()):
                # Распознавание — единственная ступень: доводим её до конца.
                tray_app.mark_transcribed()
            raw_text = text
            ai_error: str | None = None
            # Кнопка панели и настройка говорят одно и то же: нажатие пишется
            # в config.json, поэтому `cfg` видит его уже в этом процессе.
            if use_ai and not transcribe_cancel.is_set():
                if tray_app is not None:
                    # Метка на панели говорит, что распознавание кончилось и
                    # началась вторая ступень: без неё две полосы не отличить.
                    tray_app.mark_transcribed()
                    tray_app.set_activity("processing", "жду ответа модели…")
                text, ai_error = ai.process_text(
                    text,
                    prompt=load_prompt(project_root),
                    api_key=str(cfg.get("ai_api_key", "")),
                    base_url=str(cfg.get("ai_base_url", ai.DEFAULT_BASE_URL)),
                    model=str(cfg.get("ai_model", ai.DEFAULT_MODEL)),
                    timeout_s=float(cfg.get("ai_timeout_s", ai.DEFAULT_TIMEOUT_S)),
                )
                if tray_app is not None:
                    tray_app.finish_ai_stage(recovered=ai_error is None)
                if transcribe_cancel.is_set():
                    return
                if ai_error is not None:
                    # Обработка не удалась — вставляем то, что распознали.
                    # Потерять диктовку из-за чужого сбоя нельзя.
                    print(f"Обработка через ИИ не удалась ({ai_error}), вставляю как есть")
                    # В журнал, а не только в консоль: приложение работает без
                    # окна, и без записи причину сбоя потом не восстановить.
                    _log_line(project_root, f"обработка через ИИ не удалась: {ai_error}")
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
                # Ошибка обработки остаётся на кнопке до следующей удачной:
                # уведомление гаснет само, а кнопка — нет.
                if ai_error is not None:
                    tray_app.report_ai_error(ai_error)
                elif use_ai:
                    tray_app.clear_ai_error()

            if pasted:
                suffix = " + Enter" if enter_sent else ""
                print(f"Вставлено{suffix}: {text}")
            else:
                print("Не удалось вставить текст")
                _log_line(project_root, "не удалось вставить текст")
                if ai_error is None and text != raw_text:
                    print(f"Было распознано: {raw_text}")

            # В журнал — «вставлено» или «не удалось»: по одной строке видно,
            # дошёл ли распознанный текст до окна, и не приходится гадать,
            # потерялся он на вставке или его вовсе не было.
            _log_line(
                project_root,
                f"{'вставлено' if pasted else 'НЕ вставлено'} ({len(text)} знаков)",
            )
            if pasted:
                text_delivered = True

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
            # Файл дожил до этого места — значит, распознавание не довели до
            # текста. Говорим об этом прямо: иначе запись лежит в папке, а
            # человек считает её потерянной.
            #
            # Но бывает и наоборот: текст уже вставлен, а файл просто не
            # успели убрать — убрать не вышло. Тогда писать «не завершилось»
            # значит соврать о диктовке, которая прошла.
            if saved_path is not None and not text_delivered:
                message = f"распознавание не завершилось, запись сохранена: {saved_path.name}"
                print(message)
                _log_line(project_root, message)
            busy.clear()
            reset_tray_idle()

    def _process_audio_guarded(
        audio: np.ndarray,
        saved_path: Path | None = None,
        noise: np.ndarray | None = None,
    ) -> None:
        """Распознать запись, что бы ни случилось.

        `busy` снимается в `finally` у `process_audio`, но туда ещё надо
        дойти: исключение до входа в `try` (например, при подсчёте длины или
        оценке времени) оставляло флаг взведённым навсегда. Тогда приложение
        больше не начинало ни одной записи — оно «слушало» и молчало, а
        вылечить это можно было только перезапуском. Поэтому здесь последний
        рубеж: любой сбой уходит в журнал, а флаг снимается всегда.
        """
        # Парные отметки «начал» и «закончил» с длительностью. Без второй
        # строки повисшая обработка неотличима от идущей: в журнале видно,
        # что работа началась, и больше ничего. По паре строк причина
        # видна глазами — где оборвалось и сколько шло.
        started = time.monotonic()
        _log_line(project_root, "обработка начата")
        failure: str | None = None
        try:
            process_audio(audio, saved_path, noise)
        except BaseException as exc:  # noqa: BLE001 — приложение не должно замереть
            # Именно BaseException, а не Exception: выход из системы и прочие
            # «не ошибки» тоже проходят здесь, и оставлять после них признак
            # занятости взведённым нельзя. Раньше флаг переживал такой сбой, и
            # каждое следующее нажатие молча упиралось в «занято».
            failure = f"{type(exc).__name__}: {exc}"
            message = f"сбой обработки диктовки: {failure}"
            print(message)
            _log_line(project_root, message)
        finally:
            busy.clear()
            _set_processing_thread(None)
            if hotkey is not None:
                hotkey.reset_state()
            reset_tray_idle()
            elapsed = time.monotonic() - started
            # Сбой уже записан строкой выше — повторять «закончил» за ним
            # незачем: выйдет две записи об одном событии.
            if failure is None:
                _log_line(project_root, f"обработка закончена за {elapsed:.1f} с")
            else:
                _log_line(project_root, f"обработка прервана через {elapsed:.1f} с")

    def _process_stream_guarded(
        text: str,
        audio: np.ndarray,
        saved_path: Path | None,
        started_at: float,
    ) -> None:
        """То же, что `_process_audio_guarded`, для потоковой диктовки.

        Признак занятости снимается всегда, а сбой ложится в журнал: без
        этого неудачная вставка оставляла бы диктовку «занятой» навсегда.
        """
        failure: str | None = None
        try:
            _process_stream(text, audio, saved_path, started_at)
        except BaseException as exc:  # noqa: BLE001 — приложение не должно замереть
            failure = f"{type(exc).__name__}: {exc}"
            print(f"сбой потоковой диктовки: {failure}")
            _log_line(project_root, f"сбой потоковой диктовки: {failure}")
        finally:
            busy.clear()
            _set_processing_thread(None)
            if hotkey is not None:
                hotkey.reset_state()
            reset_tray_idle()
            if failure is None:
                _log_line(project_root, "обработка закончена")
            else:
                _log_line(project_root, "обработка прервана")

    def on_record_stop() -> bool:
        if not recorder.is_recording():
            # Молчать здесь нельзя: снаружи это «нажимаю — ничего». Так уже
            # было 29 октября, когда микрофон отключился от USB: запись
            # считалась начатой, а остановка тихо выходила, и в журнале не
            # оставалось ни строки о поломке.
            _log_line(project_root, "остановка: запись уже не идёт, останавливать нечего")
            return False
        stop_started = time.monotonic()
        if cfg.get("sound_feedback"):
            try:
                quiet_beep_stop()
            except Exception:
                pass
        # Поток закрывается сразу после остановки записи: всё сказанное
        # уже попало в модель, и ей остаётся дочитать хвост. Звук записанный,
        # но ещё не пришедший из кольца, после этого в модель не попадёт —
        # поток закрыт. Поэтому хвост из кольца отдаётся модели отдельно.
        live = stream_live[0]
        try:
            result = recorder.stop_with_noise()
        except Exception as exc:
            # Микрофон отвалился посреди записи — звук уже не вернуть, но
            # приложение обязано остаться живым. Без этого `busy` оставался
            # взведённым, и дальше ни одно нажатие не начинало запись:
            # снаружи это «включается, слушает и перестаёт работать».
            print(f"Ошибка остановки записи: {exc}")
            _log_line(project_root, f"ошибка остановки записи: {exc}")
            _stop_stream_live()
            busy.clear()
            reset_tray_idle()
            return False
        audio, noise = result if result is not None else (None, None)
        if audio is None:
            # Пусто: сказанного не хватило на минуту разборчивой записи.
            # Самая частая причина — микрофон отключился или выбран тот, что
            # молчит. Без строки в журнале это неотличимо от нормальной паузы.
            _log_line(
                project_root,
                "запись пуста: микрофон ничего не собрал "
                "(отключён или выбран молчащий)",
            )
            _stop_stream_live(audio)
            if tray_app is not None:
                tray_app.set_live_text("", active=False)
            busy.clear()
            reset_tray_idle()
            return True
        # Сначала на диск, потом распознавание. До этого шага запись жила
        # только в памяти, и любой сбой стирал её без следа.
        saved = store.save(audio, time.strftime("%Y-%m-%d_%H-%M-%S"))
        if saved is not None:
            # Длительность рядом с именем: по одной строке видно, сколько
            # человек говорил, и не надо открывать файл, чтобы это понять.
            _log_line(
                project_root,
                f"запись сохранена: {saved.name} ({len(audio) / SAMPLE_RATE:.1f} с)",
            )
            store.trim()
        # Проверки на тишину нет нарочно. Она решала за человека, есть ли в
        # записи речь, и ошиблась: тихую диктовку («раз, два, три» на 8 %
        # шкалы) объявляла тишиной, хотя модель узнавала её. Теперь модель
        # слышит каждую запись, а когда текста нет — в журнале остаются
        # громкость записи и имя микрофона, чтобы причина была видна.
        stream_text = _stop_stream_live(audio if live is not None else None)
        if stream_text is not None:
            # Потоковая диктовка: текст уже распознан по ходу, остаётся
            # дочистить и вставить. В фоне, чтобы клавиша отвечала сразу.
            worker = threading.Thread(
                target=_process_stream_guarded,
                args=(stream_text, audio, saved, stop_started),
                daemon=True,
                name="process-stream",
            )
        else:
            worker = threading.Thread(
                target=_process_audio_guarded,
                args=(audio, saved, noise),
                daemon=True,
                name="process-audio",
            )
        _set_processing_thread(worker)
        worker.start()
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

            # Работу новый экземпляр перечитать не может: он наследует её
            # оттого, кто его запустил. Поэтому после перезапуска из меню
            # приложение дозапускается само, а не ждёт следующего входа.
            relaunch(project_root, config.CONFIG_PATH, parent_pid=os.getpid())
        sys.exit(0)


if __name__ == "__main__":
    main()
