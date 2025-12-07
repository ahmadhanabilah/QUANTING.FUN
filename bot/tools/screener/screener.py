# cd /root/arb_bot && source .venv/bin/activate && python -m screener.screener

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple, Dict, Set, Optional, DefaultDict
from collections import defaultdict
from datetime import datetime
import html

import aiohttp

from common.calc_spreads import calc_spreads
from venues.helper_lighter import LighterWS
from venues.helper_extended import ExtendedWS


DEFAULT_THRESHOLD       = 0.3
AGG_SECONDS             = 60
ENABLE_TT               = os.getenv("SCREENER_ENABLE_TT", "true").lower() == "true"
ENABLE_MT               = os.getenv("SCREENER_ENABLE_MT", "false").lower() == "true"
ENABLE_TM               = os.getenv("SCREENER_ENABLE_TM", "false").lower() == "true"
SPREAD_KEYS = []
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
TELEGRAM_TOKEN = "7409766261:AAHuwcll1fi3g5J8N_5R2qSF16dIMEYLUlI"
TELEGRAM_CHAT_ID = "-1003203774300"
TELEGRAM_TOPIC_ID = 4  # set to thread id if using a forum topic
_tg_session: Optional[aiohttp.ClientSession] = None
# pair -> key -> list of (timestamp, spread_percent)
_hits: DefaultDict[str, Dict[str, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
_hits_lock = asyncio.Lock()


async def _send_telegram(text: str, code: bool = False):
    """Send a plain text alert to Telegram."""
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


async def _fetch_lighter_symbols() -> Set[str]:
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


async def _fetch_extended_markets() -> Dict[str, str]:
    """
    Fetch all Extended markets, return mapping asset -> market name (e.g. BTC -> BTC-USD).
    """
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
                if asset and market:
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
        self._last_alert = {k: 0 for k in ["TT_LE", "TT_EL", "MT_LE", "MT_EL", "TM_LE", "TM_EL"]}

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
                if val is None:
                    continue
                # spreads already expressed in percent; compare directly to threshold
                if val <= self.threshold:
                    continue
                self._logger.info(
                    f"[Screener] {self.symbol_l}:{self.symbol_e} {key}={val:.2f}% "
                    f"L bid/ask={lbid}/{lask} E bid/ask={ebid}/{eask}"
                )
                # store hit for aggregation
                pair_key = f"{self.symbol_l}/{self.symbol_e}"
                async with _hits_lock:
                    _hits[pair_key][key].append((now, val))
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

    fh = PrependFileHandler(log_dir / "screener.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    logging.getLogger("Screener").addHandler(fh)
    logging.getLogger("Screener").propagate = False

    import sys
    pairs = _parse_pairs(sys.argv[1:])
    if not pairs:
        # autodiscover pairs present on both venues (any quote)
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
            logger.error(
                f"No pairs provided and no overlap detected between Lighter and Extended. "
                f"example lighter symbols={list(lighter_syms)[:5]} "
                f"example extended assets={list(extended_map.keys())[:5]}"
            )
            return
        logger.info(f"[Screener] autodiscovered pairs: {pairs}")

    monitors = [PairMonitor(l, e, DEFAULT_THRESHOLD) for l, e in pairs]
    tasks = [asyncio.create_task(m.start()) for m in monitors]

    async def aggregator():
        logger = logging.getLogger("Screener")

        def _median(values: List[float]) -> float:
            vals = sorted(values)
            n = len(vals)
            if n == 0:
                return 0.0
            mid = n // 2
            if n % 2 == 1:
                return vals[mid]
            return (vals[mid - 1] + vals[mid]) / 2

        while True:
            await asyncio.sleep(AGG_SECONDS)
            async with _hits_lock:
                snapshot = dict(_hits)
                _hits.clear()
            if not snapshot:
                continue
            # build entries with a max LE/EL avg for sorting
            entries = []
            for pair, keys in snapshot.items():
                sym_l = pair.split("/", 1)[0]
                stats: Dict[str, Dict[str, float]] = {}
                max_avg = None
                combined: DefaultDict[str, List[Tuple[float, float]]] = defaultdict(list)
                for key, vals in keys.items():
                    short = key.split("_", 1)[-1] if "_" in key else key
                    if short in ("LE", "EL"):
                        combined[short].extend(vals)

                for short, vals in combined.items():
                    if not vals:
                        continue
                    times = [t for t, _ in vals]
                    spreads = [v for _, v in vals]
                    avg = sum(spreads) / len(spreads)
                    max_avg = avg if max_avg is None or avg > max_avg else max_avg
                    span_ms = int((max(times) - min(times)) * 1000) if len(times) > 1 else 0
                    stats[short] = {
                        "count": len(spreads),
                        "max": max(spreads),
                        "min": min(spreads),
                        "avg": avg,
                        "median": _median(spreads),
                        "span_ms": span_ms,
                    }

                if not stats:
                    continue
                entries.append((max_avg or 0.0, sym_l, stats))
            # sort descending by max avg
            entries.sort(key=lambda x: x[0], reverse=True)
            lines: List[str] = []
            for _, sym, stats in entries:
                lines.append(f"••••• {sym} •••••")
                for short in ("LE", "EL"):
                    data = stats.get(short)
                    if not data:
                        continue
                    stat_text = (
                        f"{short}: {data['count']} Hits "
                        f"[{data['max']:.2f}%, {data['min']:.2f}%, {data['avg']:.2f}%, {data['median']:.2f}%, {data['span_ms']}ms]"
                    )
                    lines.append(stat_text)
            msg = "\n".join(lines)
            try:
                await _send_telegram(msg, code=False)
            except Exception as exc:
                logger.error(f"[Screener] failed to send aggregated alert: {exc}")

    agg_task = asyncio.create_task(aggregator())
    results = await asyncio.gather(*tasks, return_exceptions=True)
    agg_task.cancel()
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logging.getLogger("Screener").error(f"[Screener] monitor {monitors[idx].symbol_l}:{monitors[idx].symbol_e} crashed: {res}")


if __name__ == "__main__":
    asyncio.run(main())
