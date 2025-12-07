"""
Hyperliquid orderbook WS tester.
Subscribes to l2Book for a given coin and prints every message.

Usage:
  python -m bot.venues.hyperliquid_examples.ws_ob_tester --coin BTC
  HYPERLIQUID_API_URL=<url> python -m bot.venues.hyperliquid_examples.ws_ob_tester --coin ETH
  PYTHONPATH=. .venv/bin/python -m bot.venues.hyperliquid_examples.ws_ob_tester --coin BTC

"""

import argparse
import json
import os
import time

from hyperliquid.info import Info
from hyperliquid.utils import constants


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid l2Book WS tester")
    parser.add_argument("--coin", default="BTC", help="Coin symbol, e.g. BTC")
    args = parser.parse_args()

    api_url = os.getenv("HYPERLIQUID_API_URL", constants.MAINNET_API_URL)
    info = Info(api_url, skip_ws=False)

    def on_msg(msg):
        try:
            print(json.dumps(msg, separators=(",", ":")))
        except Exception:
            print(msg)

    info.subscribe({"type": "l2Book", "coin": args.coin}, on_msg)

    print(f"Subscribed to l2Book for {args.coin} on {api_url}. Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
