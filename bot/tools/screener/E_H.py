import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple

import aiohttp

from bot.common.calc_spreads import calc_spreads
from bot.venues.helper_extended import ExtendedWS
from bot.venues.helper_hyperliquid import HyperliquidWS

DEFAULT_THRESHOLD = float(os.getenv("SCREENER_THRESHOLD", "0.3"))  # percent
ALERT_COOLDOWN = float(os.getenv("SCREENER_COOLDOWN_SEC", "0.01"))
SPREAD_KEYS = ["TT"]
SPREAD_MAP = {
    "TT": ["TT_LE", "TT_EL"],
}


def _parse_pairs(args: List[str]) -> List[Tuple[str, str]]:
    """Parse pairs in form ESYM:HSYM from CLI or SCREEN_PAIRS env."""
    if args:
        raw = args
    else:
        env_pairs = os.getenv("SCREEN_PAIRS", "")
        raw = [p for p in env_pairs.split(",") if p]
    pairs: List[Tuple[str, str]] = []
    for item in raw:
        if ":" in item:
            e_sym, h_sym = item.split(":", 1)
            pairs.append((e_sym.strip(), h_sym.strip()))
    return pairs


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
        logging.getLogger("Screener").error(f"[Screener] failed to fetch Extended markets: {exc}")
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
                if isinstance(item, str):
                    assets.add(item.upper())
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("token") or item.get("coin")
                    if name:
                        assets.add(str(name).upper())
        return assets
    except Exception as exc:
        logging.getLogger("Screener").error(f"[Screener] failed to fetch Hyperliquid markets: {exc}")
        return set()


class PairMonitor:
    def __init__(self, symbol_e: str, symbol_h: str, threshold: float):
        self.symbol_e = symbol_e
        self.symbol_h = symbol_h
        self.threshold = threshold
        self.E = ExtendedWS(symbol_e, read_only=True)
        self.H = HyperliquidWS(symbol_h, read_only=True)
        self._logger = logging.getLogger("Screener")
        self._last_alert = {k: 0 for k in ["TT_LE", "TT_EL"]}

    async def start(self):
        def on_update():
            asyncio.create_task(self._handle_spread())

        self.E.set_ob_callback(on_update)
        self.H.set_ob_callback(on_update)

        try:
            await asyncio.gather(self.E.start(), self.H.start())
        except Exception as exc:
            self._logger.error(f"[Screener] start failed for {self.symbol_e}:{self.symbol_h} - {exc}")

    async def _handle_spread(self):
        try:
            ebid, eask = self.E.ob["bidPrice"], self.E.ob["askPrice"]
            hbid, hask = self.H.ob["bidPrice"], self.H.ob["askPrice"]
            if not all([ebid, eask, hbid, hask]):
                return

            spreads = calc_spreads(self.H, self.E)  # order matters: L=H, E=Extended
            now = time.time()
            keys = []
            for group in SPREAD_KEYS:
                keys.extend(SPREAD_MAP.get(group, []))
            for key in keys:
                val = spreads.get(key)
                if val is None or val <= self.threshold:
                    continue
                if now - self._last_alert.get(key, 0) < ALERT_COOLDOWN:
                    continue
                self._last_alert[key] = now
                self._logger.info(
                    f"[Screener] {self.symbol_e}:{self.symbol_h} {key}={val:.2f}% "
                    f"H bid/ask={hbid}/{hask} E bid/ask={ebid}/{eask}"
                )
        except Exception as exc:
            self._logger.error(f"[Screener] spread handler error for {self.symbol_e}:{self.symbol_h}: {exc}")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    class PrependFileHandler(logging.Handler):
        def __init__(self, path: Path):
            super().__init__()
            self.path = path

        def emit(self, record):
            msg = self.format(record) + "\n"
            try:
                existing = self.path.read_text() if self.path.exists() else ""
                self.path.write_text(msg + existing)
            except Exception:
                pass

    fh = PrependFileHandler(log_dir / "screener_EH.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    logging.getLogger("Screener").addHandler(fh)
    logging.getLogger("Screener").propagate = False

    import sys

    pairs = _parse_pairs(sys.argv[1:])
    if not pairs:
        ext_map, hyp_assets = await asyncio.gather(_fetch_extended_markets(), _fetch_hyperliquid_assets())
        overlap = [(mkt, asset) for asset, mkt in ext_map.items() if asset in hyp_assets]
        logging.getLogger("Screener").info(f"[Screener] Matching Pairs: {len(overlap)}")
        pairs = overlap
    if not pairs:
        logging.getLogger("Screener").error("No pairs provided (ESYM:HSYM) and no overlap found.")
        return

    monitors = [PairMonitor(e, h, DEFAULT_THRESHOLD) for e, h in pairs]
    tasks = [asyncio.create_task(m.start()) for m in monitors]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logging.getLogger("Screener").error(
                f"[Screener] monitor {monitors[idx].symbol_e}:{monitors[idx].symbol_h} crashed: {res}"
            )


if __name__ == "__main__":
    asyncio.run(main())
