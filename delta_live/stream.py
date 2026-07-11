from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable

import websocket


class PublicQuoteStream:
    """Reconnectable Delta public ticker stream with freshness tracking."""
    def __init__(self, url: str, symbols: list[str], on_message: Callable[[dict], None]):
        self.url, self.symbols, self.callback = url, symbols, on_message
        self._stop = threading.Event()
        self._last_message = 0.0
        self._thread: threading.Thread | None = None
        self._app: websocket.WebSocketApp | None = None

    @property
    def last_message_age(self) -> float:
        return time.monotonic() - self._last_message if self._last_message else float("inf")

    def _open(self, ws: websocket.WebSocketApp) -> None:
        ws.send(json.dumps({"type": "subscribe", "payload": {"channels": [
            {"name": "ticker", "symbols": self.symbols},
            {"name": "system_status", "symbols": ["all"]},
        ]}}))

    def _message(self, _ws: websocket.WebSocketApp, raw: str) -> None:
        message = json.loads(raw)
        self._last_message = time.monotonic()
        self.callback(message)

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            self._app = websocket.WebSocketApp(self.url, on_open=self._open, on_message=self._message)
            self._app.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop.is_set():
                break
            time.sleep(min(60, 2**attempt) + random.random())
            attempt = min(attempt + 1, 6)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="delta-public-ws", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._app:
            self._app.close()
        if self._thread:
            self._thread.join(timeout=5)
