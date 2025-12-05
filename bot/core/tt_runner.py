# ~/QUANTING.FUN/.venv/bin/python -m bot.core.tt_runner BTC BTC-USD


import asyncio
import logging
import os
import time
import json
from pathlib import Path

from bot.common.state import State
from bot.common.calc_spreads import calc_spreads
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_extended import ExtendedWS
from bot.core.tt_bot import TTBot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOT_ROOT = PROJECT_ROOT / "bot"
LOG_ROOT = BOT_ROOT / "logs"
os.chdir(PROJECT_ROOT)


def _load_env():
    for fname in [".env_bot", ".env_server"]:
        env_path = PROJECT_ROOT / fname
        if not env_path.exists():
            continue
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
    cfg_path = PROJECT_ROOT / "config.json"
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

    # basic console logging only (no file writes)
    formatter = logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    for name in ["_TT", "Hedge", "Trades", "LIG", "EXT", "DecisionDB"]:
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.handlers = [h for h in lg.handlers if not isinstance(h, logging.StreamHandler)]
        lg.addHandler(console)
        lg.propagate = False
    # silence verbose library debug noise
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("x10").setLevel(logging.WARNING)
    logging.getLogger("x10.utils.http").setLevel(logging.WARNING)
    logging.getLogger().setLevel(logging.INFO)

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
    state.warm_up_orders = str(cfg_item.get("WARM_UP_ORDERS", os.getenv("WARM_UP_ORDERS", "false"))).lower() == "true"
    state.warm_up_stage = "LE_PENDING" if state.warm_up_orders else "DONE"
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
        max_position_value=cfg_item.get("MAX_POSITION_VALUE", 500),  # cap in quote terms
        max_trade_value=cfg_item.get("MAX_TRADE_VALUE", None),
        max_of_ob=float(cfg_item.get("MAX_OF_OB", 0.3)),
        test_mode=bool(cfg_item.get("TEST_MODE", False)),  # set False for live orders
    )
    logging.getLogger("_TT").info(
        "[CONFIG] L=%s E=%s minSpread=%s spreadTP=%s repriceTick=%s "
        "maxPosValue=%s minHits=%s dedup=%s warmUp=%s maxTrades=%s",
        symbolL,
        symbolE,
        maker_bot.minSpread,
        maker_bot.spreadTP,
        maker_bot.repriceTick,
        maker_bot.max_position_value,
        state.tt_min_hits,
        state.dedup_ob,
        state.warm_up_orders,
        getattr(state, "signals_remaining", None),
    )

    async def maker_loop():
        def maybe_finalize_trade():
            """Finalize trade once both legs are filled (or pending is empty)."""
            ctx = getattr(state, "last_trade_ctx", None)
            if not ctx:
                return
            # consider pending TT state; treat None or all-zero as complete
            pending = getattr(maker_bot, "_pending_tt", None) or {}
            # use qty-aware tolerance so tiny sizes don't finish early/late
            qty_ctx = abs(ctx.get("qty") or 0.0)
            tol_local = max(getattr(maker_bot, "_pending_tol", 1e-6), qty_ctx * 1e-4)
            if pending and any(abs(v) > tol_local for v in pending.values()):
                logging.getLogger("Maker").debug(
                    f"[PENDING] trace={ctx.get('trace')} pending={pending} tol={tol_local}"
                )
                return
            try:
                logging.getLogger("Maker").info(
                    f"[FILLED] TT finalize trace={ctx.get('trace')} pending={pending} tol={tol_local}"
                )
                maker_bot._log_trade_complete()
            finally:
                maker_bot._pending_tt = None
                state.last_send_latency_L = None
                state.last_send_latency_E = None

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
                    # quiet spread logger; rely on in-place print from logic_entry_exit
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
            prev_qty = state.invL
            prev_entry = getattr(state, "entry_price_L", 0)
            state.invL += delta
            if abs(state.invL) < 1e-9:
                state.invL = 0.0
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
            last_px = getattr(state, "last_fill_price_L", None)
            if last_px is None:
                last_px = getattr(L, "last_fill_price", None)
            if last_px is None:
                last_px = getattr(state, "last_exec_price_L", None)
            olat = f"{order_lat:.0f}" if order_lat is not None else "N/A"
            flat = f"{fill_lat:.0f}" if fill_lat is not None else "N/A"
            state.last_fill_price_L = last_px
            state.last_fill_latency_L = fill_lat
            # weighted avg entry price update
            new_qty = state.invL
            if new_qty == 0 or last_px is None:
                state.entry_price_L = 0
            elif prev_qty == 0 or (prev_qty > 0 > new_qty) or (prev_qty < 0 < new_qty):
                state.entry_price_L = last_px
            else:
                try:
                    state.entry_price_L = (prev_qty * prev_entry + delta * last_px) / new_qty
                except Exception:
                    state.entry_price_L = last_px
            logging.getLogger("Maker").info(
                f"[FILLED] venue=L qty={delta} price={last_px} order_latency={olat} fill_latency={flat}"
            )
            log_inv()
            if abs(maker_bot._pending_tt.get("L", 0.0)) < tol:
                maker_bot._pending_tt["L"] = 0.0
            if abs(maker_bot._pending_tt.get("E", 0.0)) < tol:
                maker_bot._pending_tt["E"] = 0.0
            maybe_finalize_trade()
        def on_inv_e(delta):
            prev_qty = state.invE
            prev_entry = getattr(state, "entry_price_E", 0)
            state.invE += delta
            if abs(state.invE) < 1e-9:
                state.invE = 0.0
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
            last_px = getattr(state, "last_fill_price_E", None)
            if last_px is None:
                last_px = getattr(E, "last_fill_price", None)
            if last_px is None:
                last_px = getattr(state, "last_exec_price_E", None)
            olat = f"{order_lat:.0f}" if order_lat is not None else "N/A"
            flat = f"{fill_lat:.0f}" if fill_lat is not None else "N/A"
            state.last_fill_price_E = last_px
            state.last_fill_latency_E = fill_lat
            new_qty = state.invE
            if new_qty == 0 or last_px is None:
                state.entry_price_E = 0
            elif prev_qty == 0 or (prev_qty > 0 > new_qty) or (prev_qty < 0 < new_qty):
                state.entry_price_E = last_px
            else:
                try:
                    state.entry_price_E = (prev_qty * prev_entry + delta * last_px) / new_qty
                except Exception:
                    state.entry_price_E = last_px
            logging.getLogger("Maker").info(
                f"[FILLED] venue=E qty={delta} price={last_px} order_latency={olat} fill_latency={flat}"
            )
            log_inv()
            if abs(maker_bot._pending_tt.get("L", 0.0)) < tol:
                maker_bot._pending_tt["L"] = 0.0
            if abs(maker_bot._pending_tt.get("E", 0.0)) < tol:
                maker_bot._pending_tt["E"] = 0.0
            maybe_finalize_trade()
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
        logging.getLogger("_TT").info(
            f"[INIT] L:{l_qty}@{l_entry} | E:{e_qty}@{e_entry} | Δ:{_inv_spread():.4f}%"
        )
        await asyncio.gather(L.start(), E.start())

    await maker_loop()


if __name__ == "__main__":
    asyncio.run(main())
