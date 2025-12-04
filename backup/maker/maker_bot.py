# maker/maker_bot.py
import asyncio
import time
import logging
from bot.common.calc_spreads import calc_spreads
from bot.backup.maker.logic_entry_exit import logic_entry_exit
from bot.common.enums import ActionType, Venue, Side
from bot.common.decision import Decision
# from bot.common.event_bus import publish  # not needed yet


logger_spread = logging.getLogger("Spread")
logger_realtime_spread = logging.getLogger("RealtimeSpread")
logger_maker = logging.getLogger("Maker")


class MakerBot:
    def __init__(self, state, lighter, extended,
                 symbolL, symbolE,
                 minSpread, spreadTP, spreadInv, repriceTick,
                 order_value,
                 max_position_value=None,
                 enable_tt=True,
                 tt_only=False,
                 test_mode=False):
        self.state = state
        self.L = lighter
        self.E = extended
        self.symbolL = symbolL
        self.symbolE = symbolE
        self.minSpread = minSpread
        self.spreadTP = spreadTP
        self.spreadInv = spreadInv
        self.repriceTick = repriceTick  # fraction e.g. 0.0001 = 0.01%
        self.last_order_price = None
        self.order_value = order_value
        self.max_position_value = max_position_value
        self.enable_tt = enable_tt
        self.tt_only = tt_only
        self.test_mode = test_mode
        self._exec_lock = asyncio.Lock()
        self._last_spreads = {}
        self._pending_tt = None  # tracks pending TT fills per venue
        self._pending_tol = 1e-3
        self._last_invL = state.invL
        self._last_invE = state.invE
        self._logger_spread_history = logging.getLogger("Maker")
        self._last_spreads = {}

    async def loop(self):
        """Call this on every OB update."""
        # respect max signal cap
        if getattr(self.state, "signals_remaining", None) is not None and self.state.signals_remaining <= 0:
            return
        # wait until hedge runner has seeded initial unhedged positions
        if not getattr(self.state, "hedge_seeded", True):
            return

        lbid = self.L.ob["bidPrice"]
        lask = self.L.ob["askPrice"]
        ebid = self.E.ob["bidPrice"]
        eask = self.E.ob["askPrice"]

        if not all([lbid, lask, ebid, eask]):
            return  # wait until both books valid

        # 1) spreads
        spreads = calc_spreads(self.L, self.E, self.state)
        self._last_spreads = spreads

        # 2) decision
        decision = logic_entry_exit(
            self.state,
            spreads,
            self.minSpread,
            self.spreadTP,
            self.spreadInv,
            self.L.ob,
            self.E.ob,
                 enable_tt=self.enable_tt,
                 tt_only=self.tt_only,
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

        # enforce TT-only execution; skip anything not a TT tuple
        if not (isinstance(decision, tuple) and all(getattr(d, "reason", None) in ("TT_LE", "TT_EL") for d in decision)):
            return

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
            self._pending_tt = None

        # if capped, decrement once when we have a non-NONE decision
        def _consume_signal_if_needed(dec):
            if getattr(self.state, "signals_remaining", None) is None:
                return
            if isinstance(dec, Decision) and dec.action_type != ActionType.NONE:
                self.state.signals_remaining -= 1
            elif isinstance(dec, tuple):
                # count tuple as one signal if any action is non-NONE
                if any(d.action_type != ActionType.NONE for d in dec):
                    self.state.signals_remaining -= 1

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
                        px = venue_api.ob["askPrice"] if d.side.name == "LONG" else venue_api.ob["bidPrice"]
                        ob_prices.append(px)
                        exec_prices.append(px)
                        if shared:
                            setattr(d, "_tt_size", shared)
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

    # =============================================================
    # INTERNAL: executes 1 Decision()
    # =============================================================
    async def _execute_single(self, d: Decision, log_decision: bool = True):
        if self.test_mode:
            if d.action_type != ActionType.NONE:
                logger_maker.info(f"[TEST DECISION] {d}")
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

        ob_price = venue_api.ob["askPrice"] if d.side.name == "LONG" else venue_api.ob["bidPrice"]
        slip = getattr(venue_api, "config", {}).get("slippage") if hasattr(venue_api, "config") else None
        price = ob_price
        if slip is not None and ob_price:
            if d.side.name == "LONG":
                price = ob_price * (1 + slip)
            else:
                price = ob_price * (1 - slip)
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
        result = await venue_api.send_market(d.side, size, price)
        elapsed_ms = (time.perf_counter() - start_ts) * 1000
        if not result:
            return
        # mark pending TT fill tracking for TAKE orders
        if d.action_type == ActionType.TAKE:
            signed_qty = size if d.side.name == "LONG" else -size
            key = d.venue.name
            if self._pending_tt is None:
                self._pending_tt = {}
            self._pending_tt[key] = self._pending_tt.get(key, 0.0) + signed_qty
        if not self.test_mode:
            # store send timestamp/latency for fill logging even when per-leg logs are suppressed
            if d.venue.name == "L":
                setattr(self.state, "last_send_ts_L", time.time())
                setattr(self.state, "last_send_latency_L", elapsed_ms)
            else:
                setattr(self.state, "last_send_ts_E", time.time())
                setattr(self.state, "last_send_latency_E", elapsed_ms)

            if log_decision:
                spread_val = None
                if self._last_spreads and getattr(d, "reason", None):
                    spread_val = self._last_spreads.get(d.reason)
                spread_str = f"{spread_val:.2f}%" if spread_val is not None else "N/A"
                ob_price = price
                exec_price = price
                filled_qty = result.get("filled_qty")
                logger_maker.info(
                    f"[DECISION] action={d.action_type.name} venue={d.venue.name} side={d.side.name} "
                    f"dir={d.direction} reason={d.reason} ob_price={ob_price} exec_price={exec_price} "
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

        for venue_api, side in leg_info:
            price = venue_api.ob["askPrice"] if side == Side.LONG else venue_api.ob["bidPrice"]
            if not price:
                continue
            raw_size = self.order_value / price
            # apply venue-specific rounding/constraints to approximate sent size
            if hasattr(venue_api, "_fmt_decimal_int") and hasattr(venue_api, "size_decimals"):
                rounded = venue_api._fmt_decimal_int(raw_size, venue_api.size_decimals or 0)
                size = rounded / (10 ** (venue_api.size_decimals or 0))
            elif hasattr(venue_api, "_format_qty"):
                try:
                    size = float(venue_api._format_qty(raw_size))
                except Exception:
                    size = raw_size
            else:
                size = raw_size
            min_size = getattr(venue_api, "min_size", 0) or 0
            min_value = getattr(venue_api, "min_value", 0) or 0
            if min_value:
                size = max(size, min_value / price)
            size = max(size, min_size)
            sizes.append(size)
        return min(sizes) if sizes else 0.0
