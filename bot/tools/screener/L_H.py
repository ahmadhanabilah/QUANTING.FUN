import asyncio
import logging
import os
import time
from pathlib import Path
from typing import List, Tuple

from bot.common.calc_spreads import calc_spreads
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_hyperliquid import HyperliquidWS

DEFAULT_THRESHOLD = float(os.getenv("SCREENER_THRESHOLD", "0.3"))  # percent
ALERT_COOLDOWN = float(os.getenv("SCREENER_COOLDOWN_SEC", "0.01"))
SPREAD_KEYS = ["TT"]
SPREAD_MAP = {
    "TT": ["TT_LE", "TT_EL"],
}


def _parse_pairs(args: List[str]) -> List[Tuple[str, str]]:
    """Parse pairs in form LSYM:HSYM from CLI or SCREEN_PAIRS env."""
    if args:
        raw = args
    else:
        env_pairs = os.getenv("SCREEN_PAIRS", "")
        raw = [p for p in env_pairs.split(",") if p]
    pairs: List[Tuple[str, str]] = []
    for item in raw:
        if ":" in item:
            l_sym, h_sym = item.split(":", 1)
            pairs.append((l_sym.strip(), h_sym.strip()))
    return pairs


class PairMonitor:
    def __init__(self, symbol_l: str, symbol_h: str, threshold: float):
        self.symbol_l = symbol_l
        self.symbol_h = symbol_h
        self.threshold = threshold
        self.L = LighterWS(symbol_l, read_only=True)
        self.H = HyperliquidWS(symbol_h, read_only=True)
        self._logger = logging.getLogger("Screener")
        self._last_alert = {k: 0 for k in ["TT_LE", "TT_EL"]}

    async def start(self):
        def on_update():
            asyncio.create_task(self._handle_spread())

        self.L.set_ob_callback(on_update)
        self.H.set_ob_callback(on_update)

        try:
            await asyncio.gather(self.L.start(), self.H.start())
        except Exception as exc:
            self._logger.error(f"[Screener] start failed for {self.symbol_l}:{self.symbol_h} - {exc}")

    async def _handle_spread(self):
        try:
            lbid, lask = self.L.ob["bidPrice"], self.L.ob["askPrice"]
            hbid, hask = self.H.ob["bidPrice"], self.H.ob["askPrice"]
            if not all([lbid, lask, hbid, hask]):
                return

            spreads = calc_spreads(self.L, self.H)
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
                    f"[Screener] {self.symbol_l}:{self.symbol_h} {key}={val:.2f}% "
                    f"L bid/ask={lbid}/{lask} H bid/ask={hbid}/{hask}"
                )
        except Exception as exc:
            self._logger.error(f"[Screener] spread handler error for {self.symbol_l}:{self.symbol_h}: {exc}")


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

    fh = PrependFileHandler(log_dir / "screener_LH.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    logging.getLogger("Screener").addHandler(fh)
    logging.getLogger("Screener").propagate = False

    import sys
    pairs = _parse_pairs(sys.argv[1:])
    if not pairs:
        logging.getLogger("Screener").error("No pairs provided (format LSYM:HSYM via args or SCREEN_PAIRS env)")
        return

    monitors = [PairMonitor(l, h, DEFAULT_THRESHOLD) for l, h in pairs]
    tasks = [asyncio.create_task(m.start()) for m in monitors]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logging.getLogger("Screener").error(f"[Screener] monitor {monitors[idx].symbol_l}:{monitors[idx].symbol_h} crashed: {res}")


if __name__ == "__main__":
    asyncio.run(main())
