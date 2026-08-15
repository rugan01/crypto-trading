from __future__ import annotations

import time
import threading
from enum import Enum

import requests


class Delivery(str, Enum):
    """Why a send did or did not happen.

    NOT_CONFIGURED and FAILED were both reported as a bare False until
    2026-08-15, when a DNS outage produced the log line "Telegram is not
    configured" on a box whose credentials were present and correct. The two
    have completely different fixes and must never be conflated again.
    """
    SENT = "sent"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, min_interval: float = 1.0):
        self.token, self.chat_id = token, chat_id
        self.min_interval = min_interval
        self._last_sent = 0.0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def deliver(self, text: str) -> Delivery:
        """Send, distinguishing "no credentials" from "could not reach Telegram"."""
        if not self.enabled:
            return Delivery.NOT_CONFIGURED
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
                return Delivery.FAILED
            self._last_sent = time.monotonic()
        return Delivery.SENT

    def send(self, text: str) -> bool:
        """Back-compat wrapper. Prefer deliver() where the reason matters."""
        return self.deliver(text) is Delivery.SENT
