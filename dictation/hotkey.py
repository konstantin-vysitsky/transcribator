from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pynput import keyboard
from pynput.keyboard import Key, KeyCode

HOTKEY_MAP: dict[str, Key | KeyCode] = {
    "f8": Key.f8,
    "f9": Key.f9,
    "f10": Key.f10,
    "f11": Key.f11,
    "f7": Key.f7,
    "ctrl_r": Key.ctrl_r,
}

# Windows virtual-key codes for function keys (pynput may deliver Key or KeyCode).
_VK_TO_KEY: dict[int, Key] = {
    0x70: Key.f7,
    0x77: Key.f8,
    0x78: Key.f9,
    0x79: Key.f10,
    0x7A: Key.f11,
    0xA3: Key.ctrl_r,
}


def _normalize_key(key: Key | KeyCode | None) -> Key | KeyCode | None:
    if key is None:
        return None
    if isinstance(key, KeyCode) and key.vk is not None:
        mapped = _VK_TO_KEY.get(key.vk)
        if mapped is not None:
            return mapped
    return key


def _key_id(key: Key | KeyCode | None) -> Any:
    key = _normalize_key(key)
    if key is None:
        return None
    if isinstance(key, KeyCode):
        if key.vk is not None:
            return ("vk", key.vk)
        if key.char is not None and len(key.char) == 1:
            return ("char", key.char)
        return None
    return ("key", key)


def _parse_hotkey(hotkey: str) -> list[Any]:
    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not parts:
        parts = ["f9"]
    ids: list[Any] = []
    for part in parts:
        mapped = HOTKEY_MAP.get(part)
        if mapped is None:
            raise ValueError(f"Unsupported hotkey part: {part}")
        key_id = _key_id(mapped)
        if key_id is None:
            raise ValueError(f"Unsupported hotkey part: {part}")
        ids.append(key_id)
    return ids


def format_hotkey_label(hotkey: str) -> str:
    parts = [part.strip().upper().replace("_", " ") for part in hotkey.split("+") if part.strip()]
    return " + ".join(parts) if parts else "F9"


class HotkeyListener:
    def __init__(
        self,
        hotkey: str = "f9",
        mode: str = "hold",
        on_record_start: Callable[[], bool] | None = None,
        on_record_stop: Callable[[], bool] | None = None,
    ) -> None:
        self._hotkey_ids = frozenset(_parse_hotkey(hotkey))
        self._mode = mode
        self._on_record_start = on_record_start
        self._on_record_stop = on_record_stop
        self._keys_down: set[Any] = set()
        self._recording = False
        self._combo_triggered = False
        self._listener: keyboard.Listener | None = None

    def _all_targets_down(self) -> bool:
        return self._hotkey_ids.issubset(self._keys_down)

    def _is_target_key(self, key: Key | KeyCode | None) -> bool:
        kid = _key_id(key)
        return kid is not None and kid in self._hotkey_ids

    def _on_press(self, key: Key | KeyCode | None) -> None:
        try:
            self._handle_press(key)
        except Exception:
            # Падение внутри обработчика уносит поток слушателя, и нажатия
            # перестают приходить вовсе: снаружи это «клавиша перестала
            # работать», без следа в журнале. Причина уйдёт в общий перехват
            # потоков, а набор зажатых клавиш чистим, чтобы следующее нажатие
            # не упёрлось в «клавиша уже зажата».
            self._keys_down.clear()
            self._combo_triggered = False
            raise

    def _handle_press(self, key: Key | KeyCode | None) -> None:
        kid = _key_id(key)
        if kid is None or kid in self._keys_down:
            return
        self._keys_down.add(kid)
        if not self._all_targets_down() or self._combo_triggered:
            return
        self._combo_triggered = True
        if self._mode == "hold":
            if not self._recording and self._on_record_start:
                self._recording = bool(self._on_record_start())
        elif self._mode == "toggle":
            if not self._recording:
                self._recording = bool(self._on_record_start and self._on_record_start())
            else:
                # Флаг снимается в любом случае, даже если остановка отказала.
                # Иначе он расходится с настоящим состоянием записи, и горячая
                # клавиша залипает: следующее нажатие снова пытается
                # остановить, вместо того чтобы начать.
                if self._on_record_stop:
                    self._on_record_stop()
                self._recording = False

    def _on_release(self, key: Key | KeyCode | None) -> None:
        try:
            self._handle_release(key)
        except Exception:
            self._keys_down.clear()
            self._combo_triggered = False
            raise

    def _handle_release(self, key: Key | KeyCode | None) -> None:
        kid = _key_id(key)
        if kid is not None:
            self._keys_down.discard(kid)
        if self._is_target_key(key):
            self._combo_triggered = False
        if self._mode != "hold" or not self._recording:
            return
        if not self._all_targets_down() and self._on_record_stop and self._on_record_stop():
            self._recording = False

    def is_active(self) -> bool:
        """Думает ли слушатель, что запись идёт.

        Без этого вопроса расхождение «микрофон пишет, а клавиша не знает»
        невозможно увидеть: нажатие молча упирается в проверку записи, и
        горячая клавиша перестаёт работать совсем.
        """
        return self._recording

    def reset_state(self) -> None:
        self._recording = False
        self._combo_triggered = False
        self._keys_down.clear()

    def set_mode(self, mode: str) -> None:
        if mode not in ("hold", "toggle"):
            return
        if self._mode == mode:
            return
        if self._recording:
            self._recording = False
            if self._on_record_stop:
                self._on_record_stop()
        self._combo_triggered = False
        self._keys_down.clear()
        self._mode = mode

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
        )
        self._listener.start()

    def is_listening(self) -> bool:
        """Идёт ли прослушивание на самом деле.

        `Listener.start()` запускает поток, но поток может умереть сразу —
        например, когда ввод перехватывает приложение с правами выше. Проверяем
        живой поток, а не наличие объекта: иначе поломка выглядит как рабочее
        приложение, которое не отвечает на клавишу.
        """
        listener = self._listener
        if listener is None:
            return False
        try:
            # pynput останавливает поток при ошибке и помечает это флагом.
            if getattr(listener, "running", True) is False:
                return False
        except Exception:  # noqa: BLE001
            pass
        thread = getattr(listener, "_thread", None)
        if thread is not None and not thread.is_alive():
            return False
        return True

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._keys_down.clear()
        self._recording = False
        self._combo_triggered = False
