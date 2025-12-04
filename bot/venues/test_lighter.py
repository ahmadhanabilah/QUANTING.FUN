"""
Ad-hoc harness to sanity check LighterWS setup.

Usage (from repo root):
  PYTHONPATH=/root/arb_bot python3 bot/venues/test_lighter.py

Env overrides:
  SYMBOL=BTC           # which symbol to test
  READ_ONLY=true       # set to false to exercise trading client/ws init (no orders sent)
  TEST_TRADE=false     # if true and READ_ONLY=false, will connect trading WS (no orders)

Requires LIGHTER_API_* creds in env for trading client tests.
"""

import asyncio
import logging
import os
import sys

from helper_lighter import LighterWS


async def main():
    symbol = os.getenv("SYMBOL", "BTC")
    read_only = str(os.getenv("READ_ONLY", "true")).lower() == "true"
    test_trade = str(os.getenv("TEST_TRADE", "false")).lower() == "true"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
        stream=sys.stdout,
    )

    print(f"[test_lighter] symbol={symbol} read_only={read_only} test_trade={test_trade}")

    lig = LighterWS(symbol, read_only=read_only)

    try:
        await lig._init_market_id()
        print(
            f"[market] id={lig.market_id} size_decimals={lig.size_decimals} "
            f"price_decimals={lig.price_decimals} min_size={lig.min_size} min_value={lig.min_value}"
        )
    except Exception as exc:
        print(f"[market] init failed: {exc}")
        return

    try:
        qty, entry = await lig.load_initial_position()
        print(f"[position] qty={qty} entry={entry}")
    except Exception as exc:
        print(f"[position] load failed: {exc}")

    if not read_only and test_trade:
        try:
            await lig._init_trading_client()
            await lig._refresh_auth_token(force=True)
            await lig._ensure_trade_ws()
            connected = lig._ws_trade is not None and not lig._ws_trade.closed
            print(f"[trade_ws] connected={connected}")
        except Exception as exc:
            print(f"[trade_ws] init failed: {exc}")

    # cleanup
    try:
        if lig._ws_trade and not lig._ws_trade.closed:
            await lig._ws_trade.close()
        if lig._ws_session:
            await lig._ws_session.close()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())

