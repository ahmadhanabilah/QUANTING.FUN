# venues/lighter_ws.py
import asyncio
import json
import logging
import os
import time
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Optional

import aiohttp
import lighter
from lighter import WsClient

from bot.common.enums import Side, Venue

logger = logging.getLogger("LIG")
logger.setLevel(logging.ERROR)
LIGHTER_BASE_URL = "https://mainnet.zklighter.elliot.ai"


class LighterWS:
    """
    Minimal Lighter venue wrapper for MAKER bot:
    - maintains self.ob["bidPrice"], self.ob["askPrice"]
    - calls a callback on every OB update
    - no trading, no positions (for now)
    """

    def __init__(self, symbol: str, read_only: bool = False):
        self.symbol = symbol
        self.read_only = read_only
        self.market_id: Optional[int] = None
        self.size_decimals: Optional[int] = None
        self.price_decimals: Optional[int] = None
        self.min_size: Optional[float] = None
        self.min_value: Optional[float] = None
        self._last_client_order_index: int = 0
        self.ws_client: Optional[WsClient] = None
        self.trading_client: Optional[lighter.SignerClient] = None
        self._ws_trade: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_trade_task: Optional[asyncio.Task] = None
        self.ob = {
            "bidPrice": 0.0,
            "askPrice": 0.0,
            "bidSize": 0.0,
            "askSize": 0.0,
        }
        self.position_qty: float = 0.0
        self.position_entry: float = 0.0
        self.last_fill_price: float = 0.0
        self.account_callback: Optional[Callable[[float], None]] = None
        self.inventory_callback: Optional[Callable[[float], None]] = None
        self.config = {
            "base_url": LIGHTER_BASE_URL,
            "private_key": os.getenv("LIGHTER_API_PRIVATE_KEY"),
            "account_index": os.getenv("LIGHTER_ACCOUNT_INDEX"),
            "api_key_index": os.getenv("LIGHTER_API_KEY_INDEX"),
            "slippage": float(os.getenv("LIGHTER_SLIPPAGE", "0.001")),
            "account_id": os.getenv("LIGHTER_ACCOUNT_INDEX"),
        }
        self._on_ob_update_cb: Optional[Callable[[], None]] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._account_task: Optional[asyncio.Task] = None
        self.auth_token: Optional[str] = None
        self._ws_session: Optional[aiohttp.ClientSession] = None

    # ---------- public API ----------

    def set_ob_callback(self, cb: Callable[[], None]) -> None:
        """Register callback to run on every OB update."""
        self._on_ob_update_cb = cb

    def set_account_callback(self, cb: Callable[[float], None]) -> None:
        """Register callback to run on every account update (passes position qty)."""
        self.account_callback = cb
    def set_inventory_callback(self, cb: Callable[[float], None]) -> None:
        """Register callback for all fills (maker+taker) deltas."""
        self.inventory_callback = cb
    def set_position_state_callback(self, cb: Callable[[float, float], None]) -> None:
        """Callback with absolute position and avg entry."""
        self.position_state_cb = cb

    async def start(self) -> None:
        """Fetch market metadata then start WS loop."""
        await self._init_market_id()
        if not self.read_only:
            await self._init_trading_client()
        self._start_ws_loop()
        if not self.read_only:
            self._start_account_loop()
        tasks = []
        if self._ws_task:
            tasks.append(self._ws_task)
        if self._account_task:
            tasks.append(self._account_task)
        if tasks:
            await asyncio.gather(*tasks)

    # ---------- internal ----------

    async def _init_market_id(self) -> None:
        """Fetch market_id from Lighter REST orderBookDetails."""
        base_url = "https://mainnet.zklighter.elliot.ai"
        url = f"{base_url}/api/v1/orderBookDetails"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Lighter initPair HTTP {resp.status}")
                data = await resp.json()

        details = data.get("order_book_details", [])
        match = next(
            (d for d in details if d["symbol"].upper() == self.symbol.upper()),
            None,
        )
        if not match:
            raise RuntimeError(f"Lighter symbol {self.symbol} not found in orderBookDetails")

        self.market_id = int(match["market_id"])
        self.size_decimals = int(match.get("size_decimals", 0))
        self.price_decimals = int(match.get("price_decimals", 0))
        self.min_size = float(match.get("min_base_amount", 0.0))
        self.min_value = float(match.get("min_quote_amount", 0.0))

    async def load_initial_position(self) -> tuple[float, float]:
        """Load current position (prefers trading client; falls back to REST)."""
        # 1) use trading client if already initialized
        try:
            if self.trading_client:
                resp = await self.trading_client.account.get_positions(market_names=[self.symbol])
                positions = resp.data if resp and resp.data else []
                if positions:
                    size = float(positions[0].size or 0)
                    sign = 1 if str(positions[0].side).upper() == "LONG" else -1
                    qty = size * sign
                    entry = float(positions[0].open_price or 0)
                    self.position_qty = qty
                    self.position_entry = entry
                    if getattr(self, "position_state_cb", None):
                        self.position_state_cb(qty, entry)
                    return qty, entry
        except Exception:
            pass

        # 2) REST fallback: try account_id then account_index
        base_url = self.config["base_url"]
        account_id = self.config.get("account_id")
        account_index = self.config.get("account_index")
        urls = []
        if account_id:
            urls.append(f"{base_url}/api/v1/account?by=id&value={account_id}")
        if account_index:
            urls.append(f"{base_url}/api/v1/account?by=index&value={account_index}")
        try:
            async with aiohttp.ClientSession() as session:
                for url in urls:
                    async with session.get(url, headers={"accept": "application/json"}) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json(content_type=None)
                        accounts = data.get("accounts", [])
                        if not accounts:
                            continue
                        positions = accounts[0].get("positions", [])
                        pos = next((p for p in positions if p.get("symbol", "").upper() == self.symbol.upper()), None)
                        if not pos:
                            continue
                        qty = float(pos.get("position", "0") or 0) * int(pos.get("sign", 1))
                        entry = float(pos.get("avg_entry_price", 0) or 0)
                        self.position_qty = qty
                        self.position_entry = entry
                        if getattr(self, "position_state_cb", None):
                            self.position_state_cb(qty, entry)
                        return qty, entry
        except Exception as exc:
            logger.error(f"load_initial_position error: {exc}")
        return 0.0, 0.0

    async def _init_trading_client(self) -> None:
        if self.trading_client:
            return
        if not all([self.config["private_key"], self.config["account_index"], self.config["api_key_index"]]):
            raise RuntimeError("Missing Lighter trading credentials in env (LIGHTER_API_* vars)")
        api_idx = int(self.config["api_key_index"])
        api_keys = {api_idx: self.config["private_key"]}
        self.trading_client = lighter.SignerClient(
            url=self.config["base_url"],
            account_index=int(self.config["account_index"]),
            api_private_keys=api_keys,
        )

        if not self.config.get("account_id"):
            self.config["account_id"] = self.config["account_index"]
        self._last_client_order_index = int(time.time() * 1000)
        await self._refresh_auth_token(force=True)
        await self._ensure_trade_ws()

    async def _ensure_trade_ws(self) -> None:
        """Ensure trade websocket is connected."""
        if self._ws_trade and not self._ws_trade.closed:
            return
        # close stale
        if self._ws_trade:
            try:
                await self._ws_trade.close()
            except Exception:
                pass
        url = self.config["base_url"].replace("https", "wss") + "/stream"
        session = self._ws_session or aiohttp.ClientSession()
        self._ws_session = session
        self._ws_trade = await session.ws_connect(url, heartbeat=30)

    async def _refresh_auth_token(self, force: bool = False) -> None:
        now = time.time()
        if not force and self.auth_token and now < getattr(self, "auth_expiry", 0) - 60:
            return
        auth, err = self.trading_client.create_auth_token_with_expiry(
            lighter.SignerClient.DEFAULT_10_MIN_AUTH_EXPIRY
        )
        if err:
            raise RuntimeError(f"Failed to create auth token: {err}")
        token = getattr(auth, "auth_token", None) or str(auth)
        self.auth_token = token
        self.auth_expiry = now + 9 * 60

    def _start_ws_loop(self) -> None:
        if self._ws_task and not self._ws_task.done():
            return

        async def run_ws():
            while True:
                try:
                    self.ws_client = WsClient(
                        order_book_ids=[self.market_id],
                        on_order_book_update=self._handle_orderbook,
                    )
                    await self.ws_client.run_async()
                except Exception as e:
                    logger.error(f"error: {e}; reconnecting in 1s")
                    self.ob = {"bidPrice": 0.0, "askPrice": 0.0, "bidSize": 0.0, "askSize": 0.0}
                    await asyncio.sleep(1)

        self._ws_task = asyncio.create_task(run_ws())

    def _handle_orderbook(self, market_id: int, order_book: dict) -> None:
        try:
            if isinstance(order_book, dict) and order_book.get("type") == "ping":
                return

            bids = order_book.get("bids") or []
            asks = order_book.get("asks") or []

            if not bids or not asks:
                return

            # sort and pick best
            bids.sort(key=lambda x: float(x["price"]), reverse=True)
            asks.sort(key=lambda x: float(x["price"]))

            bid = next((b for b in bids if float(b["size"]) > 0), None)
            ask = next((a for a in asks if float(a["size"]) > 0), None)
            if not bid or not ask:
                return

            new_ob = {
                "bidPrice": float(bid["price"]),
                "askPrice": float(ask["price"]),
                "bidSize": float(bid["size"]),
                "askSize": float(ask["size"]),
            }

            if getattr(self, "dedup_ob", False):
                # skip if unchanged
                if self.ob == new_ob:
                    return
            self.ob = new_ob

            if self._on_ob_update_cb:
                self._on_ob_update_cb()

        except Exception as e:
            logger.error(f"handle_orderbook error: {e}")

    def _handle_account_update(self, payload) -> None:
        """
        payload structure for account_orders:
        {
            "account": int,
            "channel": "account_orders:{market_id}",
            "nonce": int,
            "orders": {
                "{market_id}": [Order ...]
            },
            "type": "update/account_orders"
        }
        """
        try:
            trades_by_market = payload.get("trades") if isinstance(payload, dict) else None
            if not trades_by_market:
                return
            trades = trades_by_market.get(str(self.market_id)) or trades_by_market.get(self.market_id)
            if not trades:
                return
            acct_id = int(self.config.get("account_id") or 0)
            maker_qty = 0.0
            total_qty = 0.0
            last_price = None
            delta_value = 0.0  # track signed notionals to compute weighted price safely
            for t in trades:
                try:
                    if int(t.get("market_id", 0)) != int(self.market_id):
                        continue
                    # presence checks
                    account_is_ask = int(t.get("ask_account_id", -1)) == acct_id
                    account_is_bid = int(t.get("bid_account_id", -1)) == acct_id
                    if not (account_is_ask or account_is_bid):
                        continue
                    maker_is_ask = bool(t.get("is_maker_ask"))
                    is_maker = (
                        (maker_is_ask and account_is_ask) or
                        (not maker_is_ask and account_is_bid)
                    )
                    size = float(t.get("size") or 0)
                    if size <= 0:
                        continue
                    try:
                        px_val = float(t.get("price") or t.get("ask_price") or t.get("bid_price") or 0)
                        if px_val > 0:
                            last_price = px_val
                    except Exception:
                        pass
                    if is_maker:
                        signed = -size if maker_is_ask else size
                        maker_qty += signed
                    # total fills regardless of maker/taker
                    if account_is_ask:
                        total_qty -= size
                        delta_value -= size * (last_price or 0)
                    elif account_is_bid:
                        total_qty += size
                        delta_value += size * (last_price or 0)
                except Exception as exc:
                    logger.debug(f"trade parse error: {exc}")
                    continue
            # update running avg entry with last fill price first so downstream logs see it
            if total_qty != 0:
                prev_qty = getattr(self, "position_qty", 0.0)
                prev_entry = getattr(self, "position_entry", 0.0)
                # if last_price missing, derive from delta_value / total_qty when possible
                px = last_price
                if (px is None or px <= 0) and total_qty != 0 and delta_value != 0:
                    px = delta_value / total_qty
                if px is None or px <= 0:
                    if getattr(self, "last_fill_price", 0) > 0:
                        px = self.last_fill_price
                    else:
                        px = self.ob["askPrice"] if total_qty < 0 else self.ob["bidPrice"]
                if px and px > 0:
                    self.last_fill_price = px
                    new_qty = prev_qty + total_qty
                    if new_qty == 0:
                        new_entry = 0.0
                    elif prev_qty == 0 or (prev_qty > 0 > new_qty) or (prev_qty < 0 < new_qty):
                        new_entry = px
                    else:
                        new_entry = (prev_qty * prev_entry + total_qty * px) / new_qty
                    self.position_qty = new_qty
                    self.position_entry = new_entry
                    if getattr(self, "position_state_cb", None):
                        self.position_state_cb(new_qty, new_entry)
            if maker_qty != 0 and self.account_callback:
                self.account_callback(maker_qty)
            if total_qty != 0 and self.inventory_callback:
                self.inventory_callback(total_qty)
        except Exception as exc:
            logger.error(f"handle_account_update error: {exc}")

    # ---------- trading helpers ----------

    def _fmt_decimal_int(self, value: float, decimals: int) -> int:
        quant = Decimal("1").scaleb(-decimals)
        val = Decimal(value).quantize(quant, rounding=ROUND_DOWN)
        scaled = val * (Decimal(10) ** decimals)
        return int(scaled)

    def _next_client_order_index(self) -> int:
        now = int(time.time() * 1000)
        if now <= self._last_client_order_index:
            now = self._last_client_order_index + 1
        self._last_client_order_index = now
        return now

    def _start_account_loop(self) -> None:
        if self._account_task and not self._account_task.done():
            return
        if not self.auth_token:
            return

        async def subscribe_account_orders():
            url = "wss://mainnet.zklighter.elliot.ai/stream"
            sub_msg = {
                "type": "subscribe",
                "channel": f"account_all_trades/{self.config['account_id']}",
                "auth": self.auth_token,
            }
            while True:
                try:
                    if not self._ws_session:
                        self._ws_session = aiohttp.ClientSession()
                    async with self._ws_session.ws_connect(url, heartbeat=30) as ws:
                        await ws.send_json(sub_msg)
                        logger.info("Subscribed [OB, Acc]")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.json()
                                if isinstance(data, dict) and data.get("type") in ("ping", "pong"):
                                    # respond to ping
                                    if data.get("type") == "ping":
                                        await ws.send_json({"type": "pong"})
                                    continue
                                if isinstance(data, dict) and data.get("error", {}).get("code") == 20013:
                                    logger.warning("auth expired; refreshing token")
                                    await self._refresh_auth_token(force=True)
                                    break  # reconnect with new token
                                self._handle_account_update(data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                raise RuntimeError(f"Lighter account WS error: {ws.exception()}")
                    logger.warning("account WS closed; reconnecting in 1s")
                    await asyncio.sleep(1)
                except Exception as exc:
                    logger.error(f"account WS error: {exc}; reconnecting in 1s")
                    await asyncio.sleep(1)

        self._account_task = asyncio.create_task(subscribe_account_orders())

    async def place_limit(self, side: Side, price: float, size: float) -> Optional[int]:
        if not self.trading_client or self.market_id is None:
            logger.error("Lighter trading client not ready")
            return None

        is_ask = side == Side.SHORT
        client_order_index = self._next_client_order_index()
        base_amount = self._fmt_decimal_int(size, self.size_decimals or 0)
        px = self._fmt_decimal_int(price, self.price_decimals or 0)

        try:
            start_time = time.perf_counter()
            logger.info(
                f"place_limit payload market_id={self.market_id} "
                f"coi={client_order_index} price={px} size={base_amount} is_ask={is_ask} tif=GTT"
            )
            tx, tx_hash, err = await self.trading_client.create_order(
                market_index=self.market_id,
                client_order_index=client_order_index,
                base_amount=base_amount,
                price=px,
                is_ask=is_ask,
                order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_POST_ONLY,
                trigger_price=0,
            )
            if err:
                raise RuntimeError(err)
            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"limit order {client_order_index} placed ({latency_ms:.2f} ms)")
            return client_order_index
        except Exception as exc:
            logger.error(f"place_limit failed: {exc}")
            return None

    async def cancel(self, order_id: int) -> None:
        if not self.trading_client or self.market_id is None:
            return
        try:
            await self._ensure_trade_ws()
            tx_type, tx_info, err = self.trading_client.sign_cancel_order(
                market_index=self.market_id,
                order_index=order_id,
            )
            if err:
                raise RuntimeError(err)
            await self._send_tx(tx_type, tx_info)
            logger.info(f"cancelled order {order_id} via WS")
        except Exception as exc:
            logger.error(f"cancel failed: {exc}")

    async def send_market(self, side: Side, size: float, price: float | None = None, use_ws: bool = True) -> Optional[dict]:
        """Market order via WS (default) or REST.

        Args:
            side: LONG/SHORT
            size: base size
            price: optional aggressive price (precomputed by caller). If not provided, will derive from OB + slippage.
            use_ws: use websocket signing path (default) or REST fallback
        """
        if use_ws:
            return await self._send_market_ws(side, size, price)
        return await self._send_market_rest(side, size, price)

    async def _send_market_ws(self, side: Side, size: float, price: float | None = None) -> Optional[dict]:
        if not self.trading_client or self.market_id is None:
            logger.error("Lighter trading client not initialized")
            return None
        if not self.ob["bidPrice"] or not self.ob["askPrice"]:
            logger.warning("Lighter OB not ready for market order")
            return None

        if price is None:
            if side == Side.LONG:
                ref_price = self.ob["askPrice"] * (1 + self.config["slippage"])
                is_ask = False
            else:
                ref_price = self.ob["bidPrice"] * (1 - self.config["slippage"])
                is_ask = True
        else:
            ref_price = price
            is_ask = side != Side.LONG

        px = self._fmt_decimal_int(ref_price, self.price_decimals or 0)
        base_amount = self._fmt_decimal_int(size, self.size_decimals or 0)
        client_order_index = self._next_client_order_index()

        prep_ts = time.perf_counter()
        logger.info(
                f"send_market payload "
                f"market_id={self.market_id} price={px} size={base_amount} is_ask={is_ask}"
            )
        try:
            api_start = time.perf_counter()
            await self._ensure_trade_ws()
            tx_type, tx_info, tx_hash, err = self.trading_client.sign_create_order(
                market_index=self.market_id,
                client_order_index=client_order_index,
                base_amount=base_amount,
                price=px,
                is_ask=is_ask,
                order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                reduce_only=False,
                trigger_price=0,
            )
            if err:
                raise RuntimeError(err)
            await self._send_tx(tx_type, tx_info)
            api_ms = (time.perf_counter() - api_start) * 1000
            total_ms = (time.perf_counter() - prep_ts) * 1000
            logger.info(
                f"send_market done order_id={client_order_index} api_ms={api_ms:.1f} total_ms={total_ms:.1f} "
                f"price={px} size={base_amount} is_ask={is_ask}"
            )
            return {
                "payload": {"price": px, "size": base_amount, "is_ask": is_ask},
                "resp": tx_info,
            }
        except Exception as exc:
            logger.error(f"market order failed: {exc}")
            return {"error": str(exc), "payload": {"price": px, "size": base_amount, "is_ask": is_ask}}

    async def _send_market_rest(self, side: Side, size: float, price: float | None = None) -> Optional[dict]:
        if not self.trading_client or self.market_id is None:
            logger.error("Lighter trading client not initialized")
            return None
        if not self.ob["bidPrice"] or not self.ob["askPrice"]:
            logger.warning("Lighter OB not ready for market order")
            return None

        if price is None:
            if side == Side.LONG:
                ref_price = self.ob["askPrice"] * (1 + self.config["slippage"])
                is_ask = False
            else:
                ref_price = self.ob["bidPrice"] * (1 - self.config["slippage"])
                is_ask = True
        else:
            ref_price = price
            is_ask = side != Side.LONG

        px = self._fmt_decimal_int(ref_price, self.price_decimals or 0)
        base_amount = self._fmt_decimal_int(size, self.size_decimals or 0)
        client_order_index = self._next_client_order_index()

        try:
            prep_ts = time.perf_counter()
            logger.info(
                f"[LighterREST] send_market payload market_id={self.market_id} price={px} size={base_amount} is_ask={is_ask}"
            )
            api_start = time.perf_counter()
            tx, tx_hash, err = await self.trading_client.create_order(
                market_index=self.market_id,
                client_order_index=client_order_index,
                base_amount=base_amount,
                price=px,
                is_ask=is_ask,
                order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                trigger_price=0,
            )
            if err:
                raise RuntimeError(err)
            api_ms = (time.perf_counter() - api_start) * 1000
            total_ms = (time.perf_counter() - prep_ts) * 1000
            logger.info(
                f"[LighterREST] send_market done order_id={client_order_index} tx={tx_hash} "
                f"api_ms={api_ms:.1f} total_ms={total_ms:.1f} market_id={self.market_id} price={px} size={base_amount} is_ask={is_ask}"
            )
            return {
                "payload": {"price": px, "size": base_amount, "is_ask": is_ask},
                "resp": tx_hash,
            }
        except Exception as exc:
            logger.error(f"REST market order failed: {exc}")
            return {"error": str(exc), "payload": {"price": px, "size": base_amount, "is_ask": is_ask}}

    async def _send_tx(self, tx_type, tx_info) -> None:
        """Send a signed tx over trading websocket."""
        payload = {
            "type": "jsonapi/sendtx",
            "data": {
                "id": f"arb_bot_{self._next_client_order_index()}",
                "tx_type": tx_type,
                "tx_info": json.loads(tx_info),
            },
        }
        await self._ws_trade.send_json(payload)
        # Best effort read response
        try:
            msg = await self._ws_trade.receive(timeout=1)
        except Exception:
            pass
