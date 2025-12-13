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
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_extended import ExtendedWS
from bot.venues.helper_hyperliquid import HyperliquidWS

# -------- config --------

DEFAULT_THRESHOLD = float(os.getenv("SCREENER_THRESHOLD", "0.3"))
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
# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_TOPIC_LE = os.getenv("TELEGRAM_TOPIC_LE", "")
TELEGRAM_TOPIC_EH = os.getenv("TELEGRAM_TOPIC_EH", "")
TELEGRAM_TOPIC_LH = os.getenv("TELEGRAM_TOPIC_LH", "")
_tg_session: Optional[aiohttp.ClientSession] = None

# hit storage: pair -> key -> list of (ts, spread)
_hits: DefaultDict[str, Dict[str, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
_hits_lock = asyncio.Lock()

# -------- utils --------


async def _send_telegram(text: str, topic_id: str):
    global _tg_session
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if _tg_session is None or _tg_session.closed:
        _tg_session = aiohttp.ClientSession()
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if topic_id:
        payload["message_thread_id"] = topic_id
    try:
        await _tg_session.post(url, data=payload)
    except Exception as exc:
        logging.getLogger("Screener").error(f"[Screener] telegram send failed: {exc}")


def _parse_pairs(args: List[str]) -> List[Tuple[str, str]]:
    if args:
        raw = args
    else:
        env_pairs = os.getenv("SCREEN_PAIRS", "")
        raw = [p for p in env_pairs.split(",") if p]
    pairs: List[Tuple[str, str]] = []
    for item in raw:
        if ":" in item:
            a, b = item.split(":", 1)
            pairs.append((a.strip(), b.strip()))
    return pairs


async def _fetch_lighter_symbols() -> Set[str]:
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


async def _fetch_extended_markets() -> Dict[str, str]:
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


# -------- monitors --------


class PairMonitor:
    def __init__(self, label: str, a, b, topic_id: str, threshold: float):
        self.label = label  # e.g., "LE", "EH", "LH"
        self.a = a
        self.b = b
        self.topic_id = topic_id
        self.threshold = threshold
        self._logger = logging.getLogger("Screener")
        self._last_alert = defaultdict(float)

    async def start(self):
        def on_update():
            asyncio.create_task(self._handle_spread())

        self.a.set_ob_callback(on_update)
        self.b.set_ob_callback(on_update)
        await asyncio.gather(self.a.start(), self.b.start())

    async def _handle_spread(self):
        try:
            abid, aask = self.a.ob["bidPrice"], self.a.ob["askPrice"]
            bbid, bask = self.b.ob["bidPrice"], self.b.ob["askPrice"]
            if not all([abid, aask, bbid, bask]):
                return
            spreads = calc_spreads(self.a, self.b)
            now = time.time()
            keys = []
            for group in SPREAD_KEYS:
                keys.extend(SPREAD_MAP.get(group, []))
            for key in keys:
                val = spreads.get(key)
                if val is None or val <= self.threshold:
                    continue
                if now - self._last_alert.get(key, 0) < float(os.getenv("SCREENER_COOLDOWN_SEC", "0.01")):
                    continue
                self._last_alert[key] = now
                msg = (
                    f"[Screener-{self.label}] {key}={val:.2f}% "
                    f"{self._name_a()}"
                    f" bid/ask={abid}/{aask} {self._name_b()} bid/ask={bbid}/{bask}"
                )
                self._logger.info(msg)
                async with _hits_lock:
                    _hits[f"{self.label}:{self._pair_name()}"][key].append((now, val))
                await _send_telegram(msg, self.topic_id)
        except Exception as exc:
            self._logger.error(f"[Screener-{self.label}] spread handler error for {self._pair_name()}: {exc}")

    def _pair_name(self) -> str:
        try:
            return f"{self.a.symbol}/{self.b.symbol}"
        except Exception:
            return "unknown"

    def _name_a(self):
        return getattr(self.a, "symbol", "A")

    def _name_b(self):
        return getattr(self.b, "symbol", "B")


# -------- main --------


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

    fh = PrependFileHandler(log_dir / "screener.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    logging.getLogger("Screener").addHandler(fh)
    logging.getLogger("Screener").propagate = False

    import sys

    # Explicit pairs via args/env (ESYM:HSYM, LSYM:ESYM, LSYM:HSYM)
    le_pairs = _parse_pairs(os.getenv("SCREEN_PAIRS_LE", "").split(",") if os.getenv("SCREEN_PAIRS_LE") else [])
    eh_pairs = _parse_pairs(os.getenv("SCREEN_PAIRS_EH", "").split(",") if os.getenv("SCREEN_PAIRS_EH") else [])
    lh_pairs = _parse_pairs(os.getenv("SCREEN_PAIRS_LH", "").split(",") if os.getenv("SCREEN_PAIRS_LH") else [])

    # If CLI provided, treat as L:E pairs to keep backward compat
    cli_pairs = _parse_pairs(sys.argv[1:])
    if cli_pairs:
        le_pairs = cli_pairs

    if not (le_pairs and eh_pairs and lh_pairs):
        lighter_syms, extended_map, hyp_assets = await asyncio.gather(
            _fetch_lighter_symbols(), _fetch_extended_markets(), _fetch_hyperliquid_assets()
        )
        if not le_pairs:
            le_pairs = [(sym, extended_map[sym]) for sym in lighter_syms if sym in extended_map]
        if not eh_pairs:
            eh_pairs = [(mkt, asset) for asset, mkt in extended_map.items() if asset in hyp_assets]
        if not lh_pairs:
            lh_pairs = [(sym, sym) for sym in lighter_syms if sym in hyp_assets]
        logging.getLogger("Screener").info(
            f"[Screener] Matching Pairs: LE={len(le_pairs)} EH={len(eh_pairs)} LH={len(lh_pairs)}"
        )

    monitors: List[PairMonitor] = []
    for l, e in le_pairs:
        monitors.append(
            PairMonitor("LE", LighterWS(l, read_only=True), ExtendedWS(e, read_only=True), TELEGRAM_TOPIC_LE, DEFAULT_THRESHOLD)
        )
    for e, h in eh_pairs:
        monitors.append(
            PairMonitor("EH", HyperliquidWS(h, read_only=True), ExtendedWS(e, read_only=True), TELEGRAM_TOPIC_EH, DEFAULT_THRESHOLD)
        )
    for l, h in lh_pairs:
        monitors.append(
            PairMonitor("LH", LighterWS(l, read_only=True), HyperliquidWS(h, read_only=True), TELEGRAM_TOPIC_LH, DEFAULT_THRESHOLD)
        )

    if not monitors:
        logging.getLogger("Screener").error("No pairs to monitor; provide SCREEN_PAIRS_* or ensure overlaps exist.")
        return

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
            logging.getLogger("Screener").error(f"[Screener] monitor crashed: {res}")


if __name__ == "__main__":
    asyncio.run(main())
