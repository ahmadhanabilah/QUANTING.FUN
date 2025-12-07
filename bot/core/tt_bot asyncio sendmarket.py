# PYTHONPATH=. .venv/bin/python -m bot.core.tt_bot BTC BTC-USD
import asyncio
import re
import time
import os
import uuid
import math
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import logging
import aiohttp
from bot.common.calc_spreads import calc_spreads
from bot.common.state import State
from bot.core.logic_entry_exit import logic_entry_exit
from bot.common.enums import ActionType, Venue, Side
from bot.common.decision import Decision
from bot.common.db_client import DBClient
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_extended import ExtendedWS
# from bot.common.event_bus import publish  # not needed yet


logger_tt           = logging.getLogger("_TT")
logger_db           = logging.getLogger("_DB")
SLIPPAGE_DEFAULT    = 0.04  # 4% hardcoded slippage
PROJECT_ROOT        = Path(__file__).resolve().parents[2]


class TTBot:
    def __init__(self, state, lighter, extended,
                 symbolL, symbolE,
                 minSpread, spreadTP,
                 max_position_value=None,
                 max_trade_value=None,
                 max_of_ob: float = 0.3):
        self.state              = state
        self.L                  = lighter
        self.E                  = extended
        self.symbolL            = symbolL
        self.symbolE            = symbolE
        self.minSpread          = minSpread
        self.spreadTP           = spreadTP
        self.max_position_value = max_position_value
        self.max_trade_value    = max_trade_value
        self.max_of_ob          = max_of_ob

        # Hard guard: if dollar cap is below venue min value, stop immediately
        try:
            min_value_l = getattr(self.L, "min_value", 0) or 0
            if self.max_trade_value is not None and min_value_l and self.max_trade_value < min_value_l:
                logger_tt.error(
                    f"MAX_TRADE_VALUE ({self.max_trade_value}) below Lighter min_value ({min_value_l}); stopping bot"
                )
                raise RuntimeError("max_trade_value below lighter min_value")
        except Exception:
            # if check fails unexpectedly, allow startup to continue
            pass

        self._exec_lock         = asyncio.Lock()
        self._last_spreads      = {}
        self._pending_tt        = None  # tracks pending TT fills per venue
        self._pending_db        = False
        self._trade_complete_logged = False
        # tolerance must be below TT qty but allow minor fill-size drift
        self._pending_tol       = 1e-6
        self._last_invL         = state.invL
        self._last_invE         = state.invE
        self._last_spreads      = {}
        self.bot_name           = f"TT:{self.symbolL}:{self.symbolE}"
        self.db_client          = None
        # track position WS updates so we can gate new trades until fresh snapshots land
        self._pos_seq           = {"L": 0, "E": 0}
        self._pos_wait_targets  = None
        self._waiting_for_positions = False
        self._pending_position_ctx = None
        self._streams_ready_logged = False

    async def loop(self):
        """Call this on every OB update."""
        # wait until hedge runner has seeded initial unhedged positions
        if not getattr(self.state, "hedge_seeded", True):
            return
        # require initial WS streams before trading
        if not self._streams_ready():
            if not self._streams_ready_logged:
                missing = ", ".join(self._streams_missing_flags())
                self._streams_ready_logged = True
            return
        else:
            self._streams_ready_logged = False
        if not getattr(self, "_ready_logged", False):
            logger_tt.info("[READY TO LOOP] positions synced for L/E; trading can proceed")
            self._ready_logged = True
        if self._waiting_for_positions and not self._positions_synced():
            return
        if self._waiting_for_positions and self._positions_synced():
            self._waiting_for_positions = False
            self._pos_wait_targets = None
            # if we delayed clearing trade context until positions arrived, flush update now
            ctx_pending = self._pending_position_ctx or getattr(self.state, "last_trade_ctx", None)
            if ctx_pending:
                inv_after_now = (
                    self.state.invL,
                    getattr(self.state, "entry_price_L", 0),
                    self.state.invE,
                    getattr(self.state, "entry_price_E", 0),
                )
                asyncio.create_task(
                    self._log_decision_db(
                        initial=False,
                        inv_after=inv_after_now,
                        fill_info=None,
                        ctx=dict(ctx_pending),
                    )
                )
                self._pending_position_ctx = None
                self.state.last_trade_ctx = None
                self._current_trace = None

        lbid = self.L.ob["bidPrice"]
        lask = self.L.ob["askPrice"]
        ebid = self.E.ob["bidPrice"]
        eask = self.E.ob["askPrice"]

        if not all([lbid, lask, ebid, eask]):
            return  # wait until both books valid

        # track inventory for diagnostics; pending TT reconciliation is handled in inventory callbacks
        self._last_invL = self.state.invL
        self._last_invE = self.state.invE
        # block new decisions while TT legs are outstanding; clear/finalize when zeroed
        if self._pending_tt:
            if any(abs(v) > self._pending_tol for v in self._pending_tt.values()):
                return
            # finalize trade if ctx still present (callbacks may have already done this)
            if getattr(self.state, "last_trade_ctx", None):
                logger_tt.info(
                    "[BOTH VENUES FILLED] "
                    f"L:{self.state.invL}@{getattr(self.state, 'priceInvL', getattr(self.state, 'entry_price_L', 0))} "
                    f"E:{self.state.invE}@{getattr(self.state, 'priceInvE', getattr(self.state, 'entry_price_E', 0))}"
                )
                await self._log_trade_complete()
            else:
                # context vanished (e.g., cleared after position update); still unblock
                self._pending_db = False
            logger_tt.info(
                "[BOTH VENUES FILLED] "
                f"L:{self.state.invL}@{getattr(self.state, 'priceInvL', getattr(self.state, 'entry_price_L', 0))} "
                f"E:{self.state.invE}@{getattr(self.state, 'priceInvE', getattr(self.state, 'entry_price_E', 0))}"
            )
            self._pending_tt = None
            self.state.last_send_latency_L = None
            self.state.last_send_latency_E = None
        if self._pending_db:
            return

        # 1) spreads
        spreads = calc_spreads(self.L, self.E, self.state)
        self._last_spreads = spreads

        # pre-compute TT size hints for pricing/logging
        size_hint_le = None
        size_hint_el = None
        try:
            size_hint_le = self._compute_tt_shared_size_pair("TT_LE")
            size_hint_el = self._compute_tt_shared_size_pair("TT_EL")
        except Exception:
            pass

        # 2) decision
        decision = logic_entry_exit(
            self.state,
            spreads,
            self.minSpread,
            self.spreadTP,
            self.L.ob,
            self.E.ob,
            tt_min_hits=getattr(self.state, "tt_min_hits", 3),
            size_hint_le=size_hint_le,
            size_hint_el=size_hint_el,
            max_position_value=self.max_position_value,
            signals_remaining=getattr(self.state, "signals_remaining", None),
        )

        # enforce TT-only execution; skip anything not a TT tuple (TT or warmup tags)
        allowed_reasons = {"TT_LE", "TT_EL", "WARM_UP_LE", "WARM_UP_EL"}
        if not (isinstance(decision, tuple) and all(getattr(d, "reason", None) in allowed_reasons for d in decision)):
            return
        # stash trade context before execution
        signal_perf = None
        signal_wall = None
        if isinstance(decision, tuple):
            signal_perf = time.perf_counter()
            signal_wall = time.time()
            self.state.last_trade_ctx = {
                "ts": signal_wall,
                "reason": getattr(decision[0], "reason", None),
                "dir": getattr(decision[0], "direction", None),
                "spread_signal": self._last_spreads.get(getattr(decision[0], "reason", ""), None),
                "ob_price_L": self.L.ob["askPrice"] if decision[0].side == Side.LONG else self.L.ob["bidPrice"],
                "ob_price_E": self.E.ob["askPrice"] if decision[1].side == Side.LONG else self.E.ob["bidPrice"],
                "exec_price_L": None,
                "exec_price_E": None,
                "qty": getattr(decision[0], "_tt_size", None) or getattr(decision[1], "_tt_size", None),
                "inv_before": (
                    self.state.invL,
                    getattr(self.state, "entry_price_L", 0),
                    self.state.invE,
                    getattr(self.state, "entry_price_E", 0),
                ),
                "trace": uuid.uuid4().hex,
                "signal_perf": signal_perf,
            }
            setattr(self.state, "last_signal_perf", signal_perf)
            setattr(self.state, "last_send_ts_L", signal_perf)
            setattr(self.state, "last_send_ts_E", signal_perf)
            self._current_trace = self.state.last_trade_ctx["trace"]
            self._trade_complete_logged = False
        # carry trace onto decisions so legs keep same identifier even if ctx mutates
        trace_val = self.state.last_trade_ctx["trace"]
        try:
            if isinstance(decision, tuple):
                for d in decision:
                    setattr(d, "_trace", trace_val)
            else:
                setattr(decision, "_trace", trace_val)
        except Exception:
            pass
        # log initial decision immediately so DB has a row even if fills/logs lag
        try:
            ctx_snapshot = dict(self.state.last_trade_ctx)
            asyncio.create_task(self._log_decision_db(initial=True, ctx=ctx_snapshot))
        except Exception:
            logger_db.exception("[ERROR INSERT DECISION]")
        warm_reason = getattr(decision[0], "reason", None) if isinstance(decision, tuple) else None
        warm_stage = getattr(self.state, "warm_up_stage", "DONE")
        if warm_reason == "WARM_UP_LE" and warm_stage == "LE_PENDING":
            self.state.warm_up_stage = "LE_INFLIGHT"
        elif warm_reason == "WARM_UP_EL" and warm_stage == "EL_PENDING":
            self.state.warm_up_stage = "EL_INFLIGHT"
        if warm_reason in ("WARM_UP_LE", "WARM_UP_EL"):
            # log initial decision trace before fills
            ctx_snapshot = dict(self.state.last_trade_ctx) if getattr(self.state, "last_trade_ctx", None) else None
            asyncio.create_task(self._log_decision_db(initial=True, ctx=ctx_snapshot))

        # if capped, decrement once when we have a non-NONE decision
        def _consume_signal_if_needed(dec):
            if getattr(self.state, "signals_remaining", None) is None:
                return
            # don't count warm-up legs against the cap
            def _is_warm(d):
                return isinstance(d, Decision) and getattr(d, "reason", None) in ("WARM_UP_LE", "WARM_UP_EL")
            if isinstance(dec, Decision) and dec.action_type != ActionType.NONE:
                if not _is_warm(dec):
                    self.state.signals_remaining = max(self.state.signals_remaining - 1, 0)
            elif isinstance(dec, tuple):
                # count tuple as one signal if any action is non-NONE
                if any(d.action_type != ActionType.NONE for d in dec) and not any(_is_warm(d) for d in dec):
                    self.state.signals_remaining = max(self.state.signals_remaining - 1, 0)

        _consume_signal_if_needed(decision)

        # 5) execute
        async with self._exec_lock:
            if isinstance(decision, tuple):
                if getattr(self.state, "last_trade_ctx", None) is None:
                    self.state.last_trade_ctx = {
                        "trace": uuid.uuid4().hex,
                        "ts": time.time(),
                        "signal_perf": time.perf_counter(),
                        "reason": getattr(decision[0], "reason", None),
                        "dir": getattr(decision[0], "direction", None),
                        "spread_signal": self._last_spreads.get(getattr(decision[0], "reason", ""), None),
                        "ob_price_L": None,
                        "ob_price_E": None,
                        "exec_price_L": None,
                        "exec_price_E": None,
                        "qty": getattr(decision[0], "_tt_size", None) or getattr(decision[1], "_tt_size", None),
                        "inv_before": (
                            self.state.invL,
                            getattr(self.state, "entry_price_L", 0),
                            self.state.invE,
                            getattr(self.state, "entry_price_E", 0),
                        ),
                    }
                    self._trade_complete_logged = False
                reasons = [getattr(d, "reason", None) for d in decision]
                if all(r in ("TT_LE", "TT_EL") for r in reasons):
                    shared = self._compute_tt_shared_size_pair(reasons[0])
                    ob_prices = []
                    exec_prices = []
                    def _slip_factor(_venue_api):
                        return SLIPPAGE_DEFAULT
                    spread_val = self._last_spreads.get(reasons[0]) if self._last_spreads else None
                    dir_label = getattr(decision[0], "direction", None)
                    if reasons[0] == "TT_LE":
                        hist = getattr(self.state, "tt_le_exit_history" if dir_label == "exit" else "tt_le_history", []) or []
                    else:
                        hist = getattr(self.state, "tt_el_exit_history" if dir_label == "exit" else "tt_el_history", []) or []
                    hist_str = "[" + ", ".join(f"{h.get('spread', 0):.4f}" for h in hist) + "]" if hist else "[]"
                    spread_str = f"{spread_val:.2f}%" if spread_val is not None else "N/A"
                    for d in decision:
                        venue_api = self.L if d.venue.name == "L" else self.E
                        px_ob = venue_api.ob["askPrice"] if d.side.name == "LONG" else venue_api.ob["bidPrice"]
                        ob_prices.append(px_ob)
                        exec_px = px_ob
                        slip = _slip_factor(venue_api)
                        if slip and px_ob:
                            if d.side.name == "LONG":
                                exec_px = px_ob * (1 + slip)
                            else:
                                exec_px = px_ob * (1 - slip)
                        exec_prices.append(exec_px)
                        if shared:
                            setattr(d, "_tt_size", shared)
                        # keep ctx in sync for DB/logs
                        if d.venue.name == "L":
                            self.state.last_trade_ctx["ob_price_L"] = px_ob
                            self.state.last_trade_ctx["exec_price_L"] = exec_px
                        else:
                            self.state.last_trade_ctx["ob_price_E"] = px_ob
                            self.state.last_trade_ctx["exec_price_E"] = exec_px
                    qty_log = shared or "N/A"
                    # detailed consecutive snapshot already stored per hit
                    cons_list = hist
                    trace_val = getattr(self, "_current_trace", None) or getattr(self.state, "last_trade_ctx", {}).get("trace")
                    logger_tt.info(f"[DECISION MADE] {trace_val} {reasons[0]} {dir_label}")
                    # reset counters after firing
                    self.state.tt_le_hits = 0
                    self.state.tt_el_hits = 0
                    self.state.tt_le_history = []
                    self.state.tt_el_history = []
                    self.state.tt_le_exit_history = []
                    self.state.tt_el_exit_history = []
                self._pending_db = True
                ctx_snapshot = dict(self.state.last_trade_ctx) if getattr(self.state, "last_trade_ctx", None) else None
                trace_for_trades = getattr(self, "_current_trace", None) or (ctx_snapshot or {}).get("trace") or "unknown"
                ts_for_trades = (ctx_snapshot or {}).get("ts") or time.time()
                send_tasks = [asyncio.create_task(self._execute_single(d, log_decision=True)) for d in decision]
                log_task = asyncio.create_task(self._log_decision_db(initial=True, ctx=ctx_snapshot)) if ctx_snapshot else None
                all_tasks = send_tasks + ([log_task] if log_task else [])
                results = await asyncio.gather(*all_tasks, return_exceptions=True)
                send_results = results[: len(send_tasks)]
                log_result = results[-1] if log_task else None
                if isinstance(log_result, Exception):
                    logger_db.warning(f"[ERROR INSERT DECISION] {log_result}")
                for res in send_results:
                    if not res or isinstance(res, Exception):
                        continue
                    trace_val = trace_for_trades
                    ts_val = ts_for_trades
                    await self._push_trade_db(
                        trace=trace_val,
                        ts=ts_val,
                        venue=res["venue"],
                        size=res["size"],
                        ob_price=res["ob_price"],
                        exec_price=res["exec_price"],
                        lat_order=res["lat_order"],
                        reason=res["reason"],
                        direction=res["direction"],
                        status=res["status"],
                        payload=res.get("payload"),
                        resp=res.get("resp"),
                    )
            else:
                await self._execute_single(decision)

    def _format_inv_line(self):
        l_qty = getattr(self.state, "invL", 0.0)
        e_qty = getattr(self.state, "invE", 0.0)
        l_entry = getattr(self.state, "entry_price_L", 0.0)
        e_entry = getattr(self.state, "entry_price_E", 0.0)
        delta_qty = l_qty + e_qty
        delta_entry = 0.0
        if l_qty and e_qty:
            try:
                delta_entry = ((abs(l_qty) * l_entry) - (abs(e_qty) * e_entry)) / (abs(l_qty) + abs(e_qty))
            except Exception:
                delta_entry = 0.0
        inv_spread = 0.0
        if l_qty > 0 and e_qty < 0 and l_entry:
            inv_spread = (e_entry - l_entry) / l_entry * 100
        elif l_qty < 0 and e_qty > 0 and e_entry:
            inv_spread = (l_entry - e_entry) / e_entry * 100
        arrow = "▲" if delta_qty > 0 else ("▼" if delta_qty < 0 else "■")
        arrow_price = "▲" if delta_entry > 0 else ("▼" if delta_entry < 0 else "■")
        def _fmt_price(p):
            try:
                return f"{p:.6f}"
            except Exception:
                return str(p)
        return (
            f"[INV] L:{l_qty} E:{e_qty} {arrow}:{delta_qty} | "
            f"L:{_fmt_price(l_entry)} E:{_fmt_price(e_entry)} {arrow_price}:{inv_spread:.2f}%"
        )

    def _positions_synced(self) -> bool:
        if not self._pos_wait_targets:
            return True
        try:
            for venue_key, target in self._pos_wait_targets.items():
                if self._pos_seq.get(venue_key, 0) < target:
                    return False
            return True
        except Exception:
            return False

    def _streams_ready(self) -> bool:
        """Check that both venue WS feeds have delivered initial messages."""
        try:
            lighter_ready = (
                getattr(self.L, "_got_first_ob", False)
                and getattr(self.L, "_got_first_positions", False)
                and getattr(self.L, "_got_first_trades", False)
            )
            extended_ready = (
                getattr(self.E, "_got_first_ob", False)
                and getattr(self.E, "_got_first_acc", False)
                and getattr(self.E, "_has_account_position", False)
            )
            return lighter_ready and extended_ready
        except Exception:
            return False

    def _streams_missing_flags(self):
        """Return list of missing readiness flags for logging."""
        missing = []
        if not getattr(self.L, "_got_first_ob", False):
            missing.append("L:OB")
        if not getattr(self.L, "_got_first_trades", False):
            missing.append("L:ALL_TRADES")
        if not getattr(self.L, "_got_first_positions", False):
            missing.append("L:ALL_POSITIONS")
        if not getattr(self.E, "_got_first_ob", False):
            missing.append("E:OB")
        if not getattr(self.E, "_got_first_acc", False):
            missing.append("E:Acc")
        if not getattr(self.E, "_has_account_position", False):
            missing.append("E:Position")
        return missing

    def _on_position_update(self, venue_key: str, qty: float, entry: float):
        """Handle WS position snapshots to sync state + advance wait gates."""
        try:
            if venue_key == "L":
                self.state.invL = qty
                self.state.entry_price_L = entry
                self.state.priceInvL = entry
            elif venue_key == "E":
                self.state.invE = qty
                self.state.entry_price_E = entry
                self.state.priceInvE = entry
            self._pos_seq[venue_key] = self._pos_seq.get(venue_key, 0) + 1
        except Exception:
            pass
        if self._waiting_for_positions and self._positions_synced():
            self._waiting_for_positions = False
            self._pos_wait_targets = None
        # if we were holding a decision context waiting for position prices, flush update now
        if self._waiting_for_positions and self._positions_synced():
            self._waiting_for_positions = False
            self._pos_wait_targets = None
            ctx_pending = self._pending_position_ctx or getattr(self.state, "last_trade_ctx", None)
            if ctx_pending:
                inv_after_now = (
                    self.state.invL,
                    getattr(self.state, "entry_price_L", 0),
                    self.state.invE,
                    getattr(self.state, "entry_price_E", 0),
                )
                asyncio.create_task(
                    self._log_decision_db(
                        initial=False,
                        inv_after=inv_after_now,
                        fill_info=None,
                        ctx=dict(ctx_pending),
                    )
                )
                self._pending_position_ctx = None
                # clear trade context after we emit the awaited position update
                self.state.last_trade_ctx = None
                self._current_trace = None

    def _mark_wait_for_positions(self):
        """Expect next position snapshots before allowing further trades."""
        try:
            self._pos_wait_targets = {
                "L": self._pos_seq.get("L", 0) + 1,
                "E": self._pos_seq.get("E", 0) + 1,
            }
            self._waiting_for_positions = True
        except Exception:
            # fail-open if tracking fails to avoid deadlock
            self._waiting_for_positions = False
            self._pos_wait_targets = None

    async def _log_trade_complete(self, ctx_snapshot: dict | None = None):
        if self._trade_complete_logged:
            return
        self._trade_complete_logged = True
        ctx = ctx_snapshot or getattr(self.state, "last_trade_ctx", None)
        if not ctx:
            return
        # gather fills
        fill_L = getattr(self.state, "last_fill_price_L", None)
        fill_E = getattr(self.state, "last_fill_price_E", None)
        # fallback to aggressive prices sent if fill not captured
        if fill_L is None:
            fill_L = ctx.get("exec_price_L") or getattr(self.state, "last_exec_price_L", None) or ctx.get("ob_price_L")
        if fill_E is None:
            fill_E = ctx.get("exec_price_E") or getattr(self.state, "last_exec_price_E", None) or ctx.get("ob_price_E")
        olat_L = getattr(self.state, "last_send_latency_L", None)
        olat_E = getattr(self.state, "last_send_latency_E", None)
        flat_L = getattr(self.state, "last_fill_latency_L", None)
        flat_E = getattr(self.state, "last_fill_latency_E", None)
        inv_before = ctx.get("inv_before", (0, 0, 0, 0))
        inv_after = (
            self.state.invL,
            getattr(self.state, "entry_price_L", 0),
            self.state.invE,
            getattr(self.state, "entry_price_E", 0),
        )
        # clear entry prices when flat so logs don't show stale prices
        if inv_after[0] == 0:
            self.state.entry_price_L = 0
            self.state.priceInvL = 0
            inv_after = (0, 0, inv_after[2], inv_after[3])
        if inv_after[2] == 0:
            self.state.entry_price_E = 0
            self.state.priceInvE = 0
            inv_after = (inv_after[0], inv_after[1], 0, 0)
        qty_val = ctx.get("qty")
        if qty_val is None:
            try:
                l_before, _, e_before, _ = inv_before
                l_after, _, e_after, _ = inv_after
                delta_l = abs(l_after - l_before)
                delta_e = abs(e_after - e_before)
                qty_val = max(delta_l, delta_e)
            except Exception:
                qty_val = None
        def fmt_inv(inv_tuple):
            lq, le, eq, ee = inv_tuple
            spread_inv = 0.0
            if lq > 0 and eq < 0 and le:
                spread_inv = (ee - le) / le * 100
            elif lq < 0 and eq > 0 and ee:
                spread_inv = (le - ee) / ee * 100
            return f"L:{lq}@{le} | E:{eq}@{ee} | Δ -> {spread_inv:.2f}%"

        # compute filled spread using fill prices
        spread_filled = None
        if ctx.get("reason") == "TT_LE" and fill_L and fill_E:
            spread_filled = (fill_E - fill_L) / fill_L * 100
        if ctx.get("reason") == "TT_EL" and fill_L and fill_E:
            spread_filled = (fill_L - fill_E) / fill_E * 100
        # fallback to signal spread if fills missing (skip in test mode)
        if spread_filled is None:
            spread_filled = ctx.get("spread_signal")

        def fmt_price(val):
            try:
                return f"{float(val):.6f}"
            except Exception:
                return str(val)

        def fmt_lat(val):
            if val is None:
                return ""
            try:
                return f"{float(val):.0f}"
            except Exception:
                return str(val)
        def fmt_slip(val):
            if val is None:
                return ""
            try:
                return f"{float(val):.2f}"
            except Exception:
                return str(val)
        def fmt_spread(val):
            if val is None:
                return ""
            try:
                return f"{float(val):.2f}"
            except Exception:
                return str(val)

        # determine long/short legs for slippage + CSV ordering
        reason = ctx.get("reason")
        if reason == "TT_LE":
            venue_long, venue_short = "L", "E"
            ob_long, ob_short = ctx.get("ob_price_L"), ctx.get("ob_price_E")
            exec_long, exec_short = ctx.get("exec_price_L"), ctx.get("exec_price_E")
            fill_long, fill_short = fill_L, fill_E
            lat_order_long, lat_order_short = olat_L, olat_E
            lat_fill_long, lat_fill_short = flat_L, flat_E
        else:
            venue_long, venue_short = "E", "L"
            ob_long, ob_short = ctx.get("ob_price_E"), ctx.get("ob_price_L")
            exec_long, exec_short = ctx.get("exec_price_E"), ctx.get("exec_price_L")
            fill_long, fill_short = fill_E, fill_L
            lat_order_long, lat_order_short = olat_E, olat_L
            lat_fill_long, lat_fill_short = flat_E, flat_L

        def _entry_from_account(helper, fallback_entry):
            try:
                if getattr(helper, "_has_account_position", False):
                    entry_val = getattr(helper, "position_entry", None)
                    if entry_val is not None:
                        return entry_val
            except Exception:
                pass
            return fallback_entry

        # Prefer WS account feed prices for inventory valuation when available
        entry_l_account = _entry_from_account(self.L, getattr(self.state, "entry_price_L", 0))
        entry_e_account = _entry_from_account(self.E, getattr(self.state, "entry_price_E", 0))
        self.state.entry_price_L = entry_l_account
        self.state.entry_price_E = entry_e_account
        self.state.priceInvL = entry_l_account
        self.state.priceInvE = entry_e_account
        inv_after = (
            self.state.invL,
            entry_l_account,
            self.state.invE,
            entry_e_account,
        )

        slippage_long = None
        slippage_short = None
        if ob_long and fill_long:
            slippage_long = (fill_long - ob_long) / ob_long * 100
        if ob_short and fill_short:
            # positive means better fill for short
            slippage_short = (ob_short - fill_short) / ob_short * 100

        ts_readable = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ctx.get("ts", time.time())))
        spread_filled_str = fmt_spread(spread_filled)
        row = (
            f"{ts_readable},"
            f"{ctx.get('reason')},{ctx.get('dir')},{qty_val},"
            f"{fmt_spread(ctx.get('spread_signal'))},"
            f"{spread_filled_str},"
            f"\"{fmt_inv(inv_before)}\","
            f"\"{fmt_inv(inv_after)}\","
            f"{venue_long},{fmt_price(ob_long)},{fmt_price(exec_long)},{fmt_price(fill_long)},{fmt_lat(lat_order_long)},{fmt_lat(lat_fill_long)},{fmt_slip(slippage_long)},"
            f"{venue_short},{fmt_price(ob_short)},{fmt_price(exec_short)},{fmt_price(fill_short)},{fmt_lat(lat_order_short)},{fmt_lat(lat_fill_short)},{fmt_slip(slippage_short)}"
        )
        # CSV logging disabled (no Trades logger output)
        # advance warmup stage if applicable
        try:
            if getattr(self.state, "warm_up_orders", False):
                if ctx.get("reason") == "WARM_UP_LE":
                    self.state.warm_up_stage = "EL_PENDING"
                elif ctx.get("reason") == "WARM_UP_EL":
                    self.state.warm_up_stage = "DONE"
        except Exception:
            pass
        # push fills to DB if available
        trace_val = getattr(self, "_current_trace", None) or ctx.get("trace")
        ts_val = ctx.get("ts", time.time())
        fill_info = {
            "L": {"price": fill_L, "lat_order": olat_L, "lat_fill": flat_L, "qty": qty_val},
            "E": {"price": fill_E, "lat_order": olat_E, "lat_fill": flat_E, "qty": qty_val},
        }
        def _to_float(val):
            try:
                return float(val)
            except Exception:
                return None
        if trace_val:
            base_qty_long = qty_val if qty_val is not None else None
            base_qty_short = -qty_val if qty_val is not None else None
            await self._push_fill_db(
                trace=trace_val,
                ts=ts_val,
                venue=venue_long,
                base_amount=base_qty_long,
                fill_price=fill_long,
                latency=_to_float(lat_fill_long),
            )
            await self._push_fill_db(
                trace=trace_val,
                ts=ts_val,
                venue=venue_short,
                base_amount=base_qty_short,
                fill_price=fill_short,
                latency=_to_float(lat_fill_short),
            )
        else:
            logger_db.warning("[ERROR INSERT FILL]")
        if getattr(self.state, "warm_up_orders", False) and ctx.get("reason") in ("WARM_UP_LE", "WARM_UP_EL"):
            if ctx.get("reason") == "WARM_UP_LE" and getattr(self.state, "warm_up_stage", "").startswith("LE"):
                self.state.warm_up_stage = "EL_PENDING"
            elif ctx.get("reason") == "WARM_UP_EL" and getattr(self.state, "warm_up_stage", "").startswith("EL"):
                self.state.warm_up_stage = "DONE"
        if trace_val:
            ctx_copy = dict(ctx)
            await self._log_decision_db(
                initial=False,
                inv_after=inv_after,
                fill_info=fill_info,
                ctx=ctx_copy,
            )
        self._pending_db = False
        # clear per-trade fill/lat state to avoid leaking into next trade
        self.state.last_fill_price_L = None
        self.state.last_fill_price_E = None
        self.state.last_fill_latency_L = None
        self.state.last_fill_latency_E = None
        self.state.last_send_latency_L = None
        self.state.last_send_latency_E = None
        # defer clearing trade context when waiting for position updates so we can refresh DB once snapshots arrive
        self._pending_position_ctx = None
        self._waiting_for_positions = False
        self._pos_wait_targets = None
        self.state.last_trade_ctx = None
        self._current_trace = None

    async def _log_decision_db(self, initial: bool, inv_after=None, fill_info=None, ctx=None):
        """Emit decision DB trace for warm-up legs and push to Postgres."""
        ctx = ctx or getattr(self.state, "last_trade_ctx", None)
        if not ctx:
            return
        def _hydrate_inv(inv_tuple):
            """Backfill zero entries with latest helper state to avoid empty logs."""
            lq, le, eq, ee = inv_tuple
            try:
                if abs(eq) < 1e-12 and abs(getattr(self.E, "position_qty", 0) or 0) > 0:
                    eq = getattr(self.E, "position_qty", eq)
                    ee = getattr(self.E, "position_entry", ee)
                if abs(lq) < 1e-12 and abs(getattr(self.L, "position_qty", 0) or 0) > 0:
                    lq = getattr(self.L, "position_qty", lq)
                    le = getattr(self.L, "position_entry", le)
            except Exception:
                pass
            return (lq, le, eq, ee)
        def _fmt_price_local(val):
            try:
                return f"{float(val):.6f}"
            except Exception:
                return str(val)
        def _fmt_lat(val):
            try:
                return f"{float(val):.0f}"
            except Exception:
                return "N/A"
        def _inv_json(inv_tuple):
            lq, le, eq, ee = inv_tuple
            return json.dumps(
                [
                    {"venue": "E", "qty": eq, "price": ee},
                    {"venue": "L", "qty": lq, "price": le},
                ]
            )
        def _inv_block(inv_tuple, spread_val=None, fill_info_local=None, include_lat: bool = True):
            lq, le, eq, ee = inv_tuple
            spread_inv = 0.0
            if lq > 0 and eq < 0 and le:
                spread_inv = (ee - le) / le * 100
            elif lq < 0 and eq > 0 and ee:
                spread_inv = (le - ee) / ee * 100
            info_L = (fill_info_local or {}).get("L", {})
            info_E = (fill_info_local or {}).get("E", {})
            lat_order_L = info_L.get("lat_order")
            lat_order_E = info_E.get("lat_order")
            lat_fill_L = info_L.get("lat_fill")
            lat_fill_E = info_E.get("lat_fill")
            if include_lat:
                line_e = f"E -> Qty: {eq}, Price: {_fmt_price_local(ee)}, Order: {_fmt_lat(lat_order_E)}ms, Fill: {_fmt_lat(lat_fill_E)}ms"
                line_l = f"L -> Qty: {lq}, Price: {_fmt_price_local(le)}, Order: {_fmt_lat(lat_order_L)}ms, Fill: {_fmt_lat(lat_fill_L)}ms"
            else:
                line_e = f"E -> Qty: {eq}, Price: {_fmt_price_local(ee)}"
                line_l = f"L -> Qty: {lq}, Price: {_fmt_price_local(le)}"
            line_spread = f"Δ -> {spread_inv if spread_val is None else spread_val:.2f}%"
            return " | ".join([line_e, line_l, line_spread])

        ts_readable = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ctx.get("ts", time.time())))
        reason = ctx.get("reason", "")
        dir_label = ctx.get("dir", "")
        qty = ctx.get("qty", None)
        obL = getattr(self.L, "ob", {})
        obE = getattr(self.E, "ob", {})
        ob_str_L = f"{obL.get('bidPrice')}/{obL.get('askPrice')}"
        ob_str_E = f"{obE.get('bidPrice')}/{obE.get('askPrice')}"
        # use the snapshot captured when the trade was initiated so we don't overwrite with newer state
        inv_before_ctx = ctx.get("inv_before")
        if inv_before_ctx is None:
            inv_before_ctx = (
                getattr(self.state, "invL", 0.0),
                getattr(self.state, "priceInvL", getattr(self.state, "entry_price_L", 0.0)),
                getattr(self.state, "invE", 0.0),
                getattr(self.state, "priceInvE", getattr(self.state, "entry_price_E", 0.0)),
            )
        inv_before = _hydrate_inv(inv_before_ctx)
        inv_before_str = _inv_block(inv_before, spread_val=ctx.get("spread_signal"), fill_info_local=None, include_lat=False)
        # for inv_after, compute spread from actual entry prices instead of reusing signal spread
        # match inv_before formatting: omit order/fill latency details
        if inv_after is None:
            inv_after = (
                getattr(self.state, "invL", 0.0),
                getattr(self.state, "priceInvL", getattr(self.state, "entry_price_L", 0.0)),
                getattr(self.state, "invE", 0.0),
                getattr(self.state, "priceInvE", getattr(self.state, "entry_price_E", 0.0)),
            )
        else:
            try:
                lq, le, eq, ee = inv_after
                inv_after = (
                    lq,
                    getattr(self.state, "priceInvL", le),
                    eq,
                    getattr(self.state, "priceInvE", ee),
                )
            except Exception:
                pass
        inv_after_str = "" if inv_after is None else _inv_block(inv_after, spread_val=None, fill_info_local=None, include_lat=False)
        inv_before_json = _inv_json(inv_before) if inv_before is not None else None
        inv_after_json = _inv_json(inv_after) if inv_after is not None else None
        if qty in ("", None):
            try:
                lq_before, _, eq_before, _ = inv_before
                lq_after, _, eq_after, _ = inv_after
                delta_l = abs(lq_after - lq_before)
                delta_e = abs(eq_after - eq_before)
                qty = max(delta_l, delta_e)
            except Exception:
                qty = None
        def _norm_delta(text: str | None) -> str | None:
            if text is None:
                return None
            # normalize delta marker to "Δ -> x%"
            return re.sub(r"Δ\s*:\s*", "Δ -> ", text)
        inv_before_str = _norm_delta(inv_before_str)
        inv_after_str = _norm_delta(inv_after_str) if inv_after_str is not None else None
        def _fill_str(venue_key):
            if not fill_info or venue_key not in fill_info:
                return f"{venue_key}:latOrder=/ latFill="
            info = fill_info.get(venue_key, {})
            lo = info.get("lat_order", "")
            lf = info.get("lat_fill", "")
            return f"{venue_key}:latOrder={lo} latFill={lf}"
        fill_L = _fill_str("L")
        fill_E = _fill_str("E")
        spread_inv_before = ""
        spread_inv_after = ""
        line = (
            f"{ts_readable},{reason}:{dir_label},{ob_str_L},{ob_str_E},"
            f"{inv_before_str},{inv_after_str},"
            f"{fill_L} | {fill_E},"
            f"{spread_inv_before},{spread_inv_after},qty={qty}"
        )
        try:
            db = await self._get_db_client()
            if not db:
                raise RuntimeError("DB client unavailable for decision log")
            await db.upsert_decision(
                trace=ctx.get("trace"),
                ts=self._ts_as_dt(ctx.get("ts")),
                bot_name=self.bot_name,
                ob_l=ob_str_L,
                ob_e=ob_str_E,
                inv_before=inv_before_json or inv_before_str,
                inv_after=inv_after_json or inv_after_str,
                reason=reason,
                direction=dir_label,
                spread_signal=ctx.get("spread_signal"),
                size=qty if qty not in ("", None) else None,
            )
            log_label = "[INSERT DECISION]" if initial else "[UPDATE DECISION]"
            logger_db.info(
                f"{log_label} {ctx.get('trace')} {reason} {dir_label} {qty if qty is not None else 'N/A'} {ctx.get('spread_signal')}"
            )
        except Exception:
            logger_db.exception("[ERROR INSERT DECISION]")

    async def _push_trade_db(self, trace: str, ts: float, venue: str, size: float,
                             ob_price: float, exec_price: float, lat_order: float,
                             reason: str | None = None, direction: str | None = None,
                             status: str | None = None,
                             payload: str | None = None, resp: str | None = None):
        db = await self._get_db_client()
        if not db:
            logger_db.error("[ERROR INSERT TRADE] DB unavailable")
            return
        try:
            await db.insert_trade(
                trace=trace,
                ts=self._ts_as_dt(ts),
                bot_name=self.bot_name,
                venue=venue,
                size=size,
                ob_price=ob_price,
                exec_price=exec_price,
                lat_order=lat_order,
                reason=reason,
                direction=direction,
                status=status,
                payload=payload,
                resp=resp,
            )
            logger_db.info(f"[INSERT TRADE] {trace} {venue}")
        except Exception:
            logger_db.exception("[ERROR INSERT TRADE]")

    async def _push_fill_db(self, trace: str, ts: float, venue: str,
                            base_amount: float, fill_price: float, latency: float):
        db = await self._get_db_client()
        if not db:
            logger_db.error("[ERROR INSERT FILL] DB unavailable")
            return
        try:
            await db.insert_fill(
                trace=trace,
                ts=self._ts_as_dt(ts),
                bot_name=self.bot_name,
                venue=venue,
                base_amount=base_amount,
                fill_price=fill_price,
                latency=latency,
            )
            logger_db.info(f"[INSERT FILL] {trace} {venue} {base_amount} {fill_price}")
        except Exception:
            logger_db.exception("[ERROR INSERT FILL]")

    # =============================================================
    # INTERNAL: executes 1 Decision()
    # =============================================================
    async def _execute_single(self, d: Decision, log_decision: bool = True):
        # nothing to do
        if d.action_type == ActionType.NONE:
            # still log realtime state even when none
            return None

        # TAKE → market execution (exit / TT entry)
        if d.action_type == ActionType.TAKE:
            # mark pending TT legs to block new decisions until fills seen
            if d.reason in ("TT_LE", "TT_EL", "WARM_UP_LE", "WARM_UP_EL"):
                if self._pending_tt is None:
                    self._pending_tt = {}
                shared_sz = getattr(d, "_tt_size", None)
                if shared_sz is None:
                    shared_sz = self._compute_tt_shared_size_pair(d.reason)
                signed_sz = shared_sz if d.side == Side.LONG else -shared_sz
                self._pending_tt[d.venue.name] = self._pending_tt.get(d.venue.name, 0.0) + signed_sz
            return await self._send_market(d, log_decision=log_decision)


    # =============================================================
    # ACTION HELPERS
    # =============================================================

    async def _send_market(self, d: Decision, log_decision: bool = True):
        """Market order for TT / emergency exit / cancellation follow-through"""

        venue_api = self.L if d.venue.name == "L" else self.E

        # compute aggressive price with slippage applied (what send_market will use)
        ob_price = venue_api.ob["askPrice"] if d.side.name == "LONG" else venue_api.ob["bidPrice"]
        price = ob_price
        slippage = SLIPPAGE_DEFAULT
        if slippage and ob_price:
            if d.side.name == "LONG":
                price = ob_price * (1 + slippage)
            else:
                price = ob_price * (1 - slippage)
        # stash exec price for trade log
        if d.venue.name == "L":
            setattr(self.state, "last_exec_price_L", price)
            if getattr(self.state, "last_trade_ctx", None):
                self.state.last_trade_ctx["exec_price_L"] = price
        else:
            setattr(self.state, "last_exec_price_E", price)
            if getattr(self.state, "last_trade_ctx", None):
                self.state.last_trade_ctx["exec_price_E"] = price
        shared_tt = getattr(d, "_tt_size", None)
        if shared_tt is not None:
            size = shared_tt
        elif getattr(d, "reason", None) in ("TT_LE", "TT_EL"):
            size = self._compute_tt_shared_size_pair(d.reason)
        else:
            min_size = getattr(venue_api, "min_size", 0) or 0
            min_value = getattr(venue_api, "min_value", 0) or 0
            size = max(min_size, (min_value / price) if (min_value and price) else 0)

        # size/value guard
        min_size = getattr(venue_api, "min_size", None)
        if min_size and size < min_size:
            raise RuntimeError(f"Order size {size} below min size {min_size} for {d.venue.name}")
        min_value = getattr(venue_api, "min_value", None)
        if min_value and price and size * price < min_value:
            raise RuntimeError(f"Order notional {size * price:.6f} below min value {min_value} for {d.venue.name}")

        signal_perf = None
        if getattr(self.state, "last_trade_ctx", None):
            signal_perf = self.state.last_trade_ctx.get("signal_perf")
        if signal_perf is None:
            signal_perf = getattr(self.state, "last_signal_perf", None)
        start_perf = time.perf_counter()
        result = await venue_api.send_market(d.side, size, price)
        end_perf = time.perf_counter()
        send_elapsed_ms = (end_perf - start_perf) * 1000
        overall_latency_ms = send_elapsed_ms
        if signal_perf is not None:
            overall_latency_ms = (end_perf - signal_perf) * 1000
        if not result:
            # record failed attempt with status detail
            return {
                "venue": d.venue.name,
                "size": size if d.side == Side.LONG else -size,
                "ob_price": ob_price,
                "exec_price": price,
                "lat_order": overall_latency_ms,
                "reason": getattr(d, "reason", None),
                "direction": getattr(d, "direction", None),
                "status": "ERROR",
                "payload": None,
                "resp": "send_market returned None",
            }
        result_status = "OK"
        result_payload = None
        result_resp = None
        if isinstance(result, dict):
            result_status = result.get("status") or ("ERROR" if result.get("error") else "OK")
            result_payload = result.get("payload")
            result_resp = (
                result.get("resp")
                or result.get("tx_info")
                or result.get("tx_hash")
                or result.get("error")
            )
        else:
            # fall back to stringifying unexpected structures so we can inspect them later
            result_payload = str(result)
        # store send timestamp/latency for fill logging even when per-leg logs are suppressed
        start_for_fill = signal_perf if signal_perf is not None else end_perf
        if d.venue.name == "L":
            setattr(self.state, "last_send_ts_L", start_for_fill)
            setattr(self.state, "last_send_latency_L", overall_latency_ms)
        else:
            setattr(self.state, "last_send_ts_E", start_for_fill)
            setattr(self.state, "last_send_latency_E", overall_latency_ms)

            if log_decision:
                spread_val = None
                if self._last_spreads and getattr(d, "reason", None):
                    spread_val = self._last_spreads.get(d.reason)
                spread_str = f"{spread_val:.2f}%" if spread_val is not None else "N/A"
                ob_used = ob_price
                exec_price = price
                filled_qty = result.get("filled_qty") if isinstance(result, dict) else None
        return {
            "venue": d.venue.name,
            "size": size if d.side == Side.LONG else -size,
            "ob_price": ob_price,
            "exec_price": price,
            "lat_order": overall_latency_ms,
            "reason": getattr(d, "reason", None),
            "direction": getattr(d, "direction", None),
            "status": result_status,
            "payload": str(result_payload) if result_payload is not None else None,
            "resp": str(result_resp) if result_resp is not None else None,
        }

    def _compute_tt_shared_size_pair(self, reason: str) -> float:
        """
        Compute TT size using a shared minimum:
        max(min_size_L, min_size_E, min_value_L/price_L), then snap per venue.
        Extended leg min_size_change is also enforced before snapping.
        Also respect max_of_ob (percentage depth) and max_trade_value caps.
        """
        # map TT/WARM_UP reasons to legs
        if reason in ("TT_LE", "WARM_UP_LE"):
            leg_info = [(self.L, Side.LONG), (self.E, Side.SHORT)]
        else:
            leg_info = [(self.L, Side.SHORT), (self.E, Side.LONG)]

        # side of L leg for price lookup
        def _eff_price(venue_api, side):
            ob_px = venue_api.ob["askPrice"] if side == Side.LONG else venue_api.ob["bidPrice"]
            if not ob_px:
                return None, None
            slip = SLIPPAGE_DEFAULT or 0.0
            if slip:
                if side == Side.LONG:
                    return ob_px, ob_px * (1 + slip)
                return ob_px, ob_px * (1 - slip)
            return ob_px, ob_px

        # gather exec prices for both long/short directions on each venue
        price_L_long, price_L_long_exec = _eff_price(self.L, Side.LONG)
        price_L_short, price_L_short_exec = _eff_price(self.L, Side.SHORT)
        price_E_long, price_E_long_exec = _eff_price(self.E, Side.LONG)
        price_E_short, price_E_short_exec = _eff_price(self.E, Side.SHORT)

        min_size_L = getattr(self.L, "min_size", 0) or 0
        min_size_E = getattr(self.E, "min_size", 0) or 0
        min_value_L = getattr(self.L, "min_value", 0) or 0
        # Extended currently has no min_value; reuse lighter's min_value for both legs
        min_value_E = getattr(self.E, "min_value", 0) or getattr(self.L, "min_value", 0) or 0
        min_size_change_E = getattr(self.E, "min_size_change", 0) or 0
        max_trade_value = self.max_trade_value
        max_of_ob = self.max_of_ob or 0.0

        # cap by % of OB first (priority 1)
        def _ob_cap():
            try:
                if reason in ("TT_LE", "WARM_UP_LE"):
                    depth_L = self.L.ob.get("askSize") or 0.0
                    depth_E = self.E.ob.get("bidSize") or 0.0
                else:
                    depth_L = self.L.ob.get("bidSize") or 0.0
                    depth_E = self.E.ob.get("askSize") or 0.0
                return max_of_ob * min(depth_L, depth_E)
            except Exception:
                return 0.0

        shared = _ob_cap() if max_of_ob else 0.0
        if not shared:
            return 0.0

        # ensure the OB-based size meets min_value requirements on both venues; otherwise skip
        def _notional_ok(size_val):
            if reason in ("TT_LE", "WARM_UP_LE"):
                n_l = (price_L_long_exec or 0) * size_val
                n_e = (price_E_short_exec or 0) * size_val
            else:
                n_l = (price_L_short_exec or 0) * size_val
                n_e = (price_E_long_exec or 0) * size_val
            if min_value_L and n_l < min_value_L:
                return False
            if min_value_E and n_e < min_value_E:
                return False
            return True

        if not _notional_ok(shared):
            return 0.0

        # apply max_trade_value cap (priority 2) using per-leg exec price (not worst)
        cap_sizes = []
        if max_trade_value:
            def _leg_price(venue, side_long_exec, side_short_exec):
                for v, s in leg_info:
                    if v is venue:
                        return side_long_exec if s == Side.LONG else side_short_exec
                return None

            price_L = _leg_price(self.L, price_L_long_exec, price_L_short_exec)
            price_E = _leg_price(self.E, price_E_long_exec, price_E_short_exec)
            if price_L:
                cap_sizes.append(max_trade_value / price_L)
            if price_E:
                cap_sizes.append(max_trade_value / price_E)
        if cap_sizes:
            shared = min(shared, min(cap_sizes))

        # snap using extended min_size_change only (ceil to nearest step)
        def _apply_step(val, step):
            if step and step > 0:
                return math.ceil(val / step) * step
            return val

        shared = _apply_step(shared, min_size_change_E)

        # honor min_size per venue; if OB cap is below either min_size, skip this signal
        if (min_size_L and shared < min_size_L) or (min_size_E and shared < min_size_E):
            return 0.0

        # reject zero/negative sizes to avoid emitting no-op TT decisions
        if not shared or shared <= 0:
            return 0.0
        return shared

    async def _get_db_client(self):
        if self.db_client is not None:
            return self.db_client
        dsn = os.getenv("DATABASE_URL")
        try:
            self.db_client = await DBClient.get(dsn)
        except Exception:
            logger_db.exception("[ERROR DB CLIENT]")
            self.db_client = None
        return self.db_client

    @staticmethod
    def _ts_as_dt(ts_val):
        try:
            return datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
        except Exception:
            return datetime.now(tz=timezone.utc)


