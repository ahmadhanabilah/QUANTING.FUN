import asyncio
import json
import logging
import os
from typing import Callable, Optional

import aiohttp

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
        self._got_first_ob: bool = False
        self.ws_url = os.getenv("HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws")

    # ---------- public API ----------

    def set_ob_callback(self, cb: Callable[[], None]) -> None:
        self._on_ob_update_cb = cb

    async def start(self) -> None:
        """Start orderbook websocket."""
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = asyncio.create_task(self._run_ws())
        await self._ws_task

    # ---------- internal ----------

    async def _run_ws(self):
        while True:
            try:
                logger.info("Subscribing [HYP OB]")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.ws_url, heartbeat=30) as ws:
                        sub_payload = {
                            "type": "subscribe",
                            "channel": "book",
                            "coin": self.symbol,
                            "depth": 1,
                        }
                        await ws.send_str(json.dumps(sub_payload))
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    payload = msg.json()
                                except Exception:
                                    continue
                                self._handle_orderbook(payload)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                raise RuntimeError(f"Hyperliquid WS error: {ws.exception()}")
            except Exception as exc:
                logger.error(f"HYP ob stream error: {exc}; reconnecting in 1s")
                self.ob = {"bidPrice": 0.0, "askPrice": 0.0, "bidSize": 0.0, "askSize": 0.0}
                await asyncio.sleep(1)

    def _handle_orderbook(self, payload) -> None:
        try:
            data = payload.get("data") if isinstance(payload, dict) else None
            book = data.get("levels") if isinstance(data, dict) else None
            if not book or not isinstance(book, dict):
                return
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids or not asks:
                return
            bid = bids[0]
            ask = asks[0]
            new_ob = {
                "bidPrice": float(bid[0]),
                "askPrice": float(ask[0]),
                "bidSize": float(bid[1]),
                "askSize": float(ask[1]),
            }
            if self.dedup_ob and self.ob == new_ob:
                return
            self.ob = new_ob
            if not self._got_first_ob:
                logger.info("[HYP GOT FIRST OB]")
                self._got_first_ob = True
            if self._on_ob_update_cb:
                self._on_ob_update_cb()
        except Exception as exc:
            logger.error(f"handle_orderbook error: {exc}")
