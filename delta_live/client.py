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

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.settings.assert_order_mode(allow_production=os.getenv("DELTA_PRODUCTION_ORDER_MODE") == "1")
        return self.request("POST", "/v2/orders", payload=order, auth=True)

    def cancel_order(self, order_id: int, product_id: int) -> dict[str, Any]:
        self.settings.assert_order_mode(allow_production=os.getenv("DELTA_PRODUCTION_ORDER_MODE") == "1")
        return self.request("DELETE", "/v2/orders", payload={"id": order_id, "product_id": product_id}, auth=True)