def _load_env_files():
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


def _pick_cfg(symbols_cfg, sym_l: str | None, sym_e: str | None):
    if sym_l:
        for item in symbols_cfg:
            if item.get("SYMBOL_LIGHTER") == sym_l and (sym_e is None or item.get("SYMBOL_EXTENDED") == sym_e):
                return item
    return symbols_cfg[0] if symbols_cfg else {}


def _configure_console_logging():
    fmt = "%(asctime)s,%(msecs)03d %(levelname)s:%(name)s:%(message)s"
    formatter = logging.Formatter(fmt, datefmt="%m-%d:%H:%M:%S")
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%m-%d:%H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    for name in ["_TT", "Hedge", "LIG", "EXT", "DB"]:
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.handlers = [h for h in lg.handlers if not isinstance(h, logging.StreamHandler)]
        lg.addHandler(console)
        lg.propagate = False
    for noisy in [
        "websockets",
        "websockets.client",
        "urllib3",
        "urllib3.connectionpool",
        "x10",
        "x10.utils.http",
    ]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger().setLevel(logging.INFO)


async def run_tt_bot(symbol_l: str | None = None, symbol_e: str | None = None, config: dict | None = None):
    """
    Combined TT runner and bot entrypoint.
    CLI args still work when symbol_l/symbol_e are None.
    """
    os.chdir(PROJECT_ROOT)
    _load_env_files()
    cfg_data = config
    if cfg_data is None:
        cfg_data = {}
        cfg_path = PROJECT_ROOT / "config.json"
        if cfg_path.exists():
            try:
                cfg_data = json.loads(cfg_path.read_text())
            except Exception:
                cfg_data = {}
    symbols_cfg = cfg_data.get("symbols", []) if isinstance(cfg_data, dict) else []

    arg_symL = symbol_l if symbol_l is not None else (sys.argv[1] if len(sys.argv) > 1 else None)
    arg_symE = symbol_e if symbol_e is not None else (sys.argv[2] if len(sys.argv) > 2 else None)
    cfg_item = _pick_cfg(symbols_cfg, arg_symL, arg_symE)
    symbolL = cfg_item.get("SYMBOL_LIGHTER", arg_symL or "MEGA")
    symbolE = cfg_item.get("SYMBOL_EXTENDED", arg_symE or "MEGA-USD")

    _configure_console_logging()

    state = State()
    state.last_ob_ts = None
    state.tt_min_hits = int(cfg_item.get("MIN_HITS", os.getenv("MIN_HITS", "3")))
    sig_cap = cfg_item.get("MAX_TRADES", os.getenv("MAX_TRADES", None))
    if sig_cap is not None:
        try:
            state.signals_remaining = int(sig_cap)
        except Exception:
            state.signals_remaining = None
    state.dedup_ob = str(cfg_item.get("DEDUP_OB", os.getenv("DEDUP_OB", "false"))).lower() == "true"
    state.warm_up_orders = str(cfg_item.get("WARM_UP_ORDERS", os.getenv("WARM_UP_ORDERS", "false"))).lower() == "true"
    state.warm_up_stage = "LE_PENDING" if state.warm_up_orders else "DONE"

    L = LighterWS(symbolL)
    E = ExtendedWS(symbolE)
    L.dedup_ob = state.dedup_ob
    E.dedup_ob = state.dedup_ob
    state.hedge_seeded = True

    # hydrate venue metadata before logging config so min sizes/values are populated
    try:
        await L._init_market_id()
    except Exception:
        pass
    try:
        await E._load_market_info()
    except Exception:
        pass

    maker_bot = TTBot(
        state=state,
        lighter=L,
        extended=E,
        symbolL=symbolL,
        symbolE=symbolE,
        minSpread=float(cfg_item.get("MIN_SPREAD", 0.4)),
        spreadTP=float(cfg_item.get("SPREAD_TP", 0.2)),
        max_position_value=cfg_item.get("MAX_POSITION_VALUE", 500),
        max_trade_value=cfg_item.get("MAX_TRADE_VALUE", None),
        max_of_ob=float(cfg_item.get("MAX_OF_OB", 0.3)),
    )
    logging.getLogger("_TT").info(
        "[CONFIG] L=%s E=%s minSpread=%s spreadTP=%s "
        "maxPosValue=%s minHits=%s dedup=%s warmUp=%s maxTrades=%s "
        "minSizeL=%s minSizeE=%s minValueL=%s minSizeChangeE=%s",
        symbolL,
        symbolE,
        maker_bot.minSpread,
        maker_bot.spreadTP,
        maker_bot.max_position_value,
        state.tt_min_hits,
        state.dedup_ob,
        state.warm_up_orders,
        getattr(state, "signals_remaining", None),
        getattr(L, "min_size", None),
        getattr(E, "min_size", None),
        getattr(L, "min_value", None),
        getattr(E, "min_size_change", None),
    )

    async def maker_loop():
        def maybe_finalize_trade():
            ctx = getattr(state, "last_trade_ctx", None)
            if not ctx:
                return
            pending = getattr(maker_bot, "_pending_tt", None) or {}
            qty_ctx = abs(ctx.get("qty") or 0.0)
            tol_local = max(getattr(maker_bot, "_pending_tol", 1e-6), qty_ctx * 1e-4)
            if pending and any(abs(v) > tol_local for v in pending.values()):
                return
            if not maker_bot._waiting_for_positions:
                maker_bot._mark_wait_for_positions()

            async def _finalize():
                try:
                    logger_tt.info(
                        "[BOTH VENUES FILLED] "
                        f"L:{state.invL}@{getattr(state, 'priceInvL', getattr(state, 'entry_price_L', 0))} "
                        f"E:{state.invE}@{getattr(state, 'priceInvE', getattr(state, 'entry_price_E', 0))}"
                    )
                    await maker_bot._log_trade_complete()
                finally:
                    maker_bot._pending_tt = None
                    state.last_send_latency_L = None
                    state.last_send_latency_E = None

            asyncio.create_task(_finalize())

        def on_update():
            state.last_ob_ts = time.time()
            if L.ob["bidPrice"] and L.ob["askPrice"] and E.ob["bidPrice"] and E.ob["askPrice"]:
                spreads = calc_spreads(L, E, state)
                snap = (
                    L.ob["bidPrice"],
                    L.ob["bidSize"],
                    L.ob["askPrice"],
                    L.ob["askSize"],
                    E.ob["bidPrice"],
                    E.ob["bidSize"],
                    E.ob["askPrice"],
                    E.ob["askSize"],
                    spreads.get("TT_LE"),
                    spreads.get("TT_EL"),
                    spreads.get("MT_LE"),
                    spreads.get("MT_EL"),
                    spreads.get("TM_LE"),
                    spreads.get("TM_EL"),
                )
                should_log = True
                if state.dedup_ob and state.last_spread_snapshot == snap:
                    should_log = False
                if should_log:
                    state.last_spread_snapshot = snap
            asyncio.create_task(maker_bot.loop())

        L.set_ob_callback(on_update)
        E.set_ob_callback(on_update)

        def _inv_spread():
            l_qty, e_qty = state.invL, state.invE
            l_entry, e_entry = getattr(state, "entry_price_L", 0), getattr(state, "entry_price_E", 0)
            spread_inv = 0.0
            if l_qty > 0 and e_qty < 0 and l_entry:
                spread_inv = (e_entry - l_entry) / l_entry * 100
            elif l_qty < 0 and e_qty > 0 and e_entry:
                spread_inv = (l_entry - e_entry) / e_entry * 100
            return spread_inv

        def on_inv_l(delta):
            prev_qty = state.invL
            prev_entry = getattr(state, "entry_price_L", 0)
            pending_ref = (maker_bot._pending_tt or {}).get("L")
            if pending_ref and abs(delta) > abs(pending_ref) * 1.1:
                delta = math.copysign(abs(pending_ref), delta)
            state.invL += delta
            if abs(state.invL) < 1e-9:
                state.invL = 0.0
            if getattr(maker_bot, "_pending_tt", None) is None:
                maker_bot._pending_tt = {}
            maker_bot._pending_tt["L"] = maker_bot._pending_tt.get("L", 0.0) - delta
            ctx_qty = getattr(state, "last_trade_ctx", {}).get("qty") or 0.0
            base_tol = getattr(maker_bot, "_pending_tol", 1e-3)
            tol = max(base_tol, abs(ctx_qty) * 1e-4)
            order_lat = getattr(state, "last_send_latency_L", None)
            fill_lat = None
            ts = getattr(state, "last_send_ts_L", None)
            if ts:
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
            new_qty = state.invL
            trace_val = getattr(maker_bot, "_current_trace", None) or getattr(state, "last_trade_ctx", {}).get("trace") or "unknown"
            logger_tt.info(f"[FILLED LIG] {trace_val}")
            if abs(maker_bot._pending_tt.get("L", 0.0)) < tol:
                maker_bot._pending_tt["L"] = 0.0
            if abs(maker_bot._pending_tt.get("E", 0.0)) < tol:
                maker_bot._pending_tt["E"] = 0.0
            maybe_finalize_trade()

        def on_inv_e(delta):
            prev_qty = state.invE
            prev_entry = getattr(state, "entry_price_E", 0)
            pending_ref = (maker_bot._pending_tt or {}).get("E")
            if pending_ref and abs(delta) > abs(pending_ref) * 1.1:
                delta = math.copysign(abs(pending_ref), delta)
            state.invE += delta
            if abs(state.invE) < 1e-9:
                state.invE = 0.0
            if getattr(maker_bot, "_pending_tt", None) is None:
                maker_bot._pending_tt = {}
            maker_bot._pending_tt["E"] = maker_bot._pending_tt.get("E", 0.0) - delta
            ctx_qty = getattr(state, "last_trade_ctx", {}).get("qty") or 0.0
            base_tol = getattr(maker_bot, "_pending_tol", 1e-3)
            tol = max(base_tol, abs(ctx_qty) * 1e-4)
            order_lat = getattr(state, "last_send_latency_E", None)
            fill_lat = None
            ts = getattr(state, "last_send_ts_E", None)
            if ts:
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
            trace_val = getattr(maker_bot, "_current_trace", None) or getattr(state, "last_trade_ctx", {}).get("trace") or "unknown"
            logger_tt.info(f"[FILLED EXT] {trace_val}")
            if abs(maker_bot._pending_tt.get("L", 0.0)) < tol:
                maker_bot._pending_tt["L"] = 0.0
            if abs(maker_bot._pending_tt.get("E", 0.0)) < tol:
                maker_bot._pending_tt["E"] = 0.0
            maybe_finalize_trade()

        L.set_inventory_callback(on_inv_l)
        E.set_inventory_callback(on_inv_e)
        L.set_position_state_callback(lambda qty, entry: maker_bot._on_position_update("L", qty, entry))
        E.set_position_state_callback(lambda qty, entry: maker_bot._on_position_update("E", qty, entry))
        l_qty, l_entry = await L.load_initial_position()
        e_qty, e_entry = await E.load_initial_position()
        state.invL = l_qty
        state.entry_price_L = l_entry
        state.priceInvL = l_entry
        state.invE = e_qty
        state.entry_price_E = e_entry
        state.priceInvE = e_entry
        logging.getLogger("_TT").info(
            f"[INIT] L:{l_qty}@{l_entry} | E:{e_qty}@{e_entry} | Δ:{_inv_spread():.4f}%"
        )
        await asyncio.gather(L.start(), E.start())

    await maker_loop()


async def main():
    await run_tt_bot()


if __name__ == "__main__":
    asyncio.run(main())
