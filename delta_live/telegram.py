from __future__ import annotations

import time
import threading

import requests


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, min_interval: float = 1.0):
        self.token, self.chat_id = token, chat_id
        self.min_interval = min_interval
        self._last_sent = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last_sent)
            if delay > 0:
                time.sleep(delay)
            try:
                response = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                         json={"chat_id": self.chat_id, "text": text[:4000]}, timeout=10)
                response.raise_for_status()
            except requests.RequestException:
                # Notification failure must never interrupt risk monitoring.
                return False
            self._last_sent = time.monotonic()
        return True
