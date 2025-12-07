# maker/maker_bot.py
import asyncio
import re
import time
import os
import uuid
import math
import json
from datetime import datetime, timezone
import logging
import aiohttp
from bot.common.calc_spreads import calc_spreads
from bot.core.logic_entry_exit import logic_entry_exit
from bot.common.enums import ActionType, Venue, Side
from bot.common.decision import Decision
from bot.common.db_client import DBClient
# from bot.common.event_bus import publish  # not needed yet


logger_maker = logging.getLogger("_TT")
logger_dec_db = logging.getLogger("DecisionDB")
SLIPPAGE_DEFAULT = 0.04  # 4% hardcoded slippage


class TTBot:
    def __init__(self, state, lighter, extended,
                 symbolL, symbolE,
                 minSpread, spreadTP, repriceTick,
                 max_position_value=None,
                 max_trade_value=None,
                 max_of_ob: float = 0.3,
                 test_mode=False):
        self.state = state
        self.L = lighter
        self.E = extended
        self.symbolL = symbolL
        self.symbolE = symbolE
        self.minSpread = minSpread
        self.spreadTP = spreadTP
        self.repriceTick = repriceTick  # fraction e.g. 0.0001 = 0.01%
        self.max_position_value = max_position_value
        self.max_trade_value = max_trade_value
        self.max_of_ob = max_of_ob
        self.test_mode = test_mode

        # Hard guard: if dollar cap is below venue min value, stop immediately
        try:
            min_value_l = getattr(self.L, "min_value", 0) or 0
            if self.max_trade_value is not None and min_value_l and self.max_trade_value < min_value_l:
                logger_maker.error(
                    f"MAX_TRADE_VALUE ({self.max_trade_value}) below Lighter min_value ({min_value_l}); stopping bot"
                )
                raise RuntimeError("max_trade_value below lighter min_value")
        except Exception:
            # if check fails unexpectedly, allow startup to continue
            pass
        self._exec_lock = asyncio.Lock()
        self._last_spreads = {}
        self._pending_tt = None  # tracks pending TT fills per venue
        self._pending_db = False
        # tolerance must be below TT qty but allow minor fill-size drift
        self._pending_tol = 1e-6
        self._last_invL = state.invL
        self._last_invE = state.invE
        self._last_spreads = {}
        self._test_trade_logged = False
        self.bot_name = f"TT:{self.symbolL}:{self.symbolE}"
        self.db_client = None

    async def loop(self):
        """Call this on every OB update."""
        # wait until hedge runner has seeded initial unhedged positions
        if not getattr(self.state, "hedge_seeded", True):
            return

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
                logger_maker.info("[FILLED] TT legs complete; finalizing trade from loop")
                await self._log_trade_complete()
            logger_maker.info("[FILLED] TT legs complete; resuming decisions")
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
        )

        # max exposure check (entries only)
        val_l = abs(self.state.invL * getattr(self.state, "entry_price_L", 0) or 0)
        val_e = abs(self.state.invE * getattr(self.state, "entry_price_E", 0) or 0)
        max_val = max(val_l, val_e)
        if self.max_position_value == 0 or (self.max_position_value and max_val >= self.max_position_value):
            # block new entries when exposure cap reached
            def _is_entry(dec):
                return getattr(dec, "direction", None) == "entry"
            if isinstance(decision, tuple):
                if any(_is_entry(d) for d in decision):
                    decision = Decision(ActionType.NONE)
            else:
                if _is_entry(decision):
                    decision = Decision(ActionType.NONE)

        # convert NONE->CANCEL when we need to clear active order
        if (isinstance(decision, Decision)
                and decision.action_type == ActionType.NONE
                and self.state.active_order_id
                and self.state.active_order_venue
                and self.state.active_order_side):
            target_price = self._target_price(self.state.active_order_venue, self.state.active_order_side)
            if self.last_order_price is not None and target_price is not None:
                delta = abs(self.last_order_price - target_price)
                min_move = abs(self.last_order_price) * self.repriceTick
                if delta <= min_move:
                    return
            decision = Decision(ActionType.CANCEL)

        # 3) logging
        self._log_spreads_and_decision(spreads, decision, max_val)

        # respect max trade cap after logging so realtime still updates
        if getattr(self.state, "signals_remaining", None) is not None and self.state.signals_remaining <= 0:
            return

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
        self._test_trade_logged = False
        # log initial decision immediately so DB has a row even if fills/logs lag
        try:
            ctx_snapshot = dict(self.state.last_trade_ctx)
            asyncio.create_task(self._log_decision_db(initial=True, ctx=ctx_snapshot))
        except Exception:
            logger_dec_db.exception("decision_db_initial_log_failed")
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

        # 5) execute (no-op in test_mode)
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
                    logger_maker.info(
                        f"[DECISION] {reasons[0]} dir={dir_label} | qty={qty_log} "
                        f"venues={decision[0].venue.name}/{decision[1].venue.name} "
                        f"sides={decision[0].side.name}/{decision[1].side.name} "
                        f"ob=[{ob_prices[0]}, {ob_prices[1]} Δ:{spread_str}] "
                        f"exec=[{exec_prices[0]}, {exec_prices[1]}] "
                        f"spreads={hist_str} cons={cons_list}"
                    )
                    # reset counters after firing
                    self.state.tt_le_hits = 0
                    self.state.tt_el_hits = 0
                    self.state.tt_le_history = []
                    self.state.tt_el_history = []
                    self.state.tt_le_exit_history = []
                    self.state.tt_el_exit_history = []
                if not self.test_mode:
                    self._pending_db = True
                ctx_snapshot = dict(self.state.last_trade_ctx) if getattr(self.state, "last_trade_ctx", None) else None
                send_tasks = [asyncio.create_task(self._execute_single(d, log_decision=True)) for d in decision]
                log_task = asyncio.create_task(self._log_decision_db(initial=True, ctx=ctx_snapshot)) if ctx_snapshot else None
                all_tasks = send_tasks + ([log_task] if log_task else [])
                results = await asyncio.gather(*all_tasks, return_exceptions=True)
                send_results = results[: len(send_tasks)]
                log_result = results[-1] if log_task else None
                if isinstance(log_result, Exception):
                    logger_maker.warning(f"[DB] initial decision log failed: {log_result}")
                for res in send_results:
                    if not res or isinstance(res, Exception):
                        continue
                    await self._push_trade_db(
                        trace=getattr(self, "_current_trace", None)
                        or getattr(self.state, "last_trade_ctx", {}).get("trace"),
                        ts=self.state.last_trade_ctx.get("ts") if getattr(self.state, "last_trade_ctx", None) else time.time(),
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

    def _log_spreads_and_decision(self, spreads, decision, max_val):
        def fmt(val):
            if val is None:
                return "None"
            return f"{val: .4f}"

        reason_val = None
        if isinstance(decision, Decision) and decision.reason and decision.reason in spreads:
            reason_val = spreads.get(decision.reason)
        # realtime logging disabled

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

    async def _log_test_trade_if_needed(self, decision: Decision):
        """In test mode, still emit a trades.csv row for TT/WARM_UP legs."""
        if decision.action_type != ActionType.TAKE:
            return
        if getattr(decision, "reason", None) not in ("TT_LE", "TT_EL", "WARM_UP_LE", "WARM_UP_EL"):
            return
        ctx = getattr(self.state, "last_trade_ctx", None)
        if not ctx or self._test_trade_logged:
            return
        def _slip_exec(_venue, side, ob_px):
            slip_val = SLIPPAGE_DEFAULT
            if not ob_px or not slip_val:
                return ob_px
            return ob_px * (1 + slip_val) if side == Side.LONG else ob_px * (1 - slip_val)
        # ensure exec prices are populated; fallback to decision price/OB
        if ctx.get("exec_price_L") is None:
            if decision.venue == Venue.L and decision.price:
                ctx["exec_price_L"] = _slip_exec(Venue.L, decision.side, decision.price)
            elif ctx.get("ob_price_L") is not None:
                ctx["exec_price_L"] = _slip_exec(Venue.L, decision.side, ctx.get("ob_price_L"))
        if ctx.get("exec_price_E") is None:
            if decision.venue == Venue.E and decision.price:
                ctx["exec_price_E"] = _slip_exec(Venue.E, decision.side, decision.price)
            elif ctx.get("ob_price_E") is not None:
                ctx["exec_price_E"] = _slip_exec(Venue.E, decision.side, ctx.get("ob_price_E"))
        # ensure qty is present; fallback to computed TT size if missing
        if ctx.get("qty") is None:
            try:
                ctx["qty"] = self._compute_tt_shared_size_pair(ctx.get("reason") or "TT_LE")
            except Exception:
                ctx["qty"] = 0.0
        # default exec to current OB if not set
        ctx.setdefault("exec_price_L", ctx.get("ob_price_L"))
        ctx.setdefault("exec_price_E", ctx.get("ob_price_E"))
        # use exec price as synthetic fill and zeroed latencies so test mode still records DB rows
        exec_price_L = ctx.get("exec_price_L")
        exec_price_E = ctx.get("exec_price_E")
        setattr(self.state, "last_exec_price_L", exec_price_L)
        setattr(self.state, "last_exec_price_E", exec_price_E)
        setattr(self.state, "last_fill_price_L", exec_price_L)
        setattr(self.state, "last_fill_price_E", exec_price_E)
        setattr(self.state, "last_send_latency_L", 0.0)
        setattr(self.state, "last_send_latency_E", 0.0)
        setattr(self.state, "last_fill_latency_L", 0.0)
        setattr(self.state, "last_fill_latency_E", 0.0)
        # apply synthetic inventory impact so inv_after reflects mocked fills
        reason = ctx.get("reason")
        qty = ctx.get("qty") or 0.0
        if reason in ("TT_LE", "WARM_UP_LE"):
            delta_L, delta_E = qty, -qty
        else:
            delta_L, delta_E = -qty, qty
        self.state.invL += delta_L
        self.state.invE += delta_E
        if delta_L:
            setattr(self.state, "entry_price_L", exec_price_L or ctx.get("ob_price_L") or getattr(self.state, "entry_price_L", 0))
        if delta_E:
            setattr(self.state, "entry_price_E", exec_price_E or ctx.get("ob_price_E") or getattr(self.state, "entry_price_E", 0))
        # clear any pending TT since we simulated the fills
        self._pending_tt = None
        self._test_trade_logged = True
        ctx_snapshot = dict(ctx)
        await self._push_test_trade_db(ctx_snapshot)
        # also mock fill rows in test mode so downstream consumers see fills
        trace_val = ctx_snapshot.get("trace")
        ts_val = ctx_snapshot.get("ts", time.time())
        qty_val = ctx_snapshot.get("qty") or qty
        if trace_val and qty_val:
            base_qty_L = qty_val if reason in ("TT_LE", "WARM_UP_LE") else -qty_val
            base_qty_E = -qty_val if reason in ("TT_LE", "WARM_UP_LE") else qty_val
            await self._push_fill_db(
                trace=trace_val,
                ts=ts_val,
                venue="L",
                base_amount=base_qty_L,
                fill_price=exec_price_L,
                latency=0.0,
            )
            await self._push_fill_db(
                trace=trace_val,
                ts=ts_val,
                venue="E",
                base_amount=base_qty_E,
                fill_price=exec_price_E,
                latency=0.0,
            )
        await self._log_trade_complete(ctx_snapshot=ctx_snapshot)
        logger_maker.info("[TEST TRADE LOGGED] simulated fills recorded (no fills/latencies)")

    async def _log_trade_complete(self, ctx_snapshot: dict | None = None):
        ctx = ctx_snapshot or getattr(self.state, "last_trade_ctx", None)
        if not ctx:
            return
        # gather fills
        fill_L = getattr(self.state, "last_fill_price_L", None)
        fill_E = getattr(self.state, "last_fill_price_E", None)
        # fallback to aggressive prices sent if fill not captured (skip in test_mode to keep nulls)
        if fill_L is None and not self.test_mode:
            fill_L = ctx.get("exec_price_L") or getattr(self.state, "last_exec_price_L", None) or ctx.get("ob_price_L")
        if fill_E is None and not self.test_mode:
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
            inv_after = (0, 0, inv_after[2], inv_after[3])
        if inv_after[2] == 0:
            self.state.entry_price_E = 0
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
        if spread_filled is None and not self.test_mode:
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

        # recompute entry prices using weighted average of previous entry and this fill
        def _weighted_entry(prev_qty, prev_entry, delta_qty, px):
            new_qty = (prev_qty or 0) + (delta_qty or 0)
            if new_qty == 0 or px is None:
                return 0.0
            if prev_qty == 0 or (prev_qty > 0 > new_qty) or (prev_qty < 0 < new_qty):
                return px
            try:
                return (prev_qty * prev_entry + delta_qty * px) / new_qty
            except Exception:
                return px

        try:
            l_before, l_entry_before, e_before, e_entry_before = ctx.get("inv_before", (0, 0, 0, 0))
            qty = qty_val or 0.0
            if reason == "TT_LE":
                new_l_entry = _weighted_entry(l_before, l_entry_before, qty, fill_long)
                new_e_entry = _weighted_entry(e_before, e_entry_before, -qty, fill_short)
            else:
                new_l_entry = _weighted_entry(l_before, l_entry_before, -qty, fill_short)
                new_e_entry = _weighted_entry(e_before, e_entry_before, qty, fill_long)
            if new_l_entry is not None:
                self.state.entry_price_L = new_l_entry
            if new_e_entry is not None:
                self.state.entry_price_E = new_e_entry
            inv_after = (
                self.state.invL,
                getattr(self.state, "entry_price_L", 0),
                self.state.invE,
                getattr(self.state, "entry_price_E", 0),
            )
        except Exception:
            pass

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
        logging.getLogger("Trades").info(row)
        logger_maker.info(f"[TRADE_LOGGED] {row}")
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
            logger_dec_db.warning("[DB] skip insert_fill: missing trace")
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
        self.state.last_trade_ctx = None
        self._current_trace = None

    async def _log_decision_db(self, initial: bool, inv_after=None, fill_info=None, ctx=None):
        """Emit decision DB trace for warm-up legs and push to Postgres."""
        ctx = ctx or getattr(self.state, "last_trade_ctx", None)
        if not ctx:
            return
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
        qty = ctx.get("qty", "")
        obL = getattr(self.L, "ob", {})
        obE = getattr(self.E, "ob", {})
        ob_str_L = f"{obL.get('bidPrice')}/{obL.get('askPrice')}"
        ob_str_E = f"{obE.get('bidPrice')}/{obE.get('askPrice')}"
        inv_before = ctx.get("inv_before", (0, 0, 0, 0))
        inv_before_str = _inv_block(inv_before, spread_val=ctx.get("spread_signal"), fill_info_local=None, include_lat=False)
        # for inv_after, compute spread from actual entry prices instead of reusing signal spread
        # match inv_before formatting: omit order/fill latency details
        inv_after_str = "" if inv_after is None else _inv_block(inv_after, spread_val=None, fill_info_local=None, include_lat=False)
        inv_before_json = _inv_json(inv_before) if inv_before is not None else None
        inv_after_json = _inv_json(inv_after) if inv_after is not None else None
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
        logger_dec_db.info(line)
        # push to DB (decisions always upsert; trades/fills handled elsewhere)
        db = await self._get_db_client()
        if not db:
            raise RuntimeError("DB client unavailable for decision log")
        logger_dec_db.info(
            f"[DB] upsert_decision trace={ctx.get('trace')} reason={reason} dir={dir_label} ts={ctx.get('ts')}"
        )
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
        )

    async def _push_trade_db(self, trace: str, ts: float, venue: str, size: float,
                             ob_price: float, exec_price: float, lat_order: float,
                             reason: str | None = None, direction: str | None = None,
                             status: str | None = None,
                             payload: str | None = None, resp: str | None = None):
        db = await self._get_db_client()
        if not db:
            raise RuntimeError("DB client unavailable for trade log")
        logger_dec_db.info(
            f"[DB] insert_trade trace={trace} venue={venue} size={size} ob={ob_price} exec={exec_price} lat={lat_order} status={status}"
        )
        logger_maker.info(
            f"[DB] insert_trade trace={trace} venue={venue} size={size} ob={ob_price} exec={exec_price} lat={lat_order} status={status}"
        )
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

    async def _push_fill_db(self, trace: str, ts: float, venue: str,
                            base_amount: float, fill_price: float, latency: float):
        db = await self._get_db_client()
        if not db:
            raise RuntimeError("DB client unavailable for fill log")
        logger_dec_db.info(
            f"[DB] insert_fill trace={trace} venue={venue} qty={base_amount} price={fill_price} lat={latency}"
        )
        logger_maker.info(
            f"[DB] insert_fill trace={trace} venue={venue} qty={base_amount} price={fill_price} lat={latency}"
        )
        await db.insert_fill(
            trace=trace,
            ts=self._ts_as_dt(ts),
            bot_name=self.bot_name,
            venue=venue,
            base_amount=base_amount,
            fill_price=fill_price,
            latency=latency,
        )

    async def _push_test_trade_db(self, ctx=None):
        """Synthetic trades for test_mode so tables stay populated."""
        if ctx is None:
            ctx = getattr(self.state, "last_trade_ctx", None)
        if not ctx:
            return
        try:
            db = await self._get_db_client()
            if not db:
                return
            ts_val = ctx.get("ts", time.time())
            qty = ctx.get("qty")
            if qty is None:
                qty = self._compute_tt_shared_size_pair(ctx.get("reason") or "TT_LE")
            reason = ctx.get("reason")
            ob_L = ctx.get("ob_price_L")
            ob_E = ctx.get("ob_price_E")
            exec_L = ctx.get("exec_price_L") or ob_L
            exec_E = ctx.get("exec_price_E") or ob_E
            # TT_LE means long L / short E; inverse for TT_EL
            if reason in ("TT_LE", "WARM_UP_LE"):
                size_L = qty
                size_E = -qty
            else:
                size_L = -qty
                size_E = qty
            await db.insert_trade(
                trace=ctx.get("trace"),
                ts=self._ts_as_dt(ts_val),
                bot_name=self.bot_name,
                venue="L",
                size=size_L,
                ob_price=ob_L,
                exec_price=exec_L,
                lat_order=0.0,
                reason=ctx.get("reason"),
                direction=ctx.get("dir"),
            )
            await db.insert_trade(
                trace=ctx.get("trace"),
                ts=self._ts_as_dt(ts_val),
                bot_name=self.bot_name,
                venue="E",
                size=size_E,
                ob_price=ob_E,
                exec_price=exec_E,
                lat_order=0.0,
                reason=ctx.get("reason"),
                direction=ctx.get("dir"),
            )
        except Exception:
            logger_dec_db.exception("test_trade_db_push_failed")

    # =============================================================
    # INTERNAL: executes 1 Decision()
    # =============================================================
    async def _execute_single(self, d: Decision, log_decision: bool = True):
        # In test mode, synthesize a price so logs/DB entries are populated
        if self.test_mode and d.action_type == ActionType.TAKE and d.price is None:
            ob = self.L.ob if d.venue.name == "L" else self.E.ob
            d.price = ob["askPrice"] if d.side == Side.LONG else ob["bidPrice"]
        if self.test_mode:
            if d.action_type != ActionType.NONE:
                logger_maker.info(f"[TEST DECISION] {d}")
                await self._log_test_trade_if_needed(d)
                self._pending_db = False
            return None

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
        if not self.test_mode:
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

        return shared

    async def _get_db_client(self):
        if self.db_client is not None:
            return self.db_client
        dsn_key = "TEST_DATABASE_URL" if self.test_mode else "DATABASE_URL"
        dsn = os.getenv(dsn_key) or os.getenv("DATABASE_URL")
        self.db_client = await DBClient.get(dsn)
        return self.db_client

    @staticmethod
    def _ts_as_dt(ts_val):
        try:
            return datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
        except Exception:
            return datetime.now(tz=timezone.utc)
