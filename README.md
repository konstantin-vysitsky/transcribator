# Трансктибатор — голосовая диктовка в трее

Локальная диктовка на Windows через **faster-whisper** (`large-v3-turbo`), с автовставкой в активное поле.

## Быстрый старт

1. Установите **Python 3.11**.
2. Запустите `start.bat` — создаст `.venv`, установит зависимости, загрузит модель (~20–30 сек на CPU).
3. Для фонового режима: `start_background.vbs` (иконка в трее, без консоли).

## Горячая клавиша

По умолчанию: **F10**, режим **toggle** (нажал — старт, ещё раз — стоп).

Настройки в `config.json` или через меню трея.

## Возможности

- Иконка в системном трее + панель статуса (запись / распознавание)
- История последних 3 транскрипций (клик → копирование)
- Автовставка + опциональный **Enter** после текста
- Кнопки на панели: «Автоотправка», «Готово», «✕» (отмена)
- Выбор режима клавиши (удержание / toggle)
- Настройка потоков CPU (1–10), перезагрузка из меню
- Автозапуск Windows через ярлык `Dictation.lnk`

## Структура

```
трансктибатор/
├── config.json
├── requirements.txt
├── transcribe.py
├── start.bat
├── start_background.vbs
└── dictation/
    ├── main.py
    ├── tray.py
    ├── status_panel.py
    ├── hotkey.py
    ├── recorder.py
    ├── transcriber.py
    ├── paster.py
    ├── timing.py
    ├── autostart.py
    └── ...
```

## Модель

`large-v3-turbo`, язык `ru`, int8 на CPU. Кэш HuggingFace общий с другими проектами faster-whisper.

## Мини-клавиатура (USB remap)

Папка `c:\по восстановлению\прочее\usb-keyboard-remap\` — ремап Keychron mini (VID 8808 / PID 6601):

- `run\remap-mini-keyboard.ahk` — клавиша 1 → F9, клавиша 2 → F10
- `run\trigger-dictation.ps1` — UDP-сигнал `toggle` на порт 38721 (запасной путь)
- `dictation/remote_trigger.py` — слушает UDP в транскрибаторе

Запуск ремапа:
```
run\ahk\AutoHotkey64.exe run\remap-mini-keyboard.ahk
```

**Внимание:** в восстановленной копии отсутствует `Lib\AutoHotInterception.ahk` (есть только DLL). Его нужно скачать с [AutoHotInterception](https://github.com/evilC/AutoHotInterception) или взять из бэкапа.

