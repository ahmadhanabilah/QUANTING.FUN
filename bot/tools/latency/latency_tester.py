"""
Latency tester for both venues.

Runs a tiny hedged market order (Extended LONG, Lighter SHORT) and reports:
- order send latency (ms) per venue
- fill latency (ms) per venue (from send start to first fill notification)

Usage:
  /root/arb_bot/.venv/bin/python -m bot.latency.latency_tester BTC BTC-USD --size 0.0003

Requires trading credentials in .env_bot (EXTENDED_*, LIGHTER_*).
This sends real orders; use a very small size and only in a test account.
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from bot.common.enums import Side
from bot.venues.helper_extended import ExtendedWS
from bot.venues.helper_lighter import LighterWS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env_bot"


def _load_env() -> None:
    """Minimal .env loader."""
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


async def _wait_for_books(lighter: LighterWS, extended: ExtendedWS, timeout: float = 20.0) -> bool:
    """Wait until both venues have a bid/ask."""
    start = time.time()
    while time.time() - start < timeout:
        if lighter.ob["bidPrice"] and lighter.ob["askPrice"] and extended.ob["bidPrice"] and extended.ob["askPrice"]:
            return True
        await asyncio.sleep(0.2)
    return False


async def _wait_for_fills(fill_lat: Dict[str, list], expected: int, timeout: float = 20.0) -> None:
    """Wait for both venues to record expected fill latencies or until timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if all(len(fill_lat.get(v, [])) >= expected for v in ("L", "E")):
            return
        await asyncio.sleep(0.2)


async def run_latency_test(symbol_l: str, symbol_e: str, size: Optional[float] = None) -> int:
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    logger = logging.getLogger("LatencyTest")

    lighter = LighterWS(symbol_l)
    extended = ExtendedWS(symbol_e)

    send_latency: Dict[str, list] = {"L": [], "E": []}
    fill_latency: Dict[str, list] = {"L": [], "E": []}
    send_ts: Dict[str, list] = {"L": [], "E": []}

    def _inv_l(delta: float) -> None:
        if send_ts["L"]:
            ts = send_ts["L"].pop(0)
            lat = (time.perf_counter() - ts) * 1000
            fill_latency["L"].append(lat)
            logger.info(f"[Lighter] fill delta={delta} fill_latency_ms={lat:.1f}")

    def _inv_e(delta: float) -> None:
        if send_ts["E"]:
            ts = send_ts["E"].pop(0)
            lat = (time.perf_counter() - ts) * 1000
            fill_latency["E"].append(lat)
            logger.info(f"[Extended] fill delta={delta} fill_latency_ms={lat:.1f}")

    lighter.set_inventory_callback(_inv_l)
    extended.set_inventory_callback(_inv_e)

    tasks = [
        asyncio.create_task(lighter.start()),
        asyncio.create_task(extended.start()),
    ]

    try:
        ready = await _wait_for_books(lighter, extended, timeout=25.0)
        if not ready:
            logger.error("Orderbooks not ready within timeout; aborting.")
            return 1

        # pick common size: user provided or max of venue minimums
        min_l = lighter.min_size or 0.0
        min_quote_l = getattr(lighter, "min_value", 0.0) or 0.0
        slip_l = getattr(lighter, "config", {}).get("slippage", 0.0) or 0.0
        bid_l = lighter.ob.get("bidPrice", 0.0) or 0.0
        ask_l = lighter.ob.get("askPrice", 0.0) or 0.0
        price_long_l = ask_l * (1 + slip_l) if ask_l else 0.0
        price_short_l = bid_l * (1 - slip_l) if bid_l else 0.0
        # use the lower of the two to ensure size*price >= min_quote for both long/short
        price_for_quote_l = min([p for p in (price_long_l, price_short_l) if p > 0] or [0.0])
        req_light = min_l
        if min_quote_l > 0 and price_for_quote_l > 0:
            req_light = max(req_light, min_quote_l / price_for_quote_l)

        min_e = extended.min_size or 0.0
        base_size = size if size is not None else max(req_light, min_e, 0.0003)
        # slight buffer above min
        chosen_l = base_size * 1.02
        chosen_e = base_size * 1.02
        logger.info(
            f"Min sizes → Lighter base={min_l} quote={min_quote_l} (price_long={price_long_l}, price_short={price_short_l}) | "
            f"Extended base={min_e} | Using common size {chosen_e} (with 2% buffer)"
        )

        # Sequences:
        #   Lighter: 2x LONG then 2x SHORT
        #   Extended: 2x SHORT then 2x LONG
        lighter_seq = [Side.LONG, Side.LONG, Side.SHORT, Side.SHORT]
        extended_seq = [Side.SHORT, Side.SHORT, Side.LONG, Side.LONG]

        for i, (ext_side, light_side) in enumerate(zip(extended_seq, lighter_seq), start=1):
            # compute aggressive prices with slippage at send time
            slip_l_now = getattr(lighter, "config", {}).get("slippage", 0.0) or 0.0
            slip_e_now = getattr(extended, "config", {}).get("slippage", 0.0) or 0.0
            ob = lighter.ob
            ob_e = extended.ob
            price_light = (ob["askPrice"] * (1 + slip_l_now)) if light_side == Side.LONG else (ob["bidPrice"] * (1 - slip_l_now))
            price_ext = (ob_e["askPrice"] * (1 + slip_e_now)) if ext_side == Side.LONG else (ob_e["bidPrice"] * (1 - slip_e_now))

            # Extended
            start_e = time.perf_counter()
            res_e = await extended.send_market(ext_side, chosen_e, price_ext)
            send_latency["E"].append((time.perf_counter() - start_e) * 1000)
            send_ts["E"].append(start_e)
            logger.info(f"[Extended] order#{i} side={ext_side.name} order_latency_ms={send_latency['E'][-1]:.1f} result={res_e}")

            # Lighter
            start_l = time.perf_counter()
            res_l = await lighter.send_market(light_side, chosen_l, price_light)
            send_latency["L"].append((time.perf_counter() - start_l) * 1000)
            send_ts["L"].append(start_l)
            logger.info(f"[Lighter] order#{i} side={light_side.name} order_latency_ms={send_latency['L'][-1]:.1f} result={res_l}")

            if i < len(lighter_seq):
                await asyncio.sleep(2.0)

        await _wait_for_fills(fill_latency, expected=4, timeout=60.0)

        logger.info("=== Latency summary (ms) ===")
        for venue in ("E", "L"):
            sends = ", ".join(f"{v:.1f}" for v in send_latency[venue]) or "n/a"
            fills = ", ".join(f"{v:.1f}" for v in fill_latency[venue]) or "n/a"
            name = "Extended" if venue == "E" else "Lighter "
            logger.info(f"{name} send=[{sends}] fill=[{fills}]")
        return 0
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Latency tester for Extended + Lighter venues.")
    parser.add_argument("symbol_l", help="Lighter symbol, e.g. BTC")
    parser.add_argument("symbol_e", help="Extended symbol, e.g. BTC-USD")
    parser.add_argument("--size", type=float, default=None, help="Order size (base units). If omitted, uses venue minimums.")
    args = parser.parse_args()

    try:
        rc = asyncio.run(run_latency_test(args.symbol_l, args.symbol_e, args.size))
        sys.exit(rc)
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
