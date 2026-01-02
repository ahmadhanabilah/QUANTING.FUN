# venues/extended_ws.py
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Optional
import time

import aiohttp
from bot.common.enums import Side
from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.orders import OrderSide as ExtendedOrderSide, TimeInForce
from x10.perpetual.stream_client import PerpetualStreamClient
from x10.perpetual.trading_client import PerpetualTradingClient

logger = logging.getLogger("EXT")
logger.setLevel(logging.INFO)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        self._has_account_position: bool = False
        self._got_first_ob: bool = False
        self._got_first_acc: bool = False
        self._need_first_account_pos: bool = True
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
                    logger.info(f"[SUBSCRIBED {self.symbol}]")
                    async with self._ws_client.subscribe_to_orderbooks(self.symbol, depth=1) as stream:
                        async for msg in stream:
                            self._handle_orderbook(msg)
                except Exception as e:
                    logger.debug(f"OB stream error: {e}; reconnecting in 1s")
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
                    logger.info(f"[SUBSCRIBED ACC {self.symbol}]")
                    async with self._ws_client.subscribe_to_account_updates(self.config["api_key"]) as stream:
                        async for msg in stream:
                            self._handle_account(msg)
                except Exception as e:
                    logger.debug(f"account stream error: {e}; reconnecting in 1s")
                    await asyncio.sleep(1)

        self._account_task = asyncio.create_task(subscribe_account())

    def _handle_orderbook(self, msg) -> None:
        try:
            payload = getattr(msg, "data", msg)
            if payload is None:
                return
            bid = payload.bid[0] if getattr(payload, "bid", None) else None
            ask = payload.ask[0] if getattr(payload, "ask", None) else None
            if not bid or not ask:
                return

            new_ob = {
                "bidPrice": float(bid.price),
                "askPrice": float(ask.price),
                "bidSize": float(bid.qty),
                "askSize": float(ask.qty),
                "timestamp": time.time(),
            }
            if self.dedup_ob and self.ob == new_ob:
                return
            if not self._got_first_ob:
                self._got_first_ob = True
                logger.info("[GOT THE FIRST WS NOTIF - OB]")
            self.ob = new_ob

            if self._on_ob_update_cb:
                self._on_ob_update_cb()

        except Exception as e:
                    logger.error(f"handle_orderbook error: {e}")

    def _handle_account(self, msg) -> None:
        try:
            if msg is None:
                return

            # x10 messages can arrive either as a wrapper with .type/.data or as the data payload itself.
            payload = getattr(msg, "data", msg)
            msg_type = str(getattr(msg, "type", "") or "").upper()

            # Pull fields from both object-style and dict-style payloads.
            positions = getattr(payload, "positions", None)
            orders = getattr(payload, "orders", None)
            if positions is None and isinstance(payload, dict):
                positions = payload.get("positions")
            if orders is None and isinstance(payload, dict):
                orders = payload.get("orders")

            orders_field_seen = (
                orders is not None
                or (isinstance(payload, dict) and "orders" in payload)
                or msg_type == "ORDER"
            )
            # Only treat a message as a positions update if the field is present (or type explicitly says so).
            positions_field_seen = (
                positions is not None
                or (isinstance(payload, dict) and "positions" in payload)
                or msg_type == "POSITION"
            )
            if (positions_field_seen or orders_field_seen) and not self._got_first_acc:
                self._got_first_acc = True
                logger.info("[GOT THE FIRST WS NOTIF - ACCOUNT]")
            # print(msg)
            if positions_field_seen:
                self._handle_positions(positions)

            # Orders-only messages should not flip the position-ready flag.
            if orders_field_seen and orders:
                self._handle_orders(orders)

        except Exception as e:
            logger.error(f"[ExtendedWS:{self.symbol}] handle_account error: {e}")

    def _handle_positions(self, positions) -> None:
        """Parse account position payloads; only invoked when a positions field is present."""
        try:
            if positions is None:
                return
            def _get(obj, key, default=None):
                if obj is None:
                    return default
                if isinstance(obj, dict):
                    return obj.get(key, default)
                try:
                    return getattr(obj, key)
                except Exception:
                    return default

            iterable = positions.values() if isinstance(positions, dict) else positions
            if iterable is None:
                iterable = []
            if not isinstance(iterable, (list, tuple)):
                iterable = [iterable]

            found_pos = False
            saw_any_position = False
            for pos in iterable:
                if pos is None:
                    continue
                saw_any_position = True
                market = str(_get(pos, "market") or _get(pos, "symbol") or "")
                if market.upper() != self.symbol.upper():
                    continue
                # print(f"[POSITION NOTIF EXT] {pos}")
                size_val = float(_get(pos, "size") or _get(pos, "position") or 0)
                side_val = str(_get(pos, "side") or "").upper()
                status_val = str(_get(pos, "status") or "").upper()
                sign = -1 if side_val == "SHORT" else 1
                qty = size_val * sign
                if status_val == "CLOSED":
                    qty = 0.0
                entry_val = (
                    _get(pos, "openPrice")
                    or _get(pos, "open_price")
                    or _get(pos, "avg_entry_price")
                )
                try:
                    entry = float(entry_val) if entry_val is not None else 0.0
                except Exception:
                    entry = 0.0
                if qty == 0 or entry <= 0:
                    entry = 0.0
                self.position_qty = qty
                self.position_entry = entry
                self._has_account_position = True
                self._need_first_account_pos = False
                if self._on_position_state_cb:
                    self._on_position_state_cb(qty, entry)
                found_pos = True

            # If the positions field was present but empty/missing our symbol, treat as flat.
            if not found_pos:
                if saw_any_position:
                    # ignore unrelated market positions; keep current state
                    return
                if self.position_qty != 0 or self._need_first_account_pos:
                    self.position_qty = 0.0
                    self.position_entry = 0.0
                    if self._on_position_state_cb:
                        self._on_position_state_cb(0.0, 0.0)
                self._has_account_position = True
                self._need_first_account_pos = False

        except Exception as e:
            logger.error(f"[ExtendedWS:{self.symbol}] handle_positions error: {e}")

    def _handle_orders(self, orders) -> None:
        """Process fills from order payloads without touching position readiness flags."""
        try:
            all_qty = 0.0
            last_price = None
            iterable = orders.values() if isinstance(orders, dict) else orders
            if iterable is None:
                return
            if not isinstance(iterable, (list, tuple)):
                iterable = [iterable]
            def _get_val(obj, key, default=None):
                if obj is None:
                    return default
                if isinstance(obj, dict):
                    return obj.get(key, default)
                try:
                    return getattr(obj, key)
                except Exception:
                    return default
            for o in iterable:
                if o is None:
                    continue
                try:
                    market = str(_get_val(o, "market", "") or "")
                    filled = float(_get_val(o, "filled_qty", 0) or 0)
                    side = str(_get_val(o, "side", "") or "").upper()
                    status = str(_get_val(o, "status", "") or "").upper()
                    if market and market.upper() != self.symbol.upper():
                        continue
                    if filled <= 0:
                        continue

                    # print(f"[FILL NOTIF EXT] {o}")

                    avg_price = _get_val(o, "average_price", None)
                    if avg_price:
                        last_price = float(avg_price)

                    # all fills for inventory (any FILLED)
                    if status == "FILLED":
                        signed = filled if side == "BUY" else -filled
                        all_qty += signed
                    try:
                        px_val = float(_get_val(o, "average_price", avg_price) or 0)
                        if px_val > 0:
                            self.last_fill_price = px_val
                    except Exception:
                        pass
                except Exception as exc:
                    logger.debug(f"skip order parse error: {exc}")
                    continue

            if all_qty != 0:
                # keep last fill price for logging; entry comes from POSITION feed
                px = last_price
                if px is None or px <= 0:
                    if getattr(self, "last_fill_price", 0) > 0:
                        px = self.last_fill_price
                    else:
                        px = self.ob["askPrice"] if all_qty < 0 else self.ob["bidPrice"]
                if px and px > 0:
                    self.last_fill_price = px
                if self._on_inventory_cb:
                    self._on_inventory_cb(all_qty)
        except Exception as e:
            logger.error(f"[ExtendedWS:{self.symbol}] handle_orders error: {e}")
            print(orders)

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
                self.position_qty = 0.0
                self.position_entry = 0.0
                self._has_account_position = True
                self._need_first_account_pos = False
                if self._on_position_state_cb:
                    self._on_position_state_cb(0.0, 0.0)
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
            self._has_account_position = True
            self._need_first_account_pos = False
            if self._on_position_state_cb:
                self._on_position_state_cb(qty, avg_entry)
            return qty, avg_entry
        except Exception as exc:
            logger.error(f"load_initial_position error: {exc}")
            return 0.0, 0.0

    async def place_limit(self, side: Side, price: float, size: float) -> Optional[int]:
        if not self._trading_client:
            logger.error("Extended trading client not initialized")
            return None

        side_enum = ExtendedOrderSide.BUY if side == Side.LONG else ExtendedOrderSide.SELL
        px = self._format_price(price)
        qty = self._format_qty(size)

        try:
            logger.info("place_limit")
            expire_time = _utc_now() + timedelta(hours=24)
            resp = await self._trading_client.place_order(
                market_name=self.symbol,
                amount_of_synthetic=qty,
                price=px,
                side=side_enum,
                post_only=True,
                reduce_only=False,
                expire_time=expire_time,
            )
            order_id = resp.data.id if resp and resp.data else None
            logger.info("limit order placed")
            return order_id
        except Exception as exc:
            logger.error(f"place_limit failed: {exc}")
            return None

    async def cancel(self, order_id: int) -> None:
        if not self._trading_client or order_id is None:
            return
        try:
            await self._trading_client.orders.cancel_order(order_id)
            logger.info("cancel order")
        except Exception as exc:
            logger.error(f"cancel failed: {exc}")

    async def send_market(self, side: Side, size: float, price: float, is_heartbeat: bool = False) -> Optional[dict]:
        if not self._trading_client:
            logger.error("Extended trading unavailable")
            return None
        if not self.ob["bidPrice"] or not self.ob["askPrice"]:
            logger.warning("Extended OB not ready for market order")
            return None

        side_enum = ExtendedOrderSide.BUY if side == Side.LONG else ExtendedOrderSide.SELL

        px = self._format_price(price)
        qty = self._format_qty(size)

        last_err = None
        max_attempts = 1 if is_heartbeat else 2
        http_logger = logging.getLogger("x10.utils.http") if is_heartbeat else None
        old_level = http_logger.level if http_logger else None
        for attempt in range(max_attempts):
            try:
                if not is_heartbeat:
                    logger.info(f"send_market")
                wall_start = time.time()
                api_start = time.perf_counter()
                if http_logger and is_heartbeat:
                    http_logger.setLevel(logging.CRITICAL)
                try:
                    expire_time = _utc_now() + timedelta(hours=24)
                    resp = await self._trading_client.place_order(
                        market_name=self.symbol,
                        amount_of_synthetic=qty,
                        price=px,
                        side=side_enum,
                        post_only=False,
                        reduce_only=False,
                        expire_time=expire_time,
                    )
                finally:
                    if http_logger and is_heartbeat and old_level is not None:
                        http_logger.setLevel(old_level)
                api_ms = (time.perf_counter() - api_start) * 1000
                wall_end = time.time()
                if not is_heartbeat:
                    logger.info(f"send_market done start_ts={wall_start:.3f} end_ts={wall_end:.3f} api_ms={api_ms:.1f}")
                return {
                    "payload": {"price": px, "qty": qty, "side": side_enum.name},
                    "resp": str(resp),
                }
            except Exception as exc:
                last_err = exc
                if not is_heartbeat:
                    logger.error(f"market order failed (attempt {attempt+1}): {exc}")
                await asyncio.sleep(0.05)
        return {"error": str(last_err), "payload": {"price": px, "qty": qty, "side": side_enum.name}}
