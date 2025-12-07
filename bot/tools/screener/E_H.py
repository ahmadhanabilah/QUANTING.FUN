import asyncio
import html
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Set, Tuple

import aiohttp

from bot.common.calc_spreads import calc_spreads
from bot.venues.helper_extended import ExtendedWS
from bot.venues.helper_hyperliquid import HyperliquidWS


DEFAULT_THRESHOLD = 0.3
AGG_SECONDS = 60
ENABLE_TT = os.getenv("SCREENER_ENABLE_TT", "true").lower() == "true"
ENABLE_MT = os.getenv("SCREENER_ENABLE_MT", "false").lower() == "true"
ENABLE_TM = os.getenv("SCREENER_ENABLE_TM", "false").lower() == "true"
SPREAD_KEYS: List[str] = []
if ENABLE_TT:
    SPREAD_KEYS.append("TT")
if ENABLE_MT:
    SPREAD_KEYS.append("MT")
if ENABLE_TM:
    SPREAD_KEYS.append("TM")
SPREAD_MAP = {
    "TT": ["TT_LE", "TT_EL"],
    "MT": ["MT_LE", "MT_EL"],
    "TM": ["TM_LE", "TM_EL"],
}
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_TOPIC_ID = os.getenv("TELEGRAM_TOPIC_ID", "")
_tg_session: Optional[aiohttp.ClientSession] = None
_hits: DefaultDict[str, Dict[str, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
_hits_lock = asyncio.Lock()


async def _send_telegram(text: str, code: bool = False):
    global _tg_session
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if _tg_session is None or _tg_session.closed:
        _tg_session = aiohttp.ClientSession()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        if code:
            payload["parse_mode"] = "HTML"
            payload["text"] = f"<pre>{html.escape(text)}</pre>"
        if TELEGRAM_TOPIC_ID:
            payload["message_thread_id"] = TELEGRAM_TOPIC_ID
        await _tg_session.post(url, data=payload)
    except Exception as exc:
        logging.getLogger("Screener").error(f"[Screener] telegram send failed: {exc}")


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
    """Fetch all Extended markets (USD) mapping asset -> market name (e.g. BTC -> BTC-USD)."""
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
        self._last_alert = {k: 0 for k in ["TT_LE", "TT_EL", "MT_LE", "MT_EL", "TM_LE", "TM_EL"]}

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
                if val is None:
                    continue
                if val <= self.threshold:
                    continue
                if now - self._last_alert.get(key, 0) < ALERT_COOLDOWN:
                    continue
                self._last_alert[key] = now
                msg = (
                    f"[Screener] {self.symbol_e}:{self.symbol_h} {key}={val:.2f}% "
                    f"H bid/ask={hbid}/{hask} E bid/ask={ebid}/{eask}"
                )
                self._logger.info(msg)
                async with _hits_lock:
                    _hits[f"{self.symbol_e}/{self.symbol_h}"][key].append((now, val))
                await _send_telegram(msg)
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
        logging.getLogger("Screener").error("No pairs provided (format ESYM:HSYM via args or SCREEN_PAIRS env)")
        return

    monitors = [PairMonitor(e, h, DEFAULT_THRESHOLD) for e, h in pairs]
    tasks = [asyncio.create_task(m.start()) for m in monitors]

    async def aggregator():
        logger = logging.getLogger("Screener")

        def _median(values: List[float]) -> float:
            values = sorted(values)
            n = len(values)
            if n == 0:
                return 0.0
            mid = n // 2
            if n % 2 == 1:
                return values[mid]
            return (values[mid - 1] + values[mid]) / 2

        while True:
            await asyncio.sleep(AGG_SECONDS)
            now = time.time()
            rows = []
            async with _hits_lock:
                for pair, key_map in list(_hits.items()):
                    for key, vals in list(key_map.items()):
                        key_map[key] = [(ts, v) for ts, v in vals if ts >= now - AGG_SECONDS]
                        if not key_map[key]:
                            continue
                        spreads_only = [v for _, v in key_map[key]]
                        rows.append((pair, key, len(spreads_only), max(spreads_only), _median(spreads_only)))
            if rows:
                rows.sort(key=lambda r: r[3], reverse=True)
                lines = [
                    f"{pair} {key} hits={cnt} max={mx:.2f}% med={med:.2f}%"
                    for pair, key, cnt, mx, med in rows
                ]
                msg = "[Screener agg " + datetime.utcnow().strftime("%H:%M:%S") + "] " + "; ".join(lines)
                logger.info(msg)

    results = await asyncio.gather(*tasks, aggregator(), return_exceptions=True)
    for idx, res in enumerate(results[:-1]):  # last is aggregator
        if isinstance(res, Exception):
            logging.getLogger("Screener").error(
                f"[Screener] monitor {monitors[idx].symbol_e}:{monitors[idx].symbol_h} crashed: {res}"
            )


if __name__ == "__main__":
    asyncio.run(main())
