"""
Manual tester for lighten send_market latency on FARTCOIN.

This script:
  * loads the LIGHTER_* env vars
  * looks up the FARTCOIN market_id
  * connects to the orderbook and account/trade websockets
  * sends two LONG market orders (one deep, one aggressive) using the WS path
  * prints the send latencies that each request experienced

Run the file with the same interpreter/env that has trading credentials in place.

PYTHONPATH=. .venv/bin/python -m bot.tools.latency.new_tester_L
"""

import asyncio
import logging
import time
from typing import Optional

from bot.common.enums import Side
from bot.tools.latency.latency_tester import _load_env
from bot.venues.helper_lighter import LighterWS

LOGGER = logging.getLogger("latency.new_tester_L")


async def _wait_for_streams_ready(ws: LighterWS, timeout: float = 40.0) -> bool:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if ws._got_first_ob and (ws._got_first_trades or ws._got_first_positions):
            LOGGER.info("orderbook and account/order streams are primed")
            return True
        await asyncio.sleep(0.2)
    LOGGER.warning("timed out waiting for OB/account streams to prime")
    return False


async def _await_bid_price(ws: LighterWS, timeout: float = 20.0) -> Optional[float]:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        bid_price = ws.ob.get("bidPrice") or 0.0
        if bid_price > 0.0:
            return bid_price
        await asyncio.sleep(0.2)
    return None


async def _send_market_and_report(ws: LighterWS, price: float, size: float, label: str) -> dict:
    send_ts = time.perf_counter()
    result = await ws.send_market(Side.LONG, size, price)
    latency_ms = (time.perf_counter() - send_ts) * 1000
    print(f"{label} | price={price:.8f} | size={size:.8f} | send_latency_ms={latency_ms:.1f} | result={result}")
    return {"label": label, "latency_ms": latency_ms, "result": result}


async def main() -> None:
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")

    lighter = LighterWS("FARTCOIN")

    await lighter._init_market_id()
    print(f"Found FARTCOIN market_id={lighter.market_id}")

    try:
        await lighter._init_trading_client()
    except RuntimeError as err:
        LOGGER.error("Trading client init failed: %s", err)
        raise
    lighter._start_ws_loop()
    lighter._start_account_loop()

    ready = await _wait_for_streams_ready(lighter)
    if not ready:
        LOGGER.warning("Streams never reported readiness; proceeding anyway")

    await lighter._ensure_trade_ws()

    first_bid = await _await_bid_price(lighter)
    if not first_bid:
        raise RuntimeError("Failed to observe a non-zero bid price on FARTCOIN")

    base_size = 50
    deep_price = first_bid * 0.8  # 50% below the current best bid
    await _send_market_and_report(lighter, deep_price, base_size, "deep_long")

    await asyncio.sleep(0.6)

    second_bid = await _await_bid_price(lighter)
    if not second_bid:
        raise RuntimeError("Lost the FARTCOIN bid price before the second order")

    aggressive_price = second_bid * 1.04  # 4% above the refreshed bid
    # await _send_market_and_report(lighter, aggressive_price, base_size, "aggressive_long")

    tasks = [t for t in (lighter._ws_task, lighter._account_task) if t]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    if lighter._ws_trade and not lighter._ws_trade.closed:
        await lighter._ws_trade.close()
    if lighter._ws_session and not lighter._ws_session.closed:
        await lighter._ws_session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted")
