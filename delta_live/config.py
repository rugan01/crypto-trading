from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

TESTNET_REST = "https://cdn-ind.testnet.deltaex.org"
PRODUCTION_REST = "https://api.india.delta.exchange"
TESTNET_PUBLIC_WS = "wss://socket-ind-pub.testnet.deltaex.org"
PRODUCTION_PUBLIC_WS = "wss://public-socket.india.delta.exchange"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    dry_run: bool
    api_key: str | None
    api_secret: str | None
    telegram_token: str | None
    telegram_chat_id: str | None
    rest_url: str
    public_ws_url: str
    log_dir: Path
    permissive_entry: bool = False
    paper_mode: bool = False

    @classmethod
    def load(cls, env_file: Path = Path(".env")) -> "Settings":
        load_dotenv(env_file)
        environment = os.getenv("DELTA_ENV", "testnet").lower()
        if environment not in {"testnet", "production"}:
            raise ValueError("DELTA_ENV must be testnet or production")
        if environment == "testnet":
            key = os.getenv("DELTA_TESTNET_API_KEY") or os.getenv("DEMO_API_KEY")
            secret = os.getenv("DELTA_TESTNET_API_SECRET") or os.getenv("DEMO_API_SECRET")
            rest, ws = TESTNET_REST, TESTNET_PUBLIC_WS
        else:
            key = os.getenv("DELTA_API_KEY") or os.getenv("API_KEY")
            secret = os.getenv("DELTA_API_SECRET") or os.getenv("API_SECRET")
            rest, ws = PRODUCTION_REST, PRODUCTION_PUBLIC_WS
        return cls(environment, env_bool("DELTA_DRY_RUN", True), key, secret,
                   os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"),
                   rest, ws, Path(os.getenv("DELTA_LOG_DIR", "outputs/live")),
                   env_bool("DELTA_PERMISSIVE_ENTRY", False),
                   env_bool("DELTA_PAPER_MODE", False))

    def assert_order_mode(self, allow_production: bool = False) -> None:
        # Paper mode never reaches the exchange, so the production authorisation gates
        # below do not apply to it. PaperRESTClient short-circuits before this is called;
        # this branch exists so that a mis-wired call path fails safe rather than
        # silently placing a real order while the operator believes it is on paper.
        if self.paper_mode:
            raise RuntimeError("DELTA_PAPER_MODE is on; orders must not reach the exchange")
        if self.environment == "production" and not allow_production:
            raise RuntimeError("Production orders require an explicit production confirmation")
        if self.environment == "production" and os.getenv("DELTA_PRODUCTION_ACK") != "LIVE_ORDERS_AUTHORIZED":
            raise RuntimeError("Set the one-session DELTA_PRODUCTION_ACK to authorize production")
        if self.dry_run:
            raise RuntimeError("Order submission is disabled while DELTA_DRY_RUN=true")
        if not self.api_key or not self.api_secret:
            raise RuntimeError("API credentials are required for the selected environment")
