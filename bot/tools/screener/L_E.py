import asyncio
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple

import aiohttp

from bot.common.calc_spreads import calc_spreads
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_extended import ExtendedWS

DEFAULT_THRESHOLD = float(os.getenv("SCREENER_THRESHOLD", "0.3"))  # percent
ALERT_COOLDOWN = float(os.getenv("SCREENER_COOLDOWN_SEC", "0.01"))
SPREAD_KEYS = ["TT"]
SPREAD_MAP = {
    "TT": ["TT_LE", "TT_EL"],
}


def _parse_pairs(args: List[str]) -> List[Tuple[str, str]]:
    """Parse pairs in form LSYM:ESYM from CLI or SCREEN_PAIRS env."""
    if args:
        raw = args
    else:
        env_pairs = os.getenv("SCREEN_PAIRS", "")
        raw = [p for p in env_pairs.split(",") if p]
    pairs: List[Tuple[str, str]] = []
    for item in raw:
        if ":" in item:
            l_sym, e_sym = item.split(":", 1)
            pairs.append((l_sym.strip(), e_sym.strip()))
    return pairs


async def _fetch_lighter_symbols() -> set:
    """Fetch all Lighter symbols from orderBookDetails."""
    url = "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
        syms = set()
        details = []
        if isinstance(data, dict):
            details = data.get("order_book_details") or data.get("orderBookDetails") or []
        elif isinstance(data, list):
            details = data
        for d in details:
            sym = str(d.get("symbol") or "").upper()
            if sym:
                syms.add(sym)
        return syms
    except Exception as exc:
        logging.getLogger("Screener").error(f"[Screener] failed to fetch Lighter markets: {exc}")
        return set()


async def _fetch_extended_markets() -> dict:
    """Fetch all Extended USD markets, return mapping asset -> market name (e.g. BTC -> BTC-USD)."""
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


class PairMonitor:
    def __init__(self, symbol_l: str, symbol_e: str, threshold: float):
        self.symbol_l = symbol_l
        self.symbol_e = symbol_e
        self.threshold = threshold
        self.L = LighterWS(symbol_l, read_only=True)
        self.E = ExtendedWS(symbol_e, read_only=True)
        self._logger = logging.getLogger("Screener")
        self._last_alert = {k: 0 for k in ["TT_LE", "TT_EL"]}

    async def start(self):
        def on_update():
            asyncio.create_task(self._handle_spread())

        self.L.set_ob_callback(on_update)
        self.E.set_ob_callback(on_update)

        try:
            await asyncio.gather(self.L.start(), self.E.start())
        except Exception as exc:
            self._logger.error(f"[Screener] start failed for {self.symbol_l}:{self.symbol_e} - {exc}")

    async def _handle_spread(self):
        try:
            lbid, lask = self.L.ob["bidPrice"], self.L.ob["askPrice"]
            ebid, eask = self.E.ob["bidPrice"], self.E.ob["askPrice"]
            if not all([lbid, lask, ebid, eask]):
                return

            spreads = calc_spreads(self.L, self.E)
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
                    f"[Screener] {self.symbol_l}:{self.symbol_e} {key}={val:.2f}% "
                    f"L bid/ask={lbid}/{lask} E bid/ask={ebid}/{eask}"
                )
        except Exception as exc:
            self._logger.error(f"[Screener] spread handler error for {self.symbol_l}:{self.symbol_e}: {exc}")


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

    fh = PrependFileHandler(log_dir / "screener_LE.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    logging.getLogger("Screener").addHandler(fh)
    logging.getLogger("Screener").propagate = False

    import sys
    pairs = _parse_pairs(sys.argv[1:])
    if not pairs:
        # autodiscover pairs present on both venues (USD quote)
        lighter_syms, extended_map = await asyncio.gather(
            _fetch_lighter_symbols(), _fetch_extended_markets()
        )
        logger = logging.getLogger("Screener")
        logger.info(f"[Screener] lighter symbols: {len(lighter_syms)} extended USD markets: {len(extended_map)}")
        pairs = []
        for sym in lighter_syms:
            if sym in extended_map:
                pairs.append((sym, extended_map[sym]))
        if not pairs:
            logger.error("No pairs provided and no overlap detected between Lighter and Extended.")
            return
        logger.info(f"[Screener] autodiscovered pairs: {pairs}")

    monitors = [PairMonitor(l, e, DEFAULT_THRESHOLD) for l, e in pairs]
    tasks = [asyncio.create_task(m.start()) for m in monitors]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logging.getLogger("Screener").error(f"[Screener] monitor {monitors[idx].symbol_l}:{monitors[idx].symbol_e} crashed: {res}")


if __name__ == "__main__":
    asyncio.run(main())
