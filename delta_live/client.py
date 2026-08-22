from __future__ import annotations

import hashlib
import hmac
import json
import random
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

from .config import Settings


class DeltaAPIError(RuntimeError):
    pass


class DeltaRESTClient:
    def __init__(self, settings: Settings, retries: int = 5):
        self.settings = settings
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "delta-0dte-live/0.1"})

    def _headers(self, method: str, path: str, query: str, body: str) -> dict[str, str]:
        if not self.settings.api_key or not self.settings.api_secret:
            raise DeltaAPIError("API credentials are not configured for this environment")
        timestamp = str(int(time.time()))
        message = method + timestamp + path + query + body
        signature = hmac.new(self.settings.api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {"api-key": self.settings.api_key, "timestamp": timestamp, "signature": signature,
                "Content-Type": "application/json", "User-Agent": "delta-0dte-live/0.1"}

    def request(self, method: str, path: str, params: dict[str, Any] | None = None,
                payload: dict[str, Any] | None = None, auth: bool = False) -> Any:
        params = params or {}
        query = "?" + urlencode(params) if params else ""
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True) if payload is not None else ""
        for attempt in range(self.retries + 1):
            # Delta rejects stale timestamps, so every retry is signed afresh.
            headers = self._headers(method, path, query, body) if auth else {}
            try:
                response = self.session.request(method, self.settings.rest_url + path,
                                                params=params, data=body or None, headers=headers, timeout=15)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise DeltaAPIError(f"{method} {path} failed: {type(exc).__name__}") from exc
            else:
                if response.ok:
                    data = response.json()
                    if data.get("success"):
                        return data.get("result")
                    raise DeltaAPIError(f"{method} {path} returned success=false")
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise DeltaAPIError(f"{method} {path} HTTP {response.status_code}: {response.text[:200]}")
            time.sleep(min(8.0, 0.4 * 2**attempt) + random.random() / 5)
        raise AssertionError("unreachable")

    def ticker(self, symbol: str) -> dict[str, Any]:
        return self.request("GET", f"/v2/tickers/{symbol}")

    def l2_orderbook(self, symbol: str, depth: int = 50) -> dict[str, Any]:
        return self.request("GET", f"/v2/l2orderbook/{symbol}", {"depth": depth})

    def product(self, symbol: str) -> dict[str, Any]:
        return self.request("GET", f"/v2/products/{symbol}")

    def option_chain(self, asset: str, expiry: str) -> list[dict[str, Any]]:
        return self.request("GET", "/v2/tickers", {"contract_types": "call_options,put_options",
                            "underlying_asset_symbols": asset, "expiry_date": expiry})

    def positions(self, asset: str) -> list[dict[str, Any]]:
        return self.request("GET", "/v2/positions", {"underlying_asset_symbol": asset}, auth=True)

    def balances(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v2/wallet/balances", auth=True)

    def fills(self, product_id: int | None = None, page_size: int = 200,
              start_time: int | None = None, end_time: int | None = None
              ) -> list[dict[str, Any]]:
        """Authenticated fills, newest first.

        Delta defaults page_size to 10, which silently looks like "the API only
        keeps a few days of history". It does not - it keeps at least a month.
        Always pass an explicit page_size when reconciling commissions.
        start_time/end_time are microseconds since epoch.
        """
        params: dict[str, Any] = {"page_size": page_size}
        if product_id is not None:
            params["product_id"] = product_id
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time
        return self.request("GET", "/v2/fills", params, auth=True)

    def order(self, order_id: int) -> dict[str, Any]:
        return self.request("GET", f"/v2/orders/{order_id}", auth=True)

    def order_leverage(self, product_id: int) -> dict[str, Any]:
        return self.request("GET", f"/v2/products/{product_id}/orders/leverage", auth=True)

    def active_orders(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v2/orders", {"state": "open"}, auth=True)

    def active_orders_for(self, asset: str) -> list[dict[str, Any]]:
        """Open orders on `asset` only.

        The entry guards must block on leftover state for the instrument being
        traded, not on unrelated positions elsewhere in the account. On
        2026-08-06 a reduce-only protective stop on P-XAUT-4200-070826 aborted
        a BTC session whose own book was completely flat.

        Matches the asset anywhere in the product symbol, so it covers options
        (C-BTC-64600-060826) and perpetuals (BTCUSD) alike - a BTC perp carries
        delta on the same underlying and is genuine leftover state, even though
        it does not follow the hyphenated option format.
        """
        needle = asset.upper()
        return [o for o in self.active_orders()
                if needle in str(o.get("product_symbol") or "").upper()]

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.settings.assert_order_mode(allow_production=os.getenv("DELTA_PRODUCTION_ORDER_MODE") == "1")
        return self.request("POST", "/v2/orders", payload=order, auth=True)

    def cancel_order(self, order_id: int, product_id: int) -> dict[str, Any]:
        self.settings.assert_order_mode(allow_production=os.getenv("DELTA_PRODUCTION_ORDER_MODE") == "1")
        return self.request("DELETE", "/v2/orders", payload={"id": order_id, "product_id": product_id}, auth=True)


class PaperRESTClient(DeltaRESTClient):
    """Reads the real market; simulates every order instead of sending it.

    Why this exists. On 20 and 21 August 2026 the book was liquidated on consecutive
    sessions -- at 100 lots and then at 50 -- and those two sessions cost more than the
    other 32 combined ($-25.09 against a lifetime $-20.78, so the book is $+4.31 without
    them). When the exchange liquidates before the engine's own stop can fire, the
    strategy is not being tested: a margin-constrained variant of it is. Paper mode keeps
    every real input -- live quotes, real IVs, the real stop logic, the real clock -- and
    removes only the thing that was doing the damage.

    Fills are simulated at the price the order would actually have hit, taken from the
    limit the engine computed against live L2. That is optimistic in one specific way and
    the log says so: a real IOC can partially fill or miss, and on 21 Aug the live put leg
    needed two retries before it filled. Paper P&L should therefore be read as the
    strategy's ceiling, not its expectation.

    Everything that is not order placement passes straight through to the real API, so
    quotes, balances and fills history remain genuine.
    """

    def __init__(self, settings, *args, **kwargs):
        super().__init__(settings, *args, **kwargs)
        self._paper_seq = 0

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self._paper_seq += 1
        # A limit IOC that we assume crosses in full. `filled()` derives the fill from
        # size minus unfilled_size and average_fill_price, so the shape must match what
        # the real endpoint returns or the caller silently sees a zero fill.
        return {
            "id": f"PAPER-{self._paper_seq:06d}",
            "size": int(order.get("size") or 0),
            "unfilled_size": 0,
            "average_fill_price": str(order.get("limit_price")),
            "state": "closed",
            "product_id": order.get("product_id"),
            "side": order.get("side"),
            "client_order_id": order.get("client_order_id"),
            "paper": True,
        }

    def cancel_order(self, order_id, product_id) -> dict[str, Any]:
        return {"id": order_id, "product_id": product_id, "state": "cancelled", "paper": True}

    def positions(self, asset: str | None = None):
        """No real position is ever opened on paper, so the book is always flat.

        Returning the genuine (empty) position list matters: `close_positions` reconciles
        against the broker before exiting, and on a real account that reconciliation is
        what discovered the 20 Aug liquidation. Reporting a fake open position here would
        make the paper session diverge from the code path the live one takes.
        """
        return []

    def active_orders_for(self, asset: str | None = None):
        return []


def make_client(settings, *args, **kwargs) -> DeltaRESTClient:
    """The only correct way to build a client.

    Paper mode has to be decided here rather than at each call site, because a single
    site that constructs DeltaRESTClient directly would place real orders while every
    log line and every event still said `paper`. That failure would be invisible until
    the fills appeared.
    """
    if getattr(settings, "paper_mode", False):
        return PaperRESTClient(settings, *args, **kwargs)
    return DeltaRESTClient(settings, *args, **kwargs)
