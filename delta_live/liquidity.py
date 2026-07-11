from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    mark: Decimal
    timestamp_us: int

    @classmethod
    def from_ticker(cls, row: dict[str, Any]) -> "Quote":
        quotes = row.get("quotes") or {}
        return cls(str(row["symbol"]), dec(quotes.get("best_bid") or 0), dec(quotes.get("best_ask") or 0),
                   dec(quotes.get("bid_size") or 0), dec(quotes.get("ask_size") or 0),
                   dec(row.get("mark_price") or 0), int(row.get("timestamp") or 0))

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else self.mark

    @property
    def spread_pct(self) -> Decimal:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else Decimal("999")


@dataclass(frozen=True)
class LiquidityDecision:
    allowed: bool
    reason: str
    supported_size: int
    executable_credit: Decimal
    mid_credit: Decimal


def entry_gate(call: Quote, put: Quote, requested_size: int, max_spread_pct: Decimal,
               min_credit_ratio: Decimal) -> LiquidityDecision:
    if requested_size <= 0:
        return LiquidityDecision(False, "invalid_size", 0, Decimal(0), Decimal(0))
    if call.bid <= 0 or put.bid <= 0 or call.ask <= 0 or put.ask <= 0:
        return LiquidityDecision(False, "missing_two_sided_quote", 0, Decimal(0), call.mid + put.mid)
    supported = min(int(call.bid_size), int(put.bid_size), requested_size)
    credit, mid = call.bid + put.bid, call.mid + put.mid
    if call.spread_pct > max_spread_pct or put.spread_pct > max_spread_pct:
        return LiquidityDecision(False, "spread_too_wide", supported, credit, mid)
    if mid <= 0 or credit / mid < min_credit_ratio:
        return LiquidityDecision(False, "executable_credit_below_floor", supported, credit, mid)
    if supported < requested_size:
        return LiquidityDecision(False, "insufficient_top_level_depth", supported, credit, mid)
    return LiquidityDecision(True, "ok", supported, credit, mid)


def tick_price(value: Decimal, tick: Decimal, side: str) -> str:
    rounding = ROUND_DOWN if side == "sell" else ROUND_UP
    units = (value / tick).to_integral_value(rounding=rounding)
    return format(units * tick, "f")
