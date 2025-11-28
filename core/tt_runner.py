# TT runner (was run_all.py)
import asyncio
import logging
import os
import time
import json
from pathlib import Path

from common.state import State
from common.calc_spreads import calc_spreads
from venues.helper_lighter import LighterWS
from venues.helper_extended import ExtendedWS
from core.tt_bot import TTBot

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def _load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


async def main():
    _load_env()
    import sys
    # load config
    cfg_path = Path("config.json")
    config = {}
    if cfg_path.exists():
        try:
            config = json.loads(cfg_path.read_text())
        except Exception:
            config = {}
    symbols_cfg = config.get("symbols", []) if isinstance(config, dict) else []

    def pick_cfg(sym_l: str | None, sym_e: str | None):
        if sym_l:
            for item in symbols_cfg:
                if item.get("SYMBOL_LIGHTER") == sym_l and (sym_e is None or item.get("SYMBOL_EXTENDED") == sym_e):
                    return item
        return symbols_cfg[0] if symbols_cfg else {}

    arg_symL = sys.argv[1] if len(sys.argv) > 1 else None
    arg_symE = sys.argv[2] if len(sys.argv) > 2 else None
    cfg_item = pick_cfg(arg_symL, arg_symE)
    symbolL = cfg_item.get("SYMBOL_LIGHTER", arg_symL or "MEGA")
    symbolE = cfg_item.get("SYMBOL_EXTENDED", arg_symE or "MEGA-USD")

    # separate handlers per logger to avoid overlapping output; per-pair subfolder
    pair_dir = f"{symbolL}:{symbolE}"
    log_dir = Path("logs") / pair_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")

    class OverwriteFileHandler(logging.Handler):
        def __init__(self, path):
            super().__init__()
            self.path = Path(path)

        def emit(self, record):
            msg = self.format(record)
            try:
                # overwrite with the latest snapshot (single-line realtime view)
                self.path.write_text(msg + "\n")
            except Exception:
                pass

    class PrependFileHandler(logging.Handler):
        def __init__(self, path):
            super().__init__()
            self.path = Path(path)

        def emit(self, record):
            msg = self.format(record) + "\n"
            try:
                existing = self.path.read_text() if self.path.exists() else ""
                self.path.write_text(msg + existing)
            except Exception:
                pass

    class PrependCsvHandler(logging.Handler):
        """Prepend rows while keeping the header (first line) at the top."""
        def __init__(self, path, header: str = ""):
            super().__init__()
            self.path = Path(path)
            self.header = header.strip()

        def emit(self, record):
            msg = self.format(record)
            try:
                existing_lines = []
                if self.path.exists():
                    existing_lines = self.path.read_text().splitlines()

                header_present = bool(existing_lines and existing_lines[0].strip() == self.header)
                if self.header and not header_present:
                    if existing_lines:
                        # strip any duplicate header-like line
                        existing_lines = [ln for ln in existing_lines if ln.strip() != self.header]
                    existing_lines = [self.header] + existing_lines

                header_line = existing_lines[0] if existing_lines else ""
                body = existing_lines[1:] if len(existing_lines) > 1 else []
                new_body = [msg] + body
                out_lines = [header_line] + new_body if header_line else new_body
                self.path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""))
            except Exception:
                pass
    def add_file_handler(name, filename, handler_cls=logging.FileHandler):
        handler = handler_cls(log_dir / filename)
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        logging.getLogger(name).addHandler(handler)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    # console handler for quick visibility (avoid duplicate handlers)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)  # show debug on console for venues
    console.setFormatter(formatter)
    for name in ["LighterWS", "ExtendedWS"]:
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)  # allow debug logs for weighted price traces
        lg.handlers = [h for h in lg.handlers if not isinstance(h, logging.StreamHandler)]
        lg.addHandler(console)
    add_file_handler("Maker", "maker.log", handler_cls=PrependFileHandler)
    add_file_handler("Hedge", "hedge.log", handler_cls=PrependFileHandler)
    add_file_handler("Spread", "spread.log", handler_cls=PrependFileHandler)
    # Trades logger: prepend rows but keep header on top
    trades_header = (
        "timestamp,reason,dir,qty,spread_signal,spread_filled,inv_before,inv_after,"
        "venue_L,ob_price_L,exec_price_L,fill_price_L,order_latency_L,fill_latency_L,slippage_L,"
        "venue_S,ob_price_S,exec_price_S,fill_price_S,order_latency_S,fill_latency_S,slippage_S"
    )
    trades_handler = PrependCsvHandler(log_dir / "trades.csv", header=trades_header)
    trades_handler.setLevel(logging.INFO)
    trades_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("Trades").addHandler(trades_handler)
    rt_handler = OverwriteFileHandler(log_dir / "realtime.log")
    rt_handler.setLevel(logging.INFO)
    rt_handler.setFormatter(formatter)
    logging.getLogger("RealtimeSpread").addHandler(rt_handler)
    logging.getLogger("Maker").propagate = False
    logging.getLogger("Hedge").propagate = False
    logging.getLogger("RealtimeSpread").propagate = False
    logging.getLogger("Spread").propagate = False
    logging.getLogger("Trades").propagate = False
    logging.getLogger("ExtendedWS").propagate = False
    logging.getLogger("LighterWS").propagate = False
    logging.getLogger("RealtimeOB_L").addHandler(rt_handler)
    logging.getLogger("RealtimeOB_L").propagate = False
    logging.getLogger("RealtimeOB_E").addHandler(rt_handler)
    logging.getLogger("RealtimeOB_E").propagate = False
    logging.getLogger("RealtimeDecision").addHandler(rt_handler)
    logging.getLogger("RealtimeDecision").propagate = False
    # shared state and venues
    state = State()
    state.last_ob_ts = None
    # configure TT consecutive hits from config/env (default 3)
    state.tt_min_hits = int(cfg_item.get("MIN_HITS", os.getenv("MIN_HITS", "3")))
    sig_cap = cfg_item.get("MAX_TRADES", os.getenv("MAX_TRADES", None))
    if sig_cap is not None:
        try:
            state.signals_remaining = int(sig_cap)
        except Exception:
            state.signals_remaining = None
    spread_log_all = True  # removed; dedup controls logging now
    state.dedup_ob = str(cfg_item.get("DEDUP_OB", os.getenv("DEDUP_OB", "false"))).lower() == "true"
    L = LighterWS(symbolL)
    E = ExtendedWS(symbolE)
    L.dedup_ob = state.dedup_ob
    E.dedup_ob = state.dedup_ob
    # no hedge runner in TT-only mode; allow maker to run immediately
    state.hedge_seeded = True

    # TT bot (test_mode controls live orders)
    maker_bot = TTBot(
        state=state,
        lighter=L,
        extended=E,
        symbolL=symbolL,
        symbolE=symbolE,
        minSpread=float(cfg_item.get("MIN_SPREAD", 0.4)),
        spreadTP=float(cfg_item.get("SPREAD_TP", 0.2)),
        repriceTick=float(cfg_item.get("REPRICE_TICK", 0)),
        order_value=float(cfg_item.get("ORDER_VALUE", 25)),
        max_position_value=cfg_item.get("MAX_POSITION_VALUE", 500),  # cap in quote terms
        test_mode=bool(cfg_item.get("TEST_MODE", False)),  # set False for live orders
    )

    async def maker_loop():
        def on_update():
            state.last_ob_ts = time.time()
            if L.ob["bidPrice"] and L.ob["askPrice"] and E.ob["bidPrice"] and E.ob["askPrice"]:
                spreads = calc_spreads(L, E, state)
                snap = (
                    L.ob["bidPrice"], L.ob["bidSize"], L.ob["askPrice"], L.ob["askSize"],
                    E.ob["bidPrice"], E.ob["bidSize"], E.ob["askPrice"], E.ob["askSize"],
                    spreads.get("TT_LE"), spreads.get("TT_EL"),
                    spreads.get("MT_LE"), spreads.get("MT_EL"),
                    spreads.get("TM_LE"), spreads.get("TM_EL"),
                )
                should_log = True
                if state.dedup_ob and state.last_spread_snapshot == snap:
                    should_log = False
                if should_log:
                    logging.getLogger("Spread").info(
                        f"ts={state.last_ob_ts} "
                        f"TT_LE={spreads.get('TT_LE')} TT_EL={spreads.get('TT_EL')} "
                        f"MT_LE={spreads.get('MT_LE')} MT_EL={spreads.get('MT_EL')} "
                        f"TM_LE={spreads.get('TM_LE')} TM_EL={spreads.get('TM_EL')} "
                        f"L bid={L.ob['bidPrice']} bs={L.ob['bidSize']} ask={L.ob['askPrice']} as={L.ob['askSize']} "
                        f"E bid={E.ob['bidPrice']} bs={E.ob['bidSize']} ask={E.ob['askPrice']} as={E.ob['askSize']}"
                    )
                    state.last_spread_snapshot = snap
            asyncio.create_task(maker_bot.loop())
        L.set_ob_callback(on_update)
        E.set_ob_callback(on_update)
        # inventory and entry price updates (taker+maker fills) with logging
        def _inv_spread():
            l_qty, e_qty = state.invL, state.invE
            l_entry, e_entry = getattr(state, "entry_price_L", 0), getattr(state, "entry_price_E", 0)
            spread_inv = 0.0
            if l_qty > 0 and e_qty < 0 and l_entry:
                spread_inv = (e_entry - l_entry) / l_entry * 100
            elif l_qty < 0 and e_qty > 0 and e_entry:
                spread_inv = (l_entry - e_entry) / e_entry * 100
            return spread_inv

        def log_inv():
            logging.getLogger("Maker").info(
                f"[INV] L:{state.invL}@{getattr(state,'entry_price_L',0)} | "
                f"E:{state.invE}@{getattr(state,'entry_price_E',0)} | "
                f"Δ:{_inv_spread():.4f}%"
            )

        def on_inv_l(delta):
            state.invL += delta
            # clear pending TT immediately on fills
            if getattr(maker_bot, "_pending_tt", None) is None:
                maker_bot._pending_tt = {}
            maker_bot._pending_tt["L"] = maker_bot._pending_tt.get("L", 0.0) - delta
            ctx_qty = getattr(state, "last_trade_ctx", {}).get("qty") or 0.0
            base_tol = getattr(maker_bot, "_pending_tol", 1e-3)
            tol = max(base_tol, abs(ctx_qty) * 1e-4)  # allow minor fill-size drift
            order_lat = getattr(state, "last_send_latency_L", None)
            fill_lat = None
            ts = getattr(state, "last_send_ts_L", None)
            if ts:
                # ts stored as perf_counter at send time
                fill_lat = (time.perf_counter() - ts) * 1000
                state.last_send_ts_L = None
            entry = getattr(state, "entry_price_L", 0)
            last_px = getattr(state, "last_fill_price_L", getattr(L, "last_fill_price", entry))
            olat = f"{order_lat:.0f}" if order_lat is not None else "N/A"
            flat = f"{fill_lat:.0f}" if fill_lat is not None else "N/A"
            state.last_fill_price_L = last_px
            state.last_fill_latency_L = fill_lat
            logging.getLogger("Maker").info(
                f"[FILLED] venue=L qty={delta} price={last_px} order_latency={olat} fill_latency={flat}"
            )
            log_inv()
            if abs(maker_bot._pending_tt.get("L", 0.0)) < tol:
                maker_bot._pending_tt["L"] = 0.0
            if abs(maker_bot._pending_tt.get("E", 0.0)) < tol:
                maker_bot._pending_tt["E"] = 0.0
            if maker_bot._pending_tt and all(abs(v) < tol for v in maker_bot._pending_tt.values()):
                maker_bot._log_trade_complete()
                maker_bot._pending_tt = None
                state.last_send_latency_L = None
                state.last_send_latency_E = None
            elif getattr(state, "last_trade_ctx", None) and (maker_bot._pending_tt is None or not maker_bot._pending_tt):
                maker_bot._log_trade_complete()
                maker_bot._pending_tt = None
                state.last_send_latency_L = None
                state.last_send_latency_E = None
        def on_inv_e(delta):
            state.invE += delta
            if getattr(maker_bot, "_pending_tt", None) is None:
                maker_bot._pending_tt = {}
            maker_bot._pending_tt["E"] = maker_bot._pending_tt.get("E", 0.0) - delta
            ctx_qty = getattr(state, "last_trade_ctx", {}).get("qty") or 0.0
            base_tol = getattr(maker_bot, "_pending_tol", 1e-3)
            tol = max(base_tol, abs(ctx_qty) * 1e-4)  # allow minor fill-size drift
            order_lat = getattr(state, "last_send_latency_E", None)
            fill_lat = None
            ts = getattr(state, "last_send_ts_E", None)
            if ts:
                # ts stored as perf_counter at send time
                fill_lat = (time.perf_counter() - ts) * 1000
                state.last_send_ts_E = None
            entry = getattr(state, "entry_price_E", 0)
            last_px = getattr(state, "last_fill_price_E", getattr(E, "last_fill_price", entry))
            olat = f"{order_lat:.0f}" if order_lat is not None else "N/A"
            flat = f"{fill_lat:.0f}" if fill_lat is not None else "N/A"
            state.last_fill_price_E = last_px
            state.last_fill_latency_E = fill_lat
            logging.getLogger("Maker").info(
                f"[FILLED] venue=E qty={delta} price={last_px} order_latency={olat} fill_latency={flat}"
            )
            log_inv()
            if abs(maker_bot._pending_tt.get("L", 0.0)) < tol:
                maker_bot._pending_tt["L"] = 0.0
            if abs(maker_bot._pending_tt.get("E", 0.0)) < tol:
                maker_bot._pending_tt["E"] = 0.0
            if maker_bot._pending_tt and all(abs(v) < tol for v in maker_bot._pending_tt.values()):
                maker_bot._log_trade_complete()
                maker_bot._pending_tt = None
                state.last_send_latency_L = None
                state.last_send_latency_E = None
            elif getattr(state, "last_trade_ctx", None) and (maker_bot._pending_tt is None or not maker_bot._pending_tt):
                maker_bot._log_trade_complete()
                maker_bot._pending_tt = None
                state.last_send_latency_L = None
                state.last_send_latency_E = None
        L.set_inventory_callback(on_inv_l)
        E.set_inventory_callback(on_inv_e)
        L.set_position_state_callback(lambda qty, entry: setattr(state, "entry_price_L", entry))
        E.set_position_state_callback(lambda qty, entry: setattr(state, "entry_price_E", entry))
        # load initial positions before starting streams
        l_qty, l_entry = await L.load_initial_position()
        e_qty, e_entry = await E.load_initial_position()
        state.invL = l_qty
        state.entry_price_L = l_entry
        state.invE = e_qty
        state.entry_price_E = e_entry
        logging.getLogger("Maker").info(
            f"[INIT] L:{l_qty}@{l_entry} | E:{e_qty}@{e_entry} | Δ:{_inv_spread():.4f}%"
        )
        await asyncio.gather(L.start(), E.start())

    await maker_loop()


if __name__ == "__main__":
    asyncio.run(main())
