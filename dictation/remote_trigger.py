from __future__ import annotations

import socket
import threading
from collections.abc import Callable

DEFAULT_PORT = 38721


class UdpToggleServer:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        port: int = DEFAULT_PORT,
    ) -> None:
        self._on_toggle = on_toggle
        self._port = port
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _serve(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self._port))
        sock.settimeout(0.5)
        self._socket = sock
        while not self._stop_event.is_set():
            try:
                data, _addr = sock.recvfrom(64)
            except TimeoutError:
                continue
            except OSError:
                break
            if data.strip().lower() == b"toggle":
                self._on_toggle()
