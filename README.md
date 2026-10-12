# Трансктибатор — голосовая диктовка в трее

Локальная диктовка на Windows через **GigaAM v3 e2e RNN-T** (Сбер) с автовставкой в активное поле и очисткой слов-паразитов.

## Быстрый старт

1. Установите **Python 3.11**.
2. Запустите `start.bat` — создаст `.venv`, установит зависимости, загрузит модель (~1–2 мин на CPU при первом запуске).
3. Для фонового режима: `start_background.vbs` (иконка в трее, без консоли).

## Горячая клавиша

По умолчанию: **F7**, режим **hold** (зажал — говоришь, отпустил — распознавание).

Настройки в `config.json` или через меню трея.

## Возможности

- Иконка в системном трее + панель статуса (запись / распознавание)
- История последних 3 транскрипций (клик → копирование)
- Автовставка + опциональный **Enter** после текста
- Кнопки на панели: «Автоотправка», «Готово», «✕» (отмена)
- Выбор режима клавиши (удержание / toggle)
- Настройка потоков CPU (1–10), перезагрузка из меню
- Автозапуск Windows через ярлык `Dictation.lnk`
- Удаление слов-паразитов («э-э», «как бы», «типа» и т.п.) после распознавания

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
    ├── cleanup.py
    ├── paster.py
    ├── timing.py
    ├── autostart.py
    └── ...
```

## Модель

По умолчанию: **`v3_e2e_rnnt`** — GigaAM v3 end-to-end RNN-T от Сбера (PyTorch, CPU).

Модель сама расставляет пунктуацию и пишет числа цифрами. Кэш весов: `~/.cache/gigaam` (или `C:/gigaam-cache` при кириллице в профиле Windows).

Другие модели GigaAM задаются полем `model` в `config.json` (например `v3_e2e_ctc`, `v2_rnnt`, `multilingual_ctc`). Whisper не поддерживается.

## Очистка слов-паразитов

Включена по умолчанию (`"remove_fillers": true` в `config.json`).

После распознавания из текста убираются «э-э», «мм», «как бы», «типа» и другие слова из списка. Список редактируется в:

```
C:\Users\ВАШЕ_ИМЯ\.transcribator\fillers.json
```

Чтобы отключить очистку, поставьте `"remove_fillers": false`.

## Мини-клавиатура (USB remap)

Папка `c:\по восстановлению\прочее\usb-keyboard-remap\` — ремап Keychron mini (VID 8808 / PID 6601):

- `run\remap-mini-keyboard.ahk` — клавиша 1 → F9, клавиша 2 → F7
- `run\trigger-dictation.ps1` — UDP-сигнал `toggle` на порт 38721 (запасной путь)
- `dictation/remote_trigger.py` — слушает UDP в транскрибаторе

Запуск ремапа:
```
run\ahk\AutoHotkey64.exe run\remap-mini-keyboard.ahk
```
или просто `ЗАПУСК.bat`.

**Комплект восстановлен:** `run\ahk\AutoHotkey64.exe` (AutoHotkey v2.0.28),
`run\Lib\AutoHotInterception.ahk` + `.dll` и `run\Lib\x64\interception.dll`.
Драйвер Interception установлен, но активируется **после перезагрузки Windows**.
