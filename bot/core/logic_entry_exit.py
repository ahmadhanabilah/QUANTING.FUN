from bot.common.enums import ActionType, Venue, Side
from bot.common.decision import Decision


def logic_entry_exit(state, spreads, minSpread, spreadTP,
                     lighter_ob, extended_ob, tt_min_hits: int = 3,
                     size_hint_le: float | None = None,
                     size_hint_el: float | None = None):
    """
    TT-only logic (entries/exits). MT/TM paths removed.
    Adds warmup tags (WARM_UP_LE/WARM_UP_EL) for initial runs.
    """

    def _clear_direction():
        state.current_direction = None

    # emit latest spreads in-place (overwrites same console line)
    try:
        le_val = spreads.get("TT_LE")
        el_val = spreads.get("TT_EL")
        le_txt = f"{le_val:.4f}" if le_val is not None else "N/A"
        el_txt = f"{el_val:.4f}" if el_val is not None else "N/A"
        # Log strong spreads as full lines for easier tracking.
        if (le_val is not None and le_val > minSpread) or (el_val is not None and el_val > minSpread):
            print(f"[SPREAD] TT_LE={le_txt} TT_EL={el_txt}")
        else:
            print(f"[SPREAD] TT_LE={le_txt} TT_EL={el_txt}", end="\r", flush=True)
    except Exception:
        pass

    def _make_decision(venue: Venue, side: Side, reason: str, direction: str, size=None):
        ob = lighter_ob if venue == Venue.L else extended_ob
        price = ob.get("askPrice") if side == Side.LONG else ob.get("bidPrice")
        d = Decision(ActionType.TAKE, venue, side, price=price, reason=reason, direction=direction)
        size_hint = size if size is not None else (size_hint_le if reason in ("TT_LE", "WARM_UP_LE") else size_hint_el)
        if size_hint is not None:
            setattr(d, "_tt_size", size_hint)
        # track OB price for downstream logging/DB
        if venue == Venue.L:
            setattr(d, "_ob_price_L", price)
        else:
            setattr(d, "_ob_price_E", price)
        return d

    warm_up_enabled = getattr(state, "warm_up_orders", False)
    warm_stage = getattr(state, "warm_up_stage", "DONE")
    if warm_up_enabled:
        # gate normal logic until warm-up sequence completes
        if warm_stage == "LE_PENDING":
            _clear_direction()
            return (
                _make_decision(Venue.L, Side.LONG, "WARM_UP_LE", "entry"),
                _make_decision(Venue.E, Side.SHORT, "WARM_UP_LE", "entry"),
            )
        if warm_stage == "EL_PENDING":
            _clear_direction()
            return (
                _make_decision(Venue.E, Side.LONG, "WARM_UP_EL", "exit"),
                _make_decision(Venue.L, Side.SHORT, "WARM_UP_EL", "exit"),
            )
        if warm_stage in ("LE_INFLIGHT", "EL_INFLIGHT"):
            return Decision(ActionType.NONE)

    # ----------------------------
    # 1) EXIT LOGIC (TT only)
    # ----------------------------
    # LE position: L long / E short
    if state.invL > 0 and state.invE < 0:
        val = spreads.get("TT_EL")
        if val is not None:
            hist = getattr(state, "tt_el_exit_history", [])
            hist = (hist + [{
                "ts": getattr(state, "last_ob_ts", None),
                "spread": val,
                "bpL": lighter_ob.get("bidPrice"),
                "bsL": lighter_ob.get("bidSize"),
                "apL": lighter_ob.get("askPrice"),
                "asL": lighter_ob.get("askSize"),
                "bpE": extended_ob.get("bidPrice"),
                "bsE": extended_ob.get("bidSize"),
                "apE": extended_ob.get("askPrice"),
                "asE": extended_ob.get("askSize"),
            }])[-max(tt_min_hits, 1):]
            state.tt_el_exit_history = hist
            if len(hist) >= tt_min_hits and all(h.get("spread", 0) > spreadTP for h in hist):
                _clear_direction()
                return (
                    _make_decision(Venue.E, Side.LONG, "TT_EL", "exit"),
                    _make_decision(Venue.L, Side.SHORT, "TT_EL", "exit"),
                )
        else:
            state.tt_el_exit_history = []

    # EL position: E long / L short
    if state.invE > 0 and state.invL < 0:
        val = spreads.get("TT_LE")
        if val is not None:
            hist = getattr(state, "tt_le_exit_history", [])
            hist = (hist + [{
                "ts": getattr(state, "last_ob_ts", None),
                "spread": val,
                "bpL": lighter_ob.get("bidPrice"),
                "bsL": lighter_ob.get("bidSize"),
                "apL": lighter_ob.get("askPrice"),
                "asL": lighter_ob.get("askSize"),
                "bpE": extended_ob.get("bidPrice"),
                "bsE": extended_ob.get("bidSize"),
                "apE": extended_ob.get("askPrice"),
                "asE": extended_ob.get("askSize"),
            }])[-max(tt_min_hits, 1):]
            state.tt_le_exit_history = hist
            if len(hist) >= tt_min_hits and all(h.get("spread", 0) > spreadTP for h in hist):
                _clear_direction()
                return (
                    _make_decision(Venue.L, Side.LONG, "TT_LE", "exit"),
                    _make_decision(Venue.E, Side.SHORT, "TT_LE", "exit"),
                )
        else:
            state.tt_le_exit_history = []

    # track current direction so we can allow scaling in the same direction only
    current_dir = None
    if state.invL > 0 and state.invE < 0:
        current_dir = "TT_LE"   # long L / short E
    elif state.invL < 0 and state.invE > 0:
        current_dir = "TT_EL"   # long E / short L

    # ----------------------------
    # 2) ENTRY LOGIC (TT only; warmup handled above)
    # ----------------------------
    tt_hits = {
        "TT_LE": "tt_le_hits",
        "TT_EL": "tt_el_hits",
    }

    # update TT hit counters
    for key, attr in (("TT_LE", "tt_le_hits"), ("TT_EL", "tt_el_hits")):
        val = spreads.get(key)
        if val is not None and val > minSpread:
            # dedup spammy HIT logs when the same OB snapshot repeats
            last_hit_cache = getattr(state, "_tt_last_hit_ts", {}) or {}
            ob_ts = getattr(state, "last_ob_ts", None)
            if ob_ts is None or last_hit_cache.get(key) != ob_ts:
                try:
                    last_hit_cache[key] = ob_ts
                    state._tt_last_hit_ts = last_hit_cache
                except Exception:
                    pass
            setattr(state, attr, getattr(state, attr, 0) + 1)
            hist_attr = "tt_le_history" if key == "TT_LE" else "tt_el_history"
            hist = getattr(state, hist_attr, [])
            hist = (hist + [{
                "ts": getattr(state, "last_ob_ts", None),
                "spread": val,
                "bpL": lighter_ob.get("bidPrice"),
                "bsL": lighter_ob.get("bidSize"),
                "apL": lighter_ob.get("askPrice"),
                "asL": lighter_ob.get("askSize"),
                "bpE": extended_ob.get("bidPrice"),
                "bsE": extended_ob.get("bidSize"),
                "apE": extended_ob.get("askPrice"),
                "asE": extended_ob.get("askSize"),
            }])[-max(tt_min_hits, 1):]
            setattr(state, hist_attr, hist)
        else:
            setattr(state, attr, 0)
            if key == "TT_LE":
                state.tt_le_history = []
            else:
                state.tt_el_history = []

    # consider TT tags only
    tt_candidates = {
        "TT_LE": spreads.get("TT_LE"),
        "TT_EL": spreads.get("TT_EL"),
    }
    # if already in a position, only allow adding in the same direction
    if current_dir:
        opposite = "TT_EL" if current_dir == "TT_LE" else "TT_LE"
        tt_candidates[opposite] = None

    best_key = None
    best_val = None
    for k, v in tt_candidates.items():
        if v is None:
            continue
        if best_val is None or v > best_val:
            best_val = v
            best_key = k

    if best_key is None or best_val is None or best_val <= minSpread:
        return Decision(ActionType.NONE)

    hits = getattr(state, tt_hits[best_key], 0)
    if hits < tt_min_hits:
        return Decision(ActionType.NONE)

    _clear_direction()
    if best_key == "TT_LE":
        return (
            _make_decision(Venue.L, Side.LONG, "TT_LE", "entry"),
            _make_decision(Venue.E, Side.SHORT, "TT_LE", "entry"),
        )
    return (
        _make_decision(Venue.E, Side.LONG, "TT_EL", "entry"),
        _make_decision(Venue.L, Side.SHORT, "TT_EL", "entry"),
    )
