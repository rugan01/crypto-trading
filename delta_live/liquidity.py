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
        """Quote from /v2/tickers.

        NOTE: this endpoint is a CACHED SNAPSHOT. Measured on 2026-08-12 it
        refreshes roughly every 5.5 seconds and is 1.7-7.4 seconds old when
        read; polling it faster returns the identical snapshot with the
        identical timestamp. It is fine for discovery and for the risk loop's
        REST fallback, but it must not be used to price an order in a moving
        book - see `from_l2` and the comment on `Quote.age_seconds`.
        """
        quotes = row.get("quotes") or {}
        return cls(str(row["symbol"]), dec(quotes.get("best_bid") or 0), dec(quotes.get("best_ask") or 0),
                   dec(quotes.get("bid_size") or 0), dec(quotes.get("ask_size") or 0),
                   dec(row.get("mark_price") or 0), int(row.get("timestamp") or 0))

    @classmethod
    def from_l2(cls, symbol: str, book: dict[str, Any]) -> "Quote":
        """Quote from /v2/l2orderbook - true top of book, typically sub-second.

        Measured against /v2/tickers on 2026-08-12: median age 0.61s versus
        4.21s, and it actually moves between reads instead of repeating one
        cached snapshot. Price orders from this; the ticker is for discovery
        and for the risk loop's REST fallback.
        """
        def touch(levels: list[dict[str, Any]] | None, best: str) -> tuple[Decimal, Decimal]:
            rows = [(dec(r.get("price") or 0), dec(r.get("size") or 0)) for r in (levels or [])]
            rows = [r for r in rows if r[0] > 0 and r[1] > 0]
            if not rows:
                return Decimal(0), Decimal(0)
            return max(rows, key=lambda r: r[0]) if best == "bid" else min(rows, key=lambda r: r[0])

        bid, bid_size = touch(book.get("buy"), "bid")
        ask, ask_size = touch(book.get("sell"), "ask")
        mark = (bid + ask) / 2 if bid > 0 and ask > 0 else Decimal(0)
        return cls(symbol, bid, ask, bid_size, ask_size, mark,
                   int(book.get("last_updated_at") or 0))

    def age_seconds(self, now_us: float) -> Decimal:
        """Seconds between this quote's exchange timestamp and `now_us`.

        Returns a large sentinel when the source carried no timestamp, so an
        untimestamped quote is treated as stale rather than as fresh.
        """
        if self.timestamp_us <= 0:
            return Decimal("999")
        return dec(round((now_us - self.timestamp_us) / 1_000_000, 3))

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
