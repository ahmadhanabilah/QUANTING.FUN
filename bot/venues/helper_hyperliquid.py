import asyncio
import json
import logging
import os
import threading
from typing import Callable, Optional

import aiohttp

# Prefer SDK import style from official examples (Info + constants).
_HYPER_SDK_AVAILABLE = False
_HYPER_SDK_IMPORT_ERR = None
try:  # pragma: no cover - optional dependency
    from hyperliquid.info import Info  # type: ignore
    from hyperliquid.utils import constants as hl_constants  # type: ignore

    _HYPER_SDK_AVAILABLE = True
except Exception as exc:
    Info = None  # type: ignore
    hl_constants = None  # type: ignore
    _HYPER_SDK_IMPORT_ERR = exc

logger = logging.getLogger("HYP")
logger.setLevel(logging.INFO)


class HyperliquidWS:
    """
    Minimal Hyperliquid venue wrapper for screening:
    - maintains self.ob["bidPrice"], self.ob["askPrice"], bidSize/askSize
    - emits a callback on every OB update
    Trading is not implemented.
    """

    def __init__(self, symbol: str, read_only: bool = True):
        self.symbol = symbol
        self.read_only = read_only
        self.ob = {
            "bidPrice": 0.0,
            "askPrice": 0.0,
            "bidSize": 0.0,
            "askSize": 0.0,
        }
        self.dedup_ob = False
        self._on_ob_update_cb: Optional[Callable[[], None]] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._sdk_thread: Optional[threading.Thread] = None
        self._shutdown = threading.Event()
        self._got_first_ob: bool = False
        # prefer SDK; fall back to raw WS if SDK is unavailable.
        self._use_sdk = _HYPER_SDK_AVAILABLE
        self.ws_url = os.getenv(
            "HYPERLIQUID_WS_URL",
            getattr(hl_constants, "WS_URL", "wss://api.hyperliquid.xyz/ws") if hl_constants else "wss://api.hyperliquid.xyz/ws",
        )

    # ---------- public API ----------

    def set_ob_callback(self, cb: Callable[[], None]) -> None:
        self._on_ob_update_cb = cb

    async def start(self) -> None:
        """Start orderbook websocket."""
        if self._ws_task and not self._ws_task.done():
            return
        if self._use_sdk:
            self._ws_task = asyncio.create_task(self._run_sdk_ws())
        else:
            raise RuntimeError(
                f"Hyperliquid SDK not available ({_HYPER_SDK_IMPORT_ERR}); install hyperliquid-python-sdk "
                "or activate the correct venv to use HyperliquidWS"
            )
        await self._ws_task

    # ---------- internal ----------

    async def _run_sdk_ws(self):
        """Use hyperliquid-python-sdk WebsocketClient if available; otherwise fall back to raw WS."""
        if not Info:
            raise RuntimeError(f"Hyperliquid SDK not available ({_HYPER_SDK_IMPORT_ERR})")

        backoff = 1
        while True:
            try:
                self._shutdown.clear()
                api_url = os.getenv(
                    "HYPERLIQUID_API_URL",
                    getattr(hl_constants, "MAINNET_API_URL", "https://api.hyperliquid.xyz"),
                )
                logger.info(f"Subscribing [HYP OB] via SDK Info at {api_url}")
                info = Info(api_url, skip_ws=False)  # type: ignore
                info.subscribe({"type": "l2Book", "coin": self.symbol}, self._handle_sdk_message)
                while not self._shutdown.is_set():
                    await asyncio.sleep(1)
                return
            except Exception as exc:
                logger.error(f"HYP sdk ob stream error: {exc}; reconnecting in {backoff}s")
                self.ob = {"bidPrice": 0.0, "askPrice": 0.0, "bidSize": 0.0, "askSize": 0.0}
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _handle_sdk_message(self, payload) -> None:
        """Handle SDK websocket payloads."""
        try:
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    return
            self._ingest_book(payload)
        except Exception as exc:
            logger.error(f"handle_sdk_orderbook error: {exc}")

    def _handle_orderbook(self, payload) -> None:
        try:
            self._ingest_book(payload)
        except Exception as exc:
            logger.error(f"handle_orderbook error: {exc}")

    def _ingest_book(self, payload) -> None:
        """Shared orderbook handler for SDK/raw payload shapes."""
        data = payload.get("data") if isinstance(payload, dict) else None
        book = None
        if isinstance(data, dict):
            book = data.get("levels") or data.get("book") or data
        elif isinstance(payload, dict):
            book = payload.get("levels") or payload.get("book")
        if not book:
            return
        # l2Book shape: levels -> [bids, asks] where entries have px/sz
        if isinstance(book, list) and len(book) >= 2:
            bids = book[0] or []
            asks = book[1] or []
        elif isinstance(book, dict):
            bids = book.get("bids") or []
            asks = book.get("asks") or []
        else:
            return
        if not bids or not asks:
            return
        bid = bids[0]
        ask = asks[0]
        new_ob = {
            "bidPrice": float(bid[0] if isinstance(bid, (list, tuple)) else bid.get("px")),
            "askPrice": float(ask[0] if isinstance(ask, (list, tuple)) else ask.get("px")),
            "bidSize": float(bid[1] if isinstance(bid, (list, tuple)) else bid.get("sz", 0)),
            "askSize": float(ask[1] if isinstance(ask, (list, tuple)) else ask.get("sz", 0)),
        }
        if self.dedup_ob and self.ob == new_ob:
            return
        self.ob = new_ob
        if not self._got_first_ob:
            logger.info("[HYP GOT FIRST OB]")
            self._got_first_ob = True
        if self._on_ob_update_cb:
            self._on_ob_update_cb()
