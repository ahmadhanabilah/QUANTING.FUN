# hedge/hedge_bot.py
import asyncio
import logging
import os
from typing import Optional
import aiohttp

from bot.common.enums import Side, Venue


class HedgeBot:
    """
    Track maker deltas and hedge them once above a threshold.
    """

    def __init__(self, state, lighter, extended, min_qty_threshold: float = 0.0, enable_send: bool = True):
        self.state = state
        self.L = lighter
        self.E = extended
        self.min_qty_threshold = min_qty_threshold
        self.enable_send = enable_send
        self.unhedged_L = 0.0
        self.unhedged_E = 0.0
        # mirror on shared state if missing
        if not hasattr(self.state, "unhedged_L"):
            self.state.unhedged_L = 0.0
        if not hasattr(self.state, "unhedged_E"):
            self.state.unhedged_E = 0.0
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger("Hedge")

    async def on_maker_fill(self, venue: Venue, side: Side, qty: float):
        """Log maker fills."""
        async with self._lock:
            self._logger.info(
                f"[HedgeBot] maker_fill venue={venue.name} side={side.name} qty={qty} "
                f"invL={self.state.invL} invE={self.state.invE}"
            )

    async def on_position_update(self, venue: Venue, delta: float):
        """
        Account stream maker delta (signed). Update inventories/unhedged, then hedge if needed.
        """
        async with self._lock:
            if delta == 0:
                return

            if venue == Venue.L:
                self.state.invL += delta
                self.unhedged_L += delta
                self.state.unhedged_L = self.unhedged_L
            else:
                self.state.invE += delta
                self.unhedged_E += delta
                self.state.unhedged_E = self.unhedged_E

            self._logger.info(
                f"[HedgeBot] pos_delta venue={venue.name} delta={delta} "
                f"invL={self.state.invL} invE={self.state.invE} "
                f"unhedged_L={self.unhedged_L} unhedged_E={self.unhedged_E}"
            )

            await self._hedge_unhedged()

    async def _hedge_unhedged(self):
        """Send hedge orders when unhedged exposure exceeds threshold."""
        # hedge L exposure on E
        if self.unhedged_L != 0 and abs(self.unhedged_L) >= self.min_qty_threshold:
            qty = abs(self.unhedged_L)
            side = Side.SHORT if self.unhedged_L > 0 else Side.LONG
            if not self.enable_send:
                self._logger.info(
                    f"[HedgeBot] hedge_L_on_E status=dry-run side={side.name} qty={qty} "
                    f"unhedged_L={self.unhedged_L} unhedged_E={self.unhedged_E}"
                )
                self.unhedged_L = 0.0
                return
            if self._ob_ready(self.E):
                sent = await self._send_with_retry(self.E, side, qty, label="L_on_E")
                if sent:
                    self.unhedged_L -= sent if self.unhedged_L > 0 else -sent
                    self.state.unhedged_L = self.unhedged_L
            else:
                self._logger.info("[HedgeBot] skip hedge; Extended OB not ready")

        # hedge E exposure on L
        if self.unhedged_E != 0 and abs(self.unhedged_E) >= self.min_qty_threshold:
            qty = abs(self.unhedged_E)
            side = Side.SHORT if self.unhedged_E > 0 else Side.LONG
            if not self.enable_send:
                self._logger.info(
                    f"[HedgeBot] hedge_E_on_L status=dry-run side={side.name} qty={qty} "
                    f"unhedged_L={self.unhedged_L} unhedged_E={self.unhedged_E}"
                )
                self.unhedged_E = 0.0
                return
            if self._ob_ready(self.L):
                sent = await self._send_with_retry(self.L, side, qty, label="E_on_L")
                if sent:
                    self.unhedged_E -= sent if self.unhedged_E > 0 else -sent
                    self.state.unhedged_E = self.unhedged_E
            else:
                self._logger.info("[HedgeBot] skip hedge; Lighter OB not ready")

    async def _send_with_retry(self, venue, side: Side, qty: float, label: str, max_retries: int = 100, delay: float = 0.1) -> float:
        """Send market with retry; returns qty sent on success, 0 on failure."""
        for attempt in range(1, max_retries + 1):
            try:
                slip = getattr(venue, "config", {}).get("slippage", 0.0) if hasattr(venue, "config") else 0.0
                raw_price = venue.ob["askPrice"] if side == Side.LONG else venue.ob["bidPrice"]
                price = raw_price * (1 + slip) if side == Side.LONG else raw_price * (1 - slip)
                start_ts = asyncio.get_event_loop().time()
                res = await venue.send_market(side, qty, price)
                elapsed_ms = (asyncio.get_event_loop().time() - start_ts) * 1000
                if res:
                    sent_qty = float(res.get("filled_qty", qty))
                    self._logger.info(
                        f"[HedgeBot] hedge_{label} status=success side={side.name} price={price} size={sent_qty} "
                        f"attempt={attempt} latency_ms={elapsed_ms:.1f} "
                        f"unhedged_L={self.unhedged_L} unhedged_E={self.unhedged_E}"
                    )
                    return sent_qty
                else:
                    self._logger.error(
                        f"[HedgeBot] hedge_{label} status=failed side={side.name} price={price} size={qty} "
                        f"attempt={attempt} latency_ms={elapsed_ms:.1f} "
                        f"unhedged_L={self.unhedged_L} unhedged_E={self.unhedged_E}"
                    )
            except Exception as exc:
                self._logger.error(f"[HedgeBot] hedge send error ({label}) attempt {attempt}: {exc}")
            if attempt < max_retries:
                await asyncio.sleep(delay)
        self._logger.error(f"[HedgeBot] hedge send failed after {max_retries} attempts on {label}")
        await self._notify_telegram(f"Hedge send failed on {label} side={side.name} qty={qty}")
        return 0.0

    async def _notify_telegram(self, msg: str) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": msg}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload) as resp:
                    if resp.status != 200:
                        self._logger.error(f"[HedgeBot] telegram notify failed HTTP {resp.status}")
        except Exception as exc:
            self._logger.error(f"[HedgeBot] telegram notify error: {exc}")

    def _ob_ready(self, venue) -> bool:
        ob = getattr(venue, "ob", None) or {}
        return bool(ob.get("bidPrice") and ob.get("askPrice"))
