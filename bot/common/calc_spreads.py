def calc_spreads(L, E, state=None):
    def _sanitize(bid, ask):
        if bid is None or ask is None:
            return bid, ask
        # if feed delivers inverted book, normalize to bid <= ask
        if bid > ask:
            bid, ask = ask, bid
        return bid, ask

    lbid, lask = _sanitize(L.ob["bidPrice"], L.ob["askPrice"])
    ebid, eask = _sanitize(E.ob["bidPrice"], E.ob["askPrice"])
    if state:
        l_qty = getattr(state, "invL", 0.0)
        e_qty = getattr(state, "invE", 0.0)
        l_entry = getattr(state, "entry_price_L", 0.0)
        e_entry = getattr(state, "entry_price_E", 0.0)
    else:
        l_qty = getattr(L, "position_qty", 0.0) if hasattr(L, "position_qty") else 0.0
        e_qty = getattr(E, "position_qty", 0.0) if hasattr(E, "position_qty") else 0.0
        l_entry = getattr(L, "position_entry", 0.0) if hasattr(L, "position_entry") else 0.0
        e_entry = getattr(E, "position_entry", 0.0) if hasattr(E, "position_entry") else 0.0

    spreads = {}

    # TT (take-take)
    spreads["TT_LE"] = (ebid - lask) / lask * 100 if ebid and lask else None
    spreads["TT_EL"] = (lbid - eask) / eask * 100 if lbid and eask else None

    # MT (maker long, taker short)
    spreads["MT_LE"] = (ebid - lbid) / lbid * 100 if lbid and ebid else None
    spreads["MT_EL"] = (lbid - ebid) / ebid * 100 if ebid and lbid else None

    # TM (maker short, taker long)
    spreads["TM_LE"] = (eask - lask) / lask * 100 if eask and lask else None
    spreads["TM_EL"] = (lask - eask) / eask * 100 if lask and eask else None

    # inventory spread
    spreadInv = 0
    if l_qty > 0 and e_qty < 0 and l_entry:
        spreadInv = (e_entry - l_entry) / l_entry * 100
    elif l_qty < 0 and e_qty > 0 and e_entry:
        spreadInv = (l_entry - e_entry) / e_entry * 100

    spreads["INV"] = spreadInv

    return spreads
