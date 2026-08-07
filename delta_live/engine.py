from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Callable
from zoneinfo import ZoneInfo

from .client import DeltaRESTClient
from .config import Settings
from .liquidity import Quote, entry_gate, tick_price
from .telegram import TelegramNotifier

IST = ZoneInfo("Asia/Kolkata")


class State(str, Enum):
    STARTING = "starting"
    READY = "ready"
    ENTERING = "entering"
    OPEN = "open"
    EXITING = "exiting"
    CLOSED = "closed"
    NO_TRADE = "no_trade"
    HALTED = "halted"


@dataclass(frozen=True)
class StrategyConfig:
    asset: str = "BTC"
    size: int = 200
    entry_time: str = "17:00:00"
    force_exit_time: str = "17:25:00"
    stop_pct: Decimal = Decimal("0.50")
    max_spread_pct: Decimal = Decimal("0.15")
    min_credit_ratio: Decimal = Decimal("0.95")
    persistence_ticks: int = 2
    poll_seconds: float = 1.0
    max_daily_loss_usd: Decimal = Decimal("100")


class ExecutionEngine:
    """Sandbox-first paired straddle executor with a REST recovery transport."""
    def __init__(self, settings: Settings, strategy: StrategyConfig,
                 client: DeltaRESTClient | None = None, clock: Callable[[], datetime] | None = None):
        self.settings, self.strategy = settings, strategy
        self.client = client or DeltaRESTClient(settings)
        self.clock = clock or (lambda: datetime.now(IST))
        self.notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
        self.state = State.STARTING
        self.entry_credit: Decimal | None = None
        self.stop_hits = 0
        # Which legs are actually held. A single-sided fill is retained rather
        # than flattened, so the risk loop must know what it is protecting.
        self.live_call = True
        self.live_put = True
        # Set by run_session so a stop-out can be attributed to the underlying
        # move rather than inferred from option repricing after the fact.
        self.entry_spot: Decimal | None = None
        self.strike: Decimal | None = None
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = settings.log_dir / f"events-{self.clock():%Y%m%d}.jsonl"

    def event(self, name: str, **fields: object) -> None:
        row = {"time": self.clock().isoformat(), "state": self.state.value, "event": name, **fields}
        with self.log_path.open("a") as handle:
            handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")
        if name in {"ready", "no_trade", "adopted_position", "entry_filled", "stop_armed",
                    "stop_triggered", "closed", "halted"}:
            message = (f"Delta {self.settings.environment.upper()} | {name}\n" +
                       "\n".join(f"{k}: {v}" for k, v in fields.items()))
            # A slow Telegram API must never delay stop arming or risk ticks.
            threading.Thread(target=self.notifier.send, args=(message,),
                             name=f"telegram-{name}", daemon=False).start()

    def select_atm(self, chain: list[dict], spot: Decimal) -> tuple[dict, dict]:
        by_strike: dict[Decimal, dict[str, dict]] = {}
        for row in chain:
            strike = Decimal(str(row.get("strike_price") or 0))
            kind = str(row.get("contract_type", ""))
            if kind in {"call_options", "put_options"}:
                by_strike.setdefault(strike, {})[kind] = row
        complete = [(abs(strike - spot), strike, legs) for strike, legs in by_strike.items() if len(legs) == 2]
        if not complete:
            raise RuntimeError("No complete call/put strike found")
        _, _, legs = min(complete, key=lambda item: item[0])
        return legs["call_options"], legs["put_options"]

    def preflight(self, call_row: dict, put_row: dict) -> tuple[Quote, Quote]:
        call, put = Quote.from_ticker(call_row), Quote.from_ticker(put_row)
        gate = entry_gate(call, put, self.strategy.size, self.strategy.max_spread_pct,
                          self.strategy.min_credit_ratio)
        integrity_failure = gate.reason in {"invalid_size", "missing_two_sided_quote"}
        if not gate.allowed and (not self.settings.permissive_entry or integrity_failure):
            self.state = State.NO_TRADE
            self.event("no_trade", reason=gate.reason, supported_size=gate.supported_size,
                       executable_credit=gate.executable_credit, mid_credit=gate.mid_credit)
            raise RuntimeError(f"Liquidity gate failed: {gate.reason}")
        self.state = State.READY
        if not gate.allowed:
            self.event("entry_gate_warning", reason=gate.reason,
                       supported_size=gate.supported_size,
                       executable_credit=gate.executable_credit,
                       mid_credit=gate.mid_credit,
                       mode="telemetry_only")
        self.event("ready", call=call.symbol, put=put.symbol, size=self.strategy.size,
                   executable_credit=gate.executable_credit,
                   permissive_entry=self.settings.permissive_entry)
        return call, put

    def order_payload(self, product: dict, side: str, size: int, price: Decimal,
                      suffix: str, reduce_only: bool) -> dict:
        tick = Decimal(str(product["tick_size"]))
        return {"product_id": int(product["id"]), "size": int(size), "side": side,
                "order_type": "limit_order", "limit_price": tick_price(price, tick, side),
                "time_in_force": "ioc", "post_only": False, "reduce_only": reduce_only,
                "client_order_id": f"d0-{self.clock():%m%d%H%M%S}-{suffix}"[:32]}

    def record_entry(self, call_fill: Decimal, put_fill: Decimal) -> None:
        self.entry_credit = call_fill + put_fill
        self.state = State.OPEN
        self.event("entry_filled", call_fill=call_fill, put_fill=put_fill,
                   combined_credit=self.entry_credit, stop_level=self.stop_level)
        self.event("stop_armed", trigger="executable_combined_ask", level=self.stop_level,
                   persistence_ticks=self.strategy.persistence_ticks)

    @property
    def stop_level(self) -> Decimal:
        if self.entry_credit is None:
            raise RuntimeError("Entry has not filled")
        return self.entry_credit * (Decimal(1) + self.strategy.stop_pct)

    def observe_stop(self, call: Quote, put: Quote) -> bool:
        if self.state != State.OPEN:
            return False
        # A leg that was never filled contributes no buyback cost. Including its
        # ask would inflate the stop trigger and stop a single-leg position early.
        executable_buyback = Decimal(0)
        if self.live_call:
            executable_buyback += call.ask
        if self.live_put:
            executable_buyback += put.ask
        self.stop_hits = self.stop_hits + 1 if executable_buyback >= self.stop_level else 0
        self.event("risk_tick", executable_buyback=executable_buyback, stop_level=self.stop_level,
                   consecutive_hits=self.stop_hits, call_mark=call.mark, put_mark=put.mark)
        if self.stop_hits >= self.strategy.persistence_ticks:
            self.state = State.EXITING
            self.event("stop_triggered", executable_buyback=executable_buyback,
                       stop_level=self.stop_level,
                       **self.underlying_move(call, put))
            return True
        return False

    def underlying_move(self, call: Quote, put: Quote) -> dict[str, object]:
        """Spot at this moment and how far it has travelled since entry.

        Derived from put-call parity on the marks already in hand
        (spot ~ strike + call_mark - put_mark) rather than a REST call, so it
        costs nothing inside the risk loop.

        This exists to separate two explanations for a stop-out that the log
        could not previously distinguish: the underlying genuinely moved, versus
        premium expanded on vol or spread. Without it the 5 August loss could
        only be attributed to "the call repriced 75 -> 133", with the size of
        the actual BTC move inferred rather than measured.
        """
        if self.strike is None or not (self.live_call and self.live_put):
            return {"spot_at_trigger": None, "spot_move_from_entry": None,
                    "spot_source": "unavailable_single_leg_or_no_strike"}
        implied = self.strike + call.mark - put.mark
        move = (implied - self.entry_spot) if self.entry_spot is not None else None
        out: dict[str, object] = {
            "spot_at_trigger": implied,
            "entry_spot": self.entry_spot,
            "spot_move_from_entry": move,
            "spot_source": "put_call_parity_on_marks",
        }
        if move is not None and self.entry_spot:
            out["spot_move_pct"] = move / self.entry_spot * 100
            out["stop_headroom_points"] = self.stop_level - self.entry_credit
        return out

    def halt(self, reason: str) -> None:
        self.state = State.HALTED
        self.event("halted", reason=reason)
