"""Alerting that survives the failure it is trying to report.

On 15 August 2026 the 16:55 scheduler lost DNS. Every Delta call failed, the
session never started — and the Telegram alert failed too, because it needs the
same network that had just gone down. The log then said "Telegram is not
configured", which is what `send()` also prints when the credentials are
missing, so the message pointed at the wrong cause entirely. Bala found out by
noticing the absence of a session, not from an alert.

Two rules follow:

1. **Never report a delivery failure as a configuration failure.** They have
   different fixes.
2. **At least one channel must not depend on the network**, or an outage is
   silent by construction.

`alert()` fans out to every channel and reports what each one did. The local
channels — a macOS notification and an append-only log — cannot be taken out by
a DNS failure, so something always lands.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .telegram import Delivery, TelegramNotifier

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class AlertResult:
    telegram: str = "skipped"
    desktop: str = "skipped"
    logfile: str = "skipped"
    channels_reached: list[str] = field(default_factory=list)

    @property
    def delivered(self) -> bool:
        return bool(self.channels_reached)

    def summary(self) -> str:
        return (f"telegram={self.telegram} desktop={self.desktop} log={self.logfile}"
                f" -> reached: {', '.join(self.channels_reached) or 'NOTHING'}")


def _desktop(title: str, message: str) -> str:
    """macOS notification. No network involved, so a DNS outage cannot mute it."""
    if sys.platform != "darwin":
        return "unsupported"
    safe = message.replace('"', "'").replace("\\", "")[:240]
    safe_title = title.replace('"', "'")[:60]
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "{safe_title}" sound name "Basso"'],
            check=True, capture_output=True, timeout=10)
        return "sent"
    except Exception as exc:                       # never let alerting raise
        return f"failed:{type(exc).__name__}"


def _logfile(log_dir: Path, level: str, message: str) -> str:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(IST).isoformat(timespec="seconds")
        with (log_dir / "ALERTS.log").open("a") as fh:
            fh.write(f"{stamp} [{level}] {message}\n")
        # A dated marker makes an unnoticed failure obvious in a directory listing.
        if level in {"ERROR", "NO_TRADE"}:
            marker = log_dir / f"ALERT-{datetime.now(IST):%Y%m%d}.txt"
            with marker.open("a") as fh:
                fh.write(f"{stamp} [{level}] {message}\n")
        return "written"
    except Exception as exc:
        return f"failed:{type(exc).__name__}"


def alert(settings, message: str, level: str = "INFO",
          title: str = "Delta BTC 0DTE") -> AlertResult:
    """Fan out to every channel. Returns what each one actually did."""
    res = AlertResult()
    body = f"[{level}] {message}" if level != "INFO" else message

    delivery = TelegramNotifier(settings.telegram_token,
                                settings.telegram_chat_id).deliver(body)
    res.telegram = delivery.value
    if delivery is Delivery.SENT:
        res.channels_reached.append("telegram")

    # Only escalate to the desktop for things that need a human; routine
    # startup notices would train the alert to be ignored.
    if level != "INFO":
        res.desktop = _desktop(title, message)
        if res.desktop == "sent":
            res.channels_reached.append("desktop")

    res.logfile = _logfile(Path(settings.log_dir), level, message)
    if res.logfile == "written":
        res.channels_reached.append("logfile")
    return res
