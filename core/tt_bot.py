# maker/maker_bot.py
import asyncio
import time
import logging
import aiohttp
from common.calc_spreads import calc_spreads
from core.logic_entry_exit import logic_entry_exit
from common.enums import ActionType, Venue, Side
from common.decision import Decision
# from common.event_bus import publish  # not needed yet


logger_spread = logging.getLogger("Spread")
logger_realtime_spread = logging.getLogger("RealtimeSpread")
logger_maker = logging.getLogger("Maker")


class TTBot:
    def __init__(self, state, lighter, extended,
                 symbolL, symbolE,
                 minSpread, spreadTP, repriceTick,
                 order_value,
                 max_position_value=None,
                 test_mode=False):
        self.state = state
        self.L = lighter
        self.E = extended
        self.symbolL = symbolL
        self.symbolE = symbolE
        self.minSpread = minSpread
        self.spreadTP = spreadTP
        self.repriceTick = repriceTick  # fraction e.g. 0.0001 = 0.01%
        self.last_order_price = None
        self.order_value = order_value
        self.max_position_value = max_position_value
        self.test_mode = test_mode
        self._exec_lock = asyncio.Lock()
        self._last_spreads = {}
        self._pending_tt = None  # tracks pending TT fills per venue
        # tolerance must be below TT qty but allow minor fill-size drift
        self._pending_tol = 1e-6
        self._last_invL = state.invL
        self._last_invE = state.invE
        self._logger_spread_history = logging.getLogger("Maker")
        self._last_spreads = {}
        # telegram batching (1 msg per minute)
        self._tg_buffer = []
        self._tg_task = None
        self._tg_interval = 60.0
        self._test_trade_logged = False

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

        # clear stale pending if already zeroed
        if self._pending_tt and all(abs(v) < self._pending_tol for v in self._pending_tt.values()):
            self._pending_tt = None

        # 1) spreads
        spreads = calc_spreads(self.L, self.E, self.state)
        self._last_spreads = spreads

        # 2) decision
        decision = logic_entry_exit(
            self.state,
            spreads,
            self.minSpread,
            self.spreadTP,
            self.L.ob,
            self.E.ob,
                 tt_min_hits=getattr(self.state, "tt_min_hits", 3),
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

        # enforce TT-only execution; skip anything not a TT tuple
        if not (isinstance(decision, tuple) and all(getattr(d, "reason", None) in ("TT_LE", "TT_EL") for d in decision)):
            return
        # stash trade context before execution
        self.state.last_trade_ctx = {
            "ts": time.time(),
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
        }
        self._test_trade_logged = False

        # 4) track inventory deltas to resolve pending TT fills (after logging so realtime log still updates)
        deltaL = self.state.invL - self._last_invL
        deltaE = self.state.invE - self._last_invE
        self._last_invL = self.state.invL
        self._last_invE = self.state.invE
        if self._pending_tt:
            if "L" in self._pending_tt:
                self._pending_tt["L"] -= deltaL
                if abs(self._pending_tt["L"]) < self._pending_tol:
                    self._pending_tt["L"] = 0.0
            if "E" in self._pending_tt:
                self._pending_tt["E"] -= deltaE
                if abs(self._pending_tt["E"]) < self._pending_tol:
                    self._pending_tt["E"] = 0.0
            # if any remaining pending, skip new decisions until filled
            if self._pending_tt and any(abs(v) > self._pending_tol for v in self._pending_tt.values()):
                logger_maker.debug(f"[PENDING_TT] waiting L={self._pending_tt.get('L')} E={self._pending_tt.get('E')}")
                return
            # cleared
            logger_maker.info("[FILLED] TT legs complete; resuming decisions")
            self._log_trade_complete()
            self._pending_tt = None

        # if capped, decrement once when we have a non-NONE decision
        def _consume_signal_if_needed(dec):
            if getattr(self.state, "signals_remaining", None) is None:
                return
            if isinstance(dec, Decision) and dec.action_type != ActionType.NONE:
                self.state.signals_remaining = max(self.state.signals_remaining - 1, 0)
            elif isinstance(dec, tuple):
                # count tuple as one signal if any action is non-NONE
                if any(d.action_type != ActionType.NONE for d in dec):
                    self.state.signals_remaining = max(self.state.signals_remaining - 1, 0)

        _consume_signal_if_needed(decision)

        # 5) execute (no-op in test_mode)
        async with self._exec_lock:
            if isinstance(decision, tuple):
                reasons = [getattr(d, "reason", None) for d in decision]
                if all(r in ("TT_LE", "TT_EL") for r in reasons):
                    shared = self._compute_tt_shared_size_pair(reasons[0])
                    ob_prices = []
                    exec_prices = []
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
                        slip = getattr(venue_api, "config", {}).get("slippage") if hasattr(venue_api, "config") else None
                        if slip is not None and px_ob:
                            if d.side.name == "LONG":
                                exec_px = px_ob * (1 + slip)
                            else:
                                exec_px = px_ob * (1 - slip)
                        exec_prices.append(exec_px)
                        if shared:
                            setattr(d, "_tt_size", shared)
                    # store exec prices in context by venue
                    try:
                        by_venue = {decision[i].venue.name: exec_prices[i] for i in range(len(decision))}
                        self.state.last_trade_ctx["exec_price_L"] = by_venue.get("L")
                        self.state.last_trade_ctx["exec_price_E"] = by_venue.get("E")
                    except Exception:
                        pass
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
                for d in decision:
                    await self._execute_single(d, log_decision=False)
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
        pending_line = ""
        if self._pending_tt and any(abs(v) > self._pending_tol for v in self._pending_tt.values()):
            pending_line = (
                f"---\n[PENDING] TT waiting "
                f"L={self._pending_tt.get('L',0)} E={self._pending_tt.get('E',0)}"
            )

        dec_line = "---\n[DECISION]\n"
        if isinstance(decision, Decision):
            spread_str = "N/A"
            if reason_val is not None:
                spread_str = f"{reason_val:.2f}%"
            dec_line += (
                f"action={decision.action_type.name} "
                f"side={getattr(decision.side,'name',None)} "
                f"venue={getattr(decision.venue,'name',None)} "
                f"dir={decision.direction} reason={decision.reason} "
                f"spread={spread_str}"
            )
        else:
            dec_line += str(decision)

        ob_block = (
            f"---\n[OB]\n"
            f"L -- bid={self.L.ob['bidPrice']} size={self.L.ob['bidSize']} "
            f"ask={self.L.ob['askPrice']} size={self.L.ob['askSize']}\n"
            f"E -- bid={self.E.ob['bidPrice']} size={self.E.ob['bidSize']} "
            f"ask={self.E.ob['askPrice']} size={self.E.ob['askSize']}\n"
            f"LE - TT_LE={fmt(spreads['TT_LE'])}  MT_LE={fmt(spreads['MT_LE'])}  TM_LE={fmt(spreads['TM_LE'])}  \n"
            f"EL - TT_EL={fmt(spreads['TT_EL'])}  MT_EL={fmt(spreads['MT_EL'])}  TM_EL={fmt(spreads['TM_EL'])}  "
        )

        unhedged_line = (
            f"---\n"
            f"unhedged_L={getattr(self.state, 'unhedged_L', 0.0)} "
            f"unhedged_E={getattr(self.state, 'unhedged_E', 0.0)}"
        )

        # consecutive spreads section
        cons_line = "---\n[CONS SPREAD]"
        if isinstance(decision, Decision) and getattr(decision, "reason", None) in ("TT_LE", "TT_EL"):
            if decision.direction == "exit":
                hist_list = getattr(self.state, "tt_le_exit_history", []) if decision.reason == "TT_LE" else getattr(self.state, "tt_el_exit_history", [])
            else:
                hist_list = getattr(self.state, "tt_le_history", []) if decision.reason == "TT_LE" else getattr(self.state, "tt_el_history", [])
            if hist_list:
                cons_line += " " + ", ".join(f"{h:.4f}" for h in hist_list)

        inv_line = f"---\n{self._format_inv_line()}"

        logger_realtime_spread.info(
            f"\n{ob_block}\n{dec_line}\n{inv_line}\n{unhedged_line}\n{pending_line}\n{cons_line}"
        )

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

    def _log_test_trade_if_needed(self, decision: Decision):
        """In test mode, still emit a trades.csv row for TT legs."""
        if decision.action_type != ActionType.TAKE:
            return
        if getattr(decision, "reason", None) not in ("TT_LE", "TT_EL"):
            return
        ctx = getattr(self.state, "last_trade_ctx", None)
        if not ctx or self._test_trade_logged:
            return
        # default exec to current OB if not set
        ctx.setdefault("exec_price_L", ctx.get("ob_price_L"))
        ctx.setdefault("exec_price_E", ctx.get("ob_price_E"))
        # in test mode, treat fills/latencies as missing (null)
        setattr(self.state, "last_exec_price_L", ctx.get("exec_price_L"))
        setattr(self.state, "last_exec_price_E", ctx.get("exec_price_E"))
        setattr(self.state, "last_fill_price_L", None)
        setattr(self.state, "last_fill_price_E", None)
        setattr(self.state, "last_send_latency_L", None)
        setattr(self.state, "last_send_latency_E", None)
        setattr(self.state, "last_fill_latency_L", None)
        setattr(self.state, "last_fill_latency_E", None)
        self._test_trade_logged = True
        self._log_trade_complete()
        logger_maker.info("[TEST TRADE LOGGED] simulated fills recorded (no fills/latencies)")

    def _log_trade_complete(self):
        ctx = getattr(self.state, "last_trade_ctx", None)
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
            return f"L:{lq}@{le} | E:{eq}@{ee} | Δ:{spread_inv:.2f}%"

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
        # enqueue telegram notify (batched to 1/min)
        self._enqueue_telegram_trade(
            ctx=ctx,
            qty=qty_val,
            spread_signal=ctx.get("spread_signal"),
            spread_filled=spread_filled,
            fill_L=fill_L,
            fill_E=fill_E,
            inv_after=inv_after,
            slippage_long=slippage_long,
            slippage_short=slippage_short,
        )
        self.state.last_trade_ctx = None

    def _enqueue_telegram_trade(self, ctx, qty, spread_signal, spread_filled, fill_L, fill_E, inv_after, slippage_long, slippage_short):
        token = getattr(self.state, "telegram_token", None) or getattr(self.state, "TELEGRAM_BOT_TOKEN", None)
        chat_id = getattr(self.state, "telegram_chat_id", None) or getattr(self.state, "TELEGRAM_CHAT_ID", None)
        if not token or not chat_id:
            return

        def fmt_p(val):
            try:
                return f"{float(val):.6f}"
            except Exception:
                return str(val)
        def fmt_pct(val):
            try:
                return f"{float(val):.2f}%"
            except Exception:
                return "N/A"
        def fmt_inv_tuple(tup):
            lq, le, eq, ee = tup
            return f"L:{lq}@{fmt_p(le)} | E:{eq}@{fmt_p(ee)}"

        reason = ctx.get("reason")
        direction = ctx.get("dir")
        entry = (
            f"{time.strftime('%H:%M:%S', time.gmtime(ctx.get('ts', time.time())))} "
            f"{reason} {direction} qty={qty} "
            f"sig={fmt_pct(spread_signal)} filled={fmt_pct(spread_filled)} "
            f"L={fmt_p(fill_L)} E={fmt_p(fill_E)} "
            f"Inv:{fmt_inv_tuple(inv_after)} "
            f"Slip L:{fmt_pct(slippage_long)} S:{fmt_pct(slippage_short)}"
        )
        self._tg_buffer.append(entry)
        if not self._tg_task or self._tg_task.done():
            self._tg_task = asyncio.create_task(self._flush_telegram_buffer(token, chat_id))

    async def _flush_telegram_buffer(self, token, chat_id):
        try:
            await asyncio.sleep(self._tg_interval)
            if not self._tg_buffer:
                return
            # collapse into one message
            header = f"✅ Trades last {int(self._tg_interval)}s ({len(self._tg_buffer)} new)"
            body = "\n".join(self._tg_buffer)
            # respect Telegram 4096 char limit
            msg = f"{header}\n{body}"
            if len(msg) > 4000:
                msg = msg[:3990] + "\n...truncated..."
            payload = {"chat_id": chat_id, "text": msg}
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload) as resp:
                    if resp.status != 200:
                        logger_maker.error(f"[TELEGRAM] send failed HTTP {resp.status}")
        except Exception as exc:
            logger_maker.error(f"[TELEGRAM] send error: {exc}")
        finally:
            self._tg_buffer = []

    # =============================================================
    # INTERNAL: executes 1 Decision()
    # =============================================================
    async def _execute_single(self, d: Decision, log_decision: bool = True):
        if self.test_mode:
            if d.action_type != ActionType.NONE:
                logger_maker.info(f"[TEST DECISION] {d}")
                self._log_test_trade_if_needed(d)
            return

        # nothing to do
        if d.action_type == ActionType.NONE:
            if self.state.active_order_id:
                await self._cancel_maker_order()
            # still log realtime state even when none
            return

        # cancel maker
        if d.action_type == ActionType.CANCEL:
            await self._cancel_maker_order(d)
            return

        # MAKE → place limit
        if d.action_type == ActionType.MAKE:
            await self._place_limit(d)
            return

        # TAKE → market execution (exit / TT entry)
        if d.action_type == ActionType.TAKE:
            # mark pending TT legs to block new decisions until fills seen
            if d.reason in ("TT_LE", "TT_EL"):
                if self._pending_tt is None:
                    self._pending_tt = {}
                shared_sz = getattr(d, "_tt_size", None)
                if shared_sz is None:
                    shared_sz = self._compute_tt_shared_size_pair(d.reason)
                signed_sz = shared_sz if d.side == Side.LONG else -shared_sz
                self._pending_tt[d.venue.name] = self._pending_tt.get(d.venue.name, 0.0) + signed_sz
            await self._send_market(d, log_decision=log_decision)
            return


    # =============================================================
    # ACTION HELPERS
    # =============================================================

    async def _place_limit(self, d: Decision):
        """Replace or create a maker limit order"""

        venue_api = self.L if d.venue.name == "L" else self.E

        # price provided from decision OR computed target with small improvement
        price = d.price if d.price else self._target_price(d.venue, d.side)
        size = self.order_value / price

        # size/value guard
        min_size = getattr(venue_api, "min_size", None)
        if min_size and size < min_size:
            raise RuntimeError(f"Order size {size} below min size {min_size} for {d.venue.name}")
        min_value = getattr(venue_api, "min_value", None)
        if min_value and price and size * price < min_value:
            raise RuntimeError(f"Order notional {size * price:.6f} below min value {min_value} for {d.venue.name}")

        # if we already have an order, decide cancel/skip/replace
        if self.state.active_order_id and self.state.active_order_venue == d.venue and self.state.active_order_side == d.side:
            # if current target price matches our live order, keep it
            if self.last_order_price is not None and abs(self.last_order_price - price) <= 1e-12:
                return
            delta = abs(self.last_order_price - price) if self.last_order_price else None
            min_move = abs(self.last_order_price) * self.repriceTick if self.last_order_price else 0
            # only replace if price moved more than threshold
            if delta is None or delta <= min_move:
                return
            await self._cancel_maker_order()
        elif self.state.active_order_id:
            # different venue/side → cancel
            await self._cancel_maker_order()

        start_ts = time.perf_counter()
        order_id = await venue_api.place_limit(d.side, price, size)
        elapsed_ms = (time.perf_counter() - start_ts) * 1000
        if not order_id:
            return
        self.last_order_price = price
        reason_val = None
        if self._last_spreads and getattr(d, "reason", None):
            reason_val = self._last_spreads.get(d.reason)

        # update state
        self.state.active_order_id = order_id
        self.state.active_order_venue = d.venue
        self.state.active_order_side = d.side
        if not self.test_mode:
            extra = f" spread={reason_val}" if reason_val is not None else ""
            logger_maker.info(f"[DECISION] {d}{extra}")
            logger_maker.info(
                f"[LIVE] PLACE_LIMIT venue={d.venue.name} side={d.side.name} "
                f"price={price} size={size} order_id={order_id} latency_ms={elapsed_ms:.1f}"
            )


    async def _cancel_maker_order(self, decision: Decision = None):
        if not self.state.active_order_id:
            return

        venue = self.state.active_order_venue
        venue_api = self.L if venue.name == "L" else self.E
        start_ts = time.perf_counter()
        await venue_api.cancel(self.state.active_order_id)
        elapsed_ms = (time.perf_counter() - start_ts) * 1000

        self.state.active_order_id = None
        self.last_order_price = None
        if not self.test_mode:
            if decision:
                logger_maker.info(f"[DECISION] {decision}")
            logger_maker.info(f"[LIVE] CANCEL venue={venue.name} latency_ms={elapsed_ms:.1f}")
        if not self.test_mode:
            print(f"[LIVE] CANCEL venue={venue.name}")


    async def _send_market(self, d: Decision, log_decision: bool = True):
        """Market order for TT / emergency exit / cancellation follow-through"""

        venue_api = self.L if d.venue.name == "L" else self.E

        # compute aggressive price with slippage applied (what send_market will use)
        ob_price = venue_api.ob["askPrice"] if d.side.name == "LONG" else venue_api.ob["bidPrice"]
        price = ob_price
        slippage = getattr(venue_api, "config", {}).get("slippage") if hasattr(venue_api, "config") else None
        if slippage is not None and ob_price:
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
        # TT legs share the smallest allowed size across both venues
        shared_tt = getattr(d, "_tt_size", None)
        if shared_tt is not None:
            size = shared_tt
        elif getattr(d, "reason", None) in ("TT_LE", "TT_EL"):
            size = self._compute_tt_shared_size_pair(d.reason)
        else:
            size = self.order_value / price

        # size/value guard
        min_size = getattr(venue_api, "min_size", None)
        if min_size and size < min_size:
            raise RuntimeError(f"Order size {size} below min size {min_size} for {d.venue.name}")
        min_value = getattr(venue_api, "min_value", None)
        if min_value and price and size * price < min_value:
            raise RuntimeError(f"Order notional {size * price:.6f} below min value {min_value} for {d.venue.name}")

        start_ts = time.perf_counter()
        result = await venue_api.send_market(d.side, size)
        elapsed_ms = (time.perf_counter() - start_ts) * 1000
        if not result:
            return
        if not self.test_mode:
            # store send timestamp/latency for fill logging even when per-leg logs are suppressed
            if d.venue.name == "L":
                setattr(self.state, "last_send_ts_L", time.perf_counter())
                setattr(self.state, "last_send_latency_L", elapsed_ms)
            else:
                setattr(self.state, "last_send_ts_E", time.perf_counter())
                setattr(self.state, "last_send_latency_E", elapsed_ms)

            if log_decision:
                spread_val = None
                if self._last_spreads and getattr(d, "reason", None):
                    spread_val = self._last_spreads.get(d.reason)
                spread_str = f"{spread_val:.2f}%" if spread_val is not None else "N/A"
                ob_used = ob_price
                exec_price = price
                filled_qty = result.get("filled_qty")
                logger_maker.info(
                    f"[DECISION] action={d.action_type.name} venue={d.venue.name} side={d.side.name} "
                    f"dir={d.direction} reason={d.reason} ob_price={ob_used} exec_price={exec_price} "
                    f"qty_sent={size} qty_filled={filled_qty} spread={spread_str}"
                )

    def _target_price(self, venue: Venue, side: Side) -> float:
        """Compute target maker price with small improvement, avoiding crossing."""
        if venue.name == "L":
            ob = self.L.ob
        else:
            ob = self.E.ob
        improve = 0.0003  # 0.03%
        if side.name == "LONG":
            base = ob["bidPrice"]
            ask = ob["askPrice"]
            price = base * (1 + improve)
            if ask and price >= ask:
                price = ask * (1 - 1e-6)
        else:
            base = ob["askPrice"]
            bid = ob["bidPrice"]
            price = base * (1 - improve)
            if bid and price <= bid:
                price = bid * (1 + 1e-6)
        return price

    def _compute_tt_shared_size_pair(self, reason: str) -> float:
        """Compute smallest acceptable size across both TT legs."""
        sizes = []
        # mapping for TT directions: which side each venue takes
        if reason == "TT_LE":
            # L long, E short
            leg_info = [(self.L, Side.LONG), (self.E, Side.SHORT)]
        else:
            # TT_EL -> E long, L short
            leg_info = [(self.L, Side.SHORT), (self.E, Side.LONG)]

        def _snap_size(venue_api, side, raw_size, price):
            """Apply venue rounding/constraints in the same order send_market uses."""
            size = raw_size
            if hasattr(venue_api, "_fmt_decimal_int") and hasattr(venue_api, "size_decimals"):
                rounded = venue_api._fmt_decimal_int(raw_size, venue_api.size_decimals or 0)
                size = rounded / (10 ** (venue_api.size_decimals or 0))
            elif hasattr(venue_api, "_format_qty"):
                try:
                    size = float(venue_api._format_qty(raw_size))
                except Exception:
                    size = raw_size
            min_size = getattr(venue_api, "min_size", 0) or 0
            min_value = getattr(venue_api, "min_value", 0) or 0
            if min_value:
                size = max(size, min_value / price)
            size = max(size, min_size)
            return size

        for venue_api, side in leg_info:
            price = venue_api.ob["askPrice"] if side == Side.LONG else venue_api.ob["bidPrice"]
            if not price:
                continue
            raw_size = self.order_value / price
            sizes.append(_snap_size(venue_api, side, raw_size, price))

        if not sizes:
            return 0.0

        # use the strictest (smallest) venue size and re-quantize for both legs
        shared = min(sizes)
        snapped = []
        for venue_api, side in leg_info:
            price = venue_api.ob["askPrice"] if side == Side.LONG else venue_api.ob["bidPrice"]
            if not price:
                continue
            snapped.append(_snap_size(venue_api, side, shared, price))
        return min(snapped) if snapped else shared
