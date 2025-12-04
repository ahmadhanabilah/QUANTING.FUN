# hedge/hedge_realtime_runner.py
import asyncio
import logging
import os
from pathlib import Path

from bot.common.state import State
from bot.common.enums import Venue
from bot.backup.hedge.hedge_bot import HedgeBot
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_extended import ExtendedWS


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_env():
    env_path = PROJECT_ROOT / ".env_bot"
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


_load_env()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")


class HedgeRealtimeRunner:
    def __init__(self, symbol_lighter, symbol_extended, enable_send=True, state=None, lighter=None, extended=None):
        self.state = state or State()
        self.L = lighter or LighterWS(symbol_lighter)
        self.E = extended or ExtendedWS(symbol_extended)
        min_thresh = None
        # provisional threshold; recalculated after OB is ready
        min_thresh = 0.0
        for venue in (self.L, self.E):
            for attr in ("min_size", "min_size_change"):
                val = getattr(venue, attr, None)
                if val:
                    min_thresh = max(min_thresh, val)
        self.bot = HedgeBot(self.state, self.L, self.E, min_qty_threshold=min_thresh, enable_send=enable_send)

    async def start(self):
        # account callbacks update state and trigger hedge check
        def on_l_pos(delta: float):
            logging.info(f"[HedgeRunner] account update L delta={delta}")
            asyncio.create_task(self.bot.on_position_update(Venue.L, delta))

        def on_e_pos(delta: float):
            logging.info(f"[HedgeRunner] account update E delta={delta}")
            asyncio.create_task(self.bot.on_position_update(Venue.E, delta))

        self.L.set_account_callback(on_l_pos)
        self.E.set_account_callback(on_e_pos)
        # inventory updates (maker+taker) for maker bot/state
        self.L.set_inventory_callback(lambda delta: setattr(self.state, "invL", self.state.invL + delta))
        self.E.set_inventory_callback(lambda delta: setattr(self.state, "invE", self.state.invE + delta))
        # update state entry prices from position snapshots
        self.L.set_position_state_callback(lambda qty, entry: setattr(self.state, "entry_price_L", entry))
        self.E.set_position_state_callback(lambda qty, entry: setattr(self.state, "entry_price_E", entry))

        # wait for both orderbooks to be ready before handling deltas
        async def wait_ob_ready():
            logged = False
            while True:
                if (self.L.ob.get("bidPrice") and self.L.ob.get("askPrice") and
                    self.E.ob.get("bidPrice") and self.E.ob.get("askPrice")):
                    logging.info("[HedgeRunner] both orderbooks ready; delta tracking enabled")
                    return
                if not logged:
                    logging.info("[HedgeRunner] waiting for orderbooks...")
                    logged = True
                await asyncio.sleep(0.5)

        # start venue streams
        l_task = asyncio.create_task(self.L.start())
        e_task = asyncio.create_task(self.E.start())

        # wait for both orderbooks to be ready
        await wait_ob_ready()

        # refine hedge threshold using min_size/min_size_change/min_value
        def _best_price(v):
            bid, ask = v.ob.get("bidPrice"), v.ob.get("askPrice")
            if bid and ask:
                return (bid + ask) / 2
            return bid or ask or 0

        candidates = []
        for venue in (self.L, self.E):
            for attr in ("min_size", "min_size_change"):
                val = getattr(venue, attr, None)
                if val:
                    candidates.append(val)
            min_val = getattr(venue, "min_value", None)
            if min_val:
                px = _best_price(venue)
                if px:
                    candidates.append(min_val / px)
        if candidates:
            self.bot.min_qty_threshold = max(candidates)

        # load initial positions after OB ready and threshold set
        l_pos, l_entry = await self.L.load_initial_position()
        e_pos, e_entry = await self.E.load_initial_position()
        self.state.invL = l_pos
        self.state.invE = e_pos
        self.state.entry_price_L = l_entry
        self.state.entry_price_E = e_entry
        self.bot._last_invL = l_pos
        self.bot._last_invE = e_pos
        self.state.unhedged_L = 0.0
        self.state.unhedged_E = 0.0
        # seed unhedged based on initial imbalance so we hedge from start
        net = self.state.invL + self.state.invE
        if net > 0:
            self.bot.unhedged_E = net
            self.state.unhedged_E = net
        elif net < 0:
            self.bot.unhedged_L = -net
            self.state.unhedged_L = -net
        logging.info(
            f"[HedgeRunner] initial net={net} unhedged_L={self.bot.unhedged_L} unhedged_E={self.bot.unhedged_E} "
            f"min_qty_threshold={self.bot.min_qty_threshold}"
        )
        logging.info(
            f"[HedgeRunner] initial positions L({self.L.symbol})={l_pos} entry={l_entry} "
            f"E({self.E.symbol})={e_pos} entry={e_entry}"
        )

        # attempt hedging initial imbalance (if any)
        await self.bot._hedge_unhedged()

        # allow maker loop to run after seeding/hedge attempt
        self.state.hedge_seeded = True

        # keep tasks alive
        await asyncio.gather(l_task, e_task)


async def main():
    runner = HedgeRealtimeRunner(
        symbol_lighter="BTC",
        symbol_extended="BTC-USD",
        enable_send=True
    )
    await runner.start()


if __name__ == "__main__":
    asyncio.run(main())
