from bot.common.enums import ActionType, Venue, Side
from bot.common.decision import Decision


def logic_entry_exit(state, spreads, minSpread, spreadTP, spreadInv,
                     lighter_ob, extended_ob, enable_tt: bool = True, tt_only: bool = False,
                     tt_min_hits: int = 3):
    """
    state      → inventory + active order info
    spreads    → dict from calc_spreads()
    minSpread  → min % to open new position
    spreadTP   → % target to exit with profit
    spreadInv  → % spread due to inventory / entry advantage
    """

    def _price_for(venue: Venue, side: Side):
        ob = lighter_ob if venue == Venue.L else extended_ob
        if not ob:
            return None
        return ob["bidPrice"] if side == Side.LONG else ob["askPrice"]

    def _start_direction(label: str):
        state.current_direction = label

    def _clear_direction():
        state.current_direction = None

    spread_inv = spreads.get("INV", 0)

    # ----------------------------
    # 1) EXIT LOGIC  (highest priority)
    # ----------------------------

    # has LE → Lighter long / Extended short
    if state.invL > 0 and state.invE < 0:

        # TT exit
        if enable_tt:
            val = spreads["TT_EL"]
            if val is not None:
                val_adj = val + spread_inv
                hist = getattr(state, "tt_el_exit_history", [])
                hist = (hist + [{
                    "ts": getattr(state, "last_ob_ts", None),
                    "spread": val,  # raw TT spread for logging
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
                        Decision(ActionType.TAKE, Venue.E, Side.LONG, reason="TT_EL", direction="exit"),
                        Decision(ActionType.TAKE, Venue.L, Side.SHORT, reason="TT_EL", direction="exit"),
                    )
            else:
                state.tt_el_exit_history = []

        if not tt_only:
            # MT exit
            if spreads["MT_EL"] is not None and spreads["MT_EL"] + spread_inv > spreadTP:
                _clear_direction()
                return Decision(
                    ActionType.MAKE,
                    Venue.E,
                    Side.LONG,
                    price=_price_for(Venue.E, Side.LONG),
                    reason="MT_EL",
                    direction="exit",
                )

            # TM exit
            if spreads["TM_EL"] is not None and spreads["TM_EL"] + spread_inv > spreadTP:
                _clear_direction()
                return Decision(
                    ActionType.MAKE,
                    Venue.L,
                    Side.SHORT,
                    price=_price_for(Venue.L, Side.SHORT),
                    reason="TM_EL",
                    direction="exit",
                )


    # has EL → Extended long / Lighter short
    if state.invE > 0 and state.invL < 0:

        # TT exit
        if enable_tt:
            val = spreads["TT_LE"]
            if val is not None:
                val_adj = val + spread_inv
                hist = getattr(state, "tt_le_exit_history", [])
                hist = (hist + [{
                    "ts": getattr(state, "last_ob_ts", None),
                    "spread": val,  # raw TT spread for logging
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
                        Decision(ActionType.TAKE, Venue.L, Side.LONG, reason="TT_LE", direction="exit"),
                        Decision(ActionType.TAKE, Venue.E, Side.SHORT, reason="TT_LE", direction="exit"),
                    )
            else:
                state.tt_le_exit_history = []

        if not tt_only:
            # MT exit
            if spreads["MT_LE"] is not None and spreads["MT_LE"] + spread_inv > spreadTP:
                _clear_direction()
                return Decision(
                    ActionType.MAKE,
                    Venue.L,
                    Side.LONG,
                    price=_price_for(Venue.L, Side.LONG),
                    reason="MT_LE",
                    direction="exit",
                )

            # TM exit
            if spreads["TM_LE"] is not None and spreads["TM_LE"] + spread_inv > spreadTP:
                _clear_direction()
                return Decision(
                    ActionType.MAKE,
                    Venue.E,
                    Side.SHORT,
                    price=_price_for(Venue.E, Side.SHORT),
                    reason="TM_LE",
                    direction="exit",
                )


    # ----------------------------
    # 2) ENTRY LOGIC  (only if flat)
    # ----------------------------

    # ----------------------------
    # 2) ENTRY LOGIC  (allowed until max_val cap handled upstream)
    # ----------------------------

    # 2a) prioritize TT entries if enabled
    if enable_tt:
        # update consecutive hit counters when above minSpread
        tt_hits = {
            "TT_LE": "tt_le_hits",
            "TT_EL": "tt_el_hits",
        }
        for key, attr in tt_hits.items():
            val = spreads.get(key)
            if val is not None and val > minSpread:
                setattr(state, attr, getattr(state, attr, 0) + 1)
                # track recent above-threshold spreads
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
                # reset history when condition fails
                if key == "TT_LE":
                    state.tt_le_history = []
                else:
                    state.tt_el_history = []

        tt_candidates = {
            "TT_LE": spreads["TT_LE"],
            "TT_EL": spreads["TT_EL"],
        }
        tt_best = max(tt_candidates, key=lambda k: tt_candidates[k] if tt_candidates[k] is not None else -999)
        tt_value = tt_candidates[tt_best]

        # require consecutive hits above minSpread before entering
        hits = getattr(state, tt_hits[tt_best], 0)

        if tt_value is not None and tt_value > minSpread and hits >= tt_min_hits:
            _clear_direction()
            if tt_best == "TT_LE":
                return (
                    Decision(ActionType.TAKE, Venue.L, Side.LONG, reason="TT_LE", direction="entry"),
                    Decision(ActionType.TAKE, Venue.E, Side.SHORT, reason="TT_LE", direction="entry"),
                )
            if tt_best == "TT_EL":
                return (
                    Decision(ActionType.TAKE, Venue.E, Side.LONG, reason="TT_EL", direction="entry"),
                    Decision(ActionType.TAKE, Venue.L, Side.SHORT, reason="TT_EL", direction="entry"),
                )

    if tt_only:
        return Decision(ActionType.NONE)

    # 2b) otherwise consider MT/TM maker entries
    maker_map = {
        "MT_LE": (Venue.L, Side.LONG),
        "MT_EL": (Venue.E, Side.LONG),
        "TM_LE": (Venue.E, Side.SHORT),
        "TM_EL": (Venue.L, Side.SHORT),
    }

    if state.current_direction in maker_map:
        dir_label = state.current_direction
        spread_value = spreads.get(dir_label)
        if spread_value is not None and spread_value > minSpread:
            venue, side = maker_map[dir_label]
            return Decision(
                ActionType.MAKE,
                venue,
                side,
                price=_price_for(venue, side),
                reason=dir_label,
                direction="entry",
            )
        else:
            _clear_direction()

    maker_candidates = {k: spreads[k] for k in maker_map.keys()}
    maker_best = max(maker_candidates, key=lambda k: maker_candidates[k] if maker_candidates[k] is not None else -999)
    maker_value = maker_candidates[maker_best]

    if maker_value is not None and maker_value > minSpread:
        venue, side = maker_map[maker_best]
        _start_direction(maker_best)
        return Decision(
            ActionType.MAKE,
            venue,
            side,
            price=_price_for(venue, side),
            reason=maker_best,
            direction="entry",
        )


    # ----------------------------
    # 3) NO ACTION
    # ----------------------------
    return Decision(ActionType.NONE)
