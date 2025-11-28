class State:
    def __init__(self):
        # inventory on each venue
        self.invL = 0.0
        self.invE = 0.0
        self.entry_price_L = 0.0
        self.entry_price_E = 0.0
        # unhedged maker deltas (tracked by HedgeBot)
        self.unhedged_L = 0.0
        self.unhedged_E = 0.0
        # set by hedge runner after initial position seeding
        self.hedge_seeded = False

        # active maker order
        self.active_order_id = None
        self.active_order_venue = None  # Venue.L or Venue.E
        self.active_order_side = None    # Side.LONG / SHORT

        # remember which entry mode we're working on (MT/TM/TT)
        self.current_direction = None

        # TT consecutive hit counters (for entry filters)
        self.tt_le_hits = 0
        self.tt_el_hits = 0
        self.tt_le_history = []
        self.tt_el_history = []
        # TT consecutive hit counters for exits
        self.tt_le_exit_hits = 0
        self.tt_el_exit_hits = 0
        self.tt_le_exit_history = []
        self.tt_el_exit_history = []

        # limit on how many signals to process (None = unlimited)
        self.signals_remaining = None

        # last spread snapshot for deduping spread.log
        self.last_spread_snapshot = None

        # dedup orderbook callbacks (per venue top-of-book)
        self.dedup_ob = False
