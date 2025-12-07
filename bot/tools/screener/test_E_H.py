# PYTHONPATH=. .venv/bin/python -m bot.tools.screener.test_E_H

import asyncio
import logging
import os
import sys
from typing import Dict, Set, Tuple

import aiohttp

from bot.tools.screener.E_H import _parse_pairs
from bot.venues.helper_extended import ExtendedWS
from bot.venues.helper_hyperliquid import HyperliquidWS
from bot.common.calc_spreads import calc_spreads


def _parse_pair(raw: str) -> Tuple[str, str]:
    if ":" not in raw:
        raise ValueError("pair must be in form ESYM:HSYM, e.g. BTC-USD:BTC")
    e_sym, h_sym = raw.split(":", 1)
    return e_sym.strip(), h_sym.strip()


async def _fetch_extended_markets() -> Dict[str, str]:
    """Fetch Extended USD markets mapping asset -> market name (e.g. BTC -> BTC-USD)."""
    url = "https://api.starknet.extended.exchange/api/v1/info/markets"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        markets = {}
        if data.get("status") == "OK":
            for m in data.get("data", []):
                asset = str(m.get("assetName") or "").upper()
                market = str(m.get("name") or "").upper()
                collateral = str(m.get("collateralAssetName") or "").upper()
                if asset and market and collateral == "USD":
                    markets[asset] = market
        return markets
    except Exception as exc:
        logging.getLogger("Screener").error(f"[TEST E_H] failed to fetch Extended markets: {exc}")
        return {}


async def _fetch_hyperliquid_assets() -> Set[str]:
    """Fetch tradable Hyperliquid coins."""
    url = os.getenv("HYPERLIQUID_INFO_URL", "https://api.hyperliquid.xyz/info")
    payload = {"type": "meta"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
        assets: Set[str] = set()
        universe = []
        if isinstance(data, dict):
            universe = data.get("universe") or data.get("coins") or []
        if isinstance(universe, list):
            for item in universe:
                # SDK meta returns list of coin symbols as strings or dicts with "name"
                if isinstance(item, str):
                    assets.add(item.upper())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("token") or item.get("coin")
                    if name:
                        assets.add(str(name).upper())
        return assets
    except Exception as exc:
        logging.getLogger("Screener").error(f"[TEST E_H] failed to fetch Hyperliquid markets: {exc}")
        return set()


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    logger = logging.getLogger("Screener")

    # Exercise parse_pairs with env + args for quick sanity, then proceed to WS.
    os.environ.setdefault("SCREEN_PAIRS", "BTC-USD:BTC,ETH-USD:ETH")
    env_out = _parse_pairs([])
    arg_out = _parse_pairs(["SOL-USD:SOL", "DOGE-USD:DOGE"])
    logger.info(f"[TEST PARSE E_H] env-> {env_out} args-> {arg_out}")

    cli_pairs = _parse_pairs(sys.argv[1:])
    selected_pair = os.getenv("TEST_PAIR", "")

    if not selected_pair:
        pairs = cli_pairs or env_out
        if not pairs:
            # autodiscover overlap
            ext_map, hyp_assets = await asyncio.gather(_fetch_extended_markets(), _fetch_hyperliquid_assets())
            overlap = [(mkt, asset) for asset, mkt in ext_map.items() if asset in hyp_assets]
            logger.info(f"[TEST E_H] autodiscovered overlaps ({len(overlap)}): {overlap}")
            if overlap:
                pairs = overlap
        if pairs:
            selected_pair = f"{pairs[0][0]}:{pairs[0][1]}"
    if not selected_pair:
        selected_pair = "BTC-USD:BTC"

    # Fetch and summarize markets, log count of matches.
    ext_map, hyp_assets = await asyncio.gather(_fetch_extended_markets(), _fetch_hyperliquid_assets())
    overlap = [(mkt, asset) for asset, mkt in ext_map.items() if asset in hyp_assets]
    logger.info(f"[TEST E_H] Matching Pairs: {len(overlap)}")
    logger.info(f"[TEST E_H] selected pair: {selected_pair}")

    # Subscribe to OB for the selected pair and print spreads.
    e_sym, h_sym = _parse_pair(selected_pair)
    E = ExtendedWS(e_sym, read_only=True)
    H = HyperliquidWS(h_sym, read_only=True)

    def on_update():
        ebid, eask = E.ob["bidPrice"], E.ob["askPrice"]
        hbid, hask = H.ob["bidPrice"], H.ob["askPrice"]
        print(f"{ebid} {eask} | {hbid} {hask}      ", end="\r", flush=True)
        if not all([ebid, eask, hbid, hask]):
            return
        spreads = calc_spreads(H, E)
        tt_le = spreads.get("TT_LE")
        tt_el = spreads.get("TT_EL")
        if tt_le is None or tt_el is None:
            return
        line = (
            f"{e_sym}:{h_sym} TT_LE={tt_le:.3f}% TT_EL={tt_el:.3f}% "
            f"H bid/ask={hbid}/{hask} E bid/ask={ebid}/{eask}"
        )
        # print(line, end="\r", flush=True)

    E.set_ob_callback(on_update)
    H.set_ob_callback(on_update)
    await asyncio.gather(E.start(), H.start())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
