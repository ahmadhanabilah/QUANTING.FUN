# venues/extended_ws.py
import asyncio
import logging
import os
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Optional
import time

import aiohttp
from common.enums import Side
from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.orders import OrderSide as ExtendedOrderSide, TimeInForce
from x10.perpetual.stream_client import PerpetualStreamClient
from x10.perpetual.trading_client import PerpetualTradingClient

logger = logging.getLogger("ExtendedWS")


class ExtendedWS:
    """
    Minimal Extended venue wrapper for MAKER bot:
    - maintains self.ob["bidPrice"], self.ob["askPrice"]
    - calls a callback on every OB update
    - no trading, no positions (for now)
    """

    def __init__(self, symbol: str, read_only: bool = False):
        self.symbol = symbol
        self.read_only = read_only
        self.ob = {
            "bidPrice": 0.0,
            "askPrice": 0.0,
            "bidSize": 0.0,
            "askSize": 0.0,
        }
        self.dedup_ob = False
        self.position_qty: float = 0.0
        self.position_entry: float = 0.0
        self.last_fill_price: float = 0.0
        self.config = {
            "vault_id": os.getenv("EXTENDED_VAULT_ID"),
            "private_key": os.getenv("EXTENDED_PRIVATE_KEY"),
            "public_key": os.getenv("EXTENDED_PUBLIC_KEY"),
            "api_key": os.getenv("EXTENDED_API_KEY"),
            "slippage": float(os.getenv("EXTENDED_SLIPPAGE", "0.001")),
        }
        self.min_size: Optional[float] = None
        self.min_price_change: Optional[float] = None
        self.min_size_change: Optional[float] = None
        self.asset_precision: Optional[int] = None
        self._on_ob_update_cb: Optional[Callable[[], None]] = None
        self._on_account_update_cb: Optional[Callable[[float], None]] = None
        self._on_inventory_cb: Optional[Callable[[float], None]] = None
        self._on_position_state_cb: Optional[Callable[[float, float], None]] = None
        self._ws_client = PerpetualStreamClient(api_url=MAINNET_CONFIG.stream_url)
        self._trading_client: Optional[PerpetualTradingClient] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._account_task: Optional[asyncio.Task] = None

    # ---------- public API ----------

    def set_ob_callback(self, cb: Callable[[], None]) -> None:
        self._on_ob_update_cb = cb
    def set_account_callback(self, cb: Callable[[float], None]) -> None:
        self._on_account_update_cb = cb
    def set_inventory_callback(self, cb: Callable[[float], None]) -> None:
        self._on_inventory_cb = cb
    def set_position_state_callback(self, cb: Callable[[float, float], None]) -> None:
        self._on_position_state_cb = cb

    async def start(self) -> None:
        """Start WS subscribers (orderbook only)."""
        if not self.read_only:
            await self._init_trading_client()
        await self._load_market_info()
        self._start_ws_loop()
        if not self.read_only:
            self._start_account_loop()
        if self._ws_task:
            await self._ws_task
        if self._account_task:
            await self._account_task

    # ---------- internal ----------

    async def _init_trading_client(self) -> None:
        if self._trading_client:
            return
        vals = [self.config["vault_id"], self.config["private_key"], self.config["public_key"], self.config["api_key"]]
        if not all(vals):
            raise RuntimeError("Missing EXTENDED_* credentials in environment")
        stark_acc = StarkPerpetualAccount(
            vault=int(self.config["vault_id"]),
            private_key=self.config["private_key"],
            public_key=self.config["public_key"],
            api_key=self.config["api_key"],
        )
        self._trading_client = PerpetualTradingClient(MAINNET_CONFIG, stark_acc)

    async def _load_market_info(self) -> None:
        if self.min_price_change is not None:
            return
        url = f"https://api.starknet.extended.exchange/api/v1/info/markets?market={self.symbol}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        if data.get("status") != "OK":
            raise RuntimeError(f"Extended market info error: {data}")
        market_info = data["data"][0]
        trading_cfg = market_info["tradingConfig"]
        self.min_size = float(trading_cfg["minOrderSize"])
        self.min_size_change = float(trading_cfg.get("minOrderSizeChange", trading_cfg["minOrderSize"]))
        self.min_price_change = float(trading_cfg["minPriceChange"])
        self.asset_precision = int(market_info["assetPrecision"])

    def _start_ws_loop(self) -> None:
        if self._ws_task and not self._ws_task.done():
            return

        async def subscribe_orderbook():
            while True:
                try:
                    logger.info(f"[ExtendedWS:{self.symbol}] subscribing orderbook")
                    async with self._ws_client.subscribe_to_orderbooks(self.symbol, depth=1) as stream:
                        logger.info(f"[ExtendedWS:{self.symbol}] subscribed orderbook")
                        async for msg in stream:
                            self._handle_orderbook(msg.data)
                except Exception as e:
                    logger.error(f"[ExtendedWS] OB stream error: {e}; reconnecting in 1s")
                    self.ob = {"bidPrice": 0.0, "askPrice": 0.0, "bidSize": 0.0, "askSize": 0.0}
                    await asyncio.sleep(1)

        self._ws_task = asyncio.create_task(subscribe_orderbook())

    def _start_account_loop(self) -> None:
        if self._account_task and not self._account_task.done():
            return
        if not self.config.get("api_key"):
            return

        async def subscribe_account():
            while True:
                try:
                    logger.info(f"[ExtendedWS:{self.symbol}] subscribing account stream")
                    async with self._ws_client.subscribe_to_account_updates(self.config["api_key"]) as stream:
                        logger.info(f"[ExtendedWS:{self.symbol}] subscribed account stream")
                        async for msg in stream:
                            self._handle_account(msg.data)
                except Exception as e:
                    logger.error(f"[ExtendedWS] account stream error: {e}; reconnecting in 1s")
                    await asyncio.sleep(1)

        self._account_task = asyncio.create_task(subscribe_account())

    def _handle_orderbook(self, msg) -> None:
        try:
            bid = msg.bid[0] if msg.bid else None
            ask = msg.ask[0] if msg.ask else None
            if not bid or not ask:
                return

            new_ob = {
                "bidPrice": float(bid.price),
                "askPrice": float(ask.price),
                "bidSize": float(bid.qty),
                "askSize": float(ask.qty),
            }
            if self.dedup_ob and self.ob == new_ob:
                return
            self.ob = new_ob

            if self._on_ob_update_cb:
                self._on_ob_update_cb()

        except Exception as e:
            logger.error(f"[ExtendedWS] handle_orderbook error: {e}")

    def _handle_account(self, msg) -> None:
        try:
            # msg is AccountStreamDataModel
            # logger.info(msg)

            # Prefer maker order fills (FILLED LIMIT post_only)
            orders = getattr(msg, "orders", None)
            if orders:
                maker_qty = 0.0
                all_qty = 0.0
                last_price = None
                for o in orders:
                    try:
                        market = str(getattr(o, "market", "") or "")
                        filled = float(o.filled_qty or 0)
                        side = str(o.side or "").upper()
                        otype = str(o.type or "").upper()
                        status = str(getattr(o, "status", "") or "").upper()
                        if market and market.upper() != self.symbol.upper():
                            continue
                        if filled <= 0:
                            continue
                        avg_price = getattr(o, "average_price", None)
                        if avg_price:
                            last_price = float(avg_price)
                        signed = filled if side == "BUY" else -filled
                        # maker-only path for hedge (LIMIT + post_only + FILLED)
                        if otype == "LIMIT" and status == "FILLED" and getattr(o, "post_only", False):
                            maker_qty += signed
                            # avoid double counting in all_qty; we add maker fills only via maker_qty
                            continue
                        logger.info(
                            f"[ExtendedWS] order update type={o.type} status={o.status} side={o.side} "
                            f"qty={o.filled_qty} post_only={getattr(o,'post_only',None)} market={market}"
                        )
                        # all fills for inventory (any FILLED)
                        if status == "FILLED":
                            all_qty += signed
                        try:
                            px_val = float(getattr(o, "average_price", avg_price))
                            if px_val > 0:
                                self.last_fill_price = px_val
                        except Exception:
                            pass
                    except Exception as exc:
                        logger.debug(f"[ExtendedWS] skip order parse error: {exc}")
                        continue
                # update position first so downstream logs see refreshed entry/qty
                if all_qty != 0 and self._on_position_state_cb:
                    px = last_price
                    if px is None or px <= 0:
                        if getattr(self, "last_fill_price", 0) > 0:
                            px = self.last_fill_price
                        else:
                            px = self.ob["askPrice"] if all_qty < 0 else self.ob["bidPrice"]
                    if px and px > 0:
                        prev_qty = getattr(self, "position_qty", 0.0)
                        prev_entry = getattr(self, "position_entry", 0.0)
                        new_qty = prev_qty + all_qty
                        if new_qty == 0:
                            new_entry = 0.0
                        elif prev_qty == 0 or (prev_qty > 0 > new_qty) or (prev_qty < 0 < new_qty):
                            new_entry = px
                        else:
                            new_entry = (prev_qty * prev_entry + all_qty * px) / new_qty
                        self.last_fill_price = px
                        self.position_qty = new_qty
                        self.position_entry = new_entry
                        self._on_position_state_cb(new_qty, new_entry)
                if maker_qty and self._on_account_update_cb:
                    self._on_account_update_cb(maker_qty)
                if all_qty and self._on_inventory_cb:
                    self._on_inventory_cb(all_qty)
                return
        except Exception as e:
            logger.error(f"[ExtendedWS:{self.symbol}] handle_account error: {e}")

    # ---------- trading API ----------

    def _format_qty(self, qty: float) -> Decimal:
        # use size step if available
        step = Decimal(str(self.min_size_change or 1e-6))
        quantized = (Decimal(qty) // step) * step
        return quantized.normalize()

    def _format_price(self, price: float) -> Decimal:
        step = Decimal(str(self.min_price_change or 0.5))
        value = Decimal(str(price))
        # floor to nearest allowed step
        ticks = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return (ticks * step).quantize(step, rounding=ROUND_DOWN)

    async def load_initial_position(self) -> tuple[float, float]:
        """Load current position via REST client."""
        if not self._trading_client:
            await self._init_trading_client()
        try:
            resp = await self._trading_client.account.get_positions(market_names=[self.symbol])
            positions = resp.data if resp and resp.data else []
            if not positions:
                return 0.0, 0.0
            qty = 0.0
            total_value = 0.0
            for pos in positions:
                size = float(pos.size or 0)
                side = str(pos.side or "").upper()
                sign = 1 if side == "LONG" else -1
                qty += size * sign
                open_price = float(pos.open_price or 0)
                total_value += size * open_price
            avg_entry = total_value / abs(qty) if qty and total_value else 0.0
            self.position_qty = qty
            self.position_entry = avg_entry
            if self._on_position_state_cb:
                self._on_position_state_cb(qty, avg_entry)
            return qty, avg_entry
        except Exception as exc:
            logger.error(f"[ExtendedWS] load_initial_position error: {exc}")
            return 0.0, 0.0

    async def place_limit(self, side: Side, price: float, size: float) -> Optional[int]:
        if not self._trading_client:
            logger.error("Extended trading client not initialized")
            return None

        side_enum = ExtendedOrderSide.BUY if side == Side.LONG else ExtendedOrderSide.SELL
        px = self._format_price(price)
        qty = self._format_qty(size)

        try:
            logger.info(
                "[ExtendedWS] place_limit payload "
                f"market={self.symbol} side={side_enum} price={px} qty={qty} post_only=True"
            )
            resp = await self._trading_client.place_order(
                market_name=self.symbol,
                amount_of_synthetic=qty,
                price=px,
                side=side_enum,
                post_only=True,
                reduce_only=False,
            )
            order_id = resp.data.id if resp and resp.data else None
            logger.info(f"[ExtendedWS] limit order placed id={order_id}")
            return order_id
        except Exception as exc:
            logger.error(f"[ExtendedWS] place_limit failed: {exc}")
            return None

    async def cancel(self, order_id: int) -> None:
        if not self._trading_client or order_id is None:
            return
        try:
            await self._trading_client.orders.cancel_order(order_id)
            logger.info(f"[ExtendedWS] cancelled order {order_id}")
        except Exception as exc:
            logger.error(f"[ExtendedWS] cancel failed: {exc}")

    async def send_market(self, side: Side, size: float) -> Optional[dict]:
        if not self._trading_client:
            logger.error("Extended trading unavailable")
            return None
        if not self.ob["bidPrice"] or not self.ob["askPrice"]:
            logger.warning("Extended OB not ready for market order")
            return None

        if side == Side.LONG:
            ref_price = self.ob["askPrice"] * (1 + self.config["slippage"])
            side_enum = ExtendedOrderSide.BUY
        else:
            ref_price = self.ob["bidPrice"] * (1 - self.config["slippage"])
            side_enum = ExtendedOrderSide.SELL

        px = self._format_price(ref_price)
        qty = self._format_qty(size)

        prep_ts = time.perf_counter()
        logger.info(
            "[ExtendedWS] send_market payload "
            f"market={self.symbol} side={side_enum} price={px} qty={qty} post_only=False"
        )
        try:
            api_start = time.perf_counter()
            await self._trading_client.place_order(
                market_name=self.symbol,
                amount_of_synthetic=qty,
                price=px,
                side=side_enum,
                post_only=False,
                reduce_only=False,
            )
            api_ms = (time.perf_counter() - api_start) * 1000
            total_ms = (time.perf_counter() - prep_ts) * 1000
            logger.info(f"[ExtendedWS] send_market done api_ms={api_ms:.1f} total_ms={total_ms:.1f}")
            return {"filled_qty": float(qty)}
        except Exception as exc:
            logger.error(f"[ExtendedWS] market order failed: {exc}")
            return None
