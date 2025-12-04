# maker/maker_realtime_runner.py (sketch)
import asyncio
import os
from pathlib import Path

from bot.common.state import State
from bot.backup.maker.maker_bot import MakerBot
from bot.venues.helper_lighter import LighterWS
from bot.venues.helper_extended import ExtendedWS


def _load_env():
    env_path = Path(__file__).resolve().parents[3] / ".env_bot"
    if not env_path.exists():
        return
    with env_path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env()


class MakerRealtimeRunner:
    def __init__(self, symbol_lighter, symbol_extended,
                 minSpread, spreadTP, spreadInv, repriceTick,
                 order_size, enable_tt=True):
        self.state = State()
        self.L = LighterWS(symbol_lighter)
        self.E = ExtendedWS(symbol_extended)

        self.bot = MakerBot(
            state=self.state,
            lighter=self.L,
            extended=self.E,
            symbolL=symbol_lighter,
            symbolE=symbol_extended,
            minSpread=minSpread,
            spreadTP=spreadTP,
            spreadInv=spreadInv,
            repriceTick=repriceTick,
            order_size=order_size,
            enable_tt=enable_tt,
            test_mode=False,   # 👈 signals only
        )

    async def start(self):
        def on_update():
            asyncio.create_task(self.bot.loop())

        self.L.set_ob_callback(on_update)
        self.E.set_ob_callback(on_update)
        # inventory updates from taker/maker fills
        self.L.set_inventory_callback(lambda delta: setattr(self.state, "invL", self.state.invL + delta))
        self.E.set_inventory_callback(lambda delta: setattr(self.state, "invE", self.state.invE + delta))
        # entry prices from position snapshots
        self.L.set_position_state_callback(lambda qty, entry: setattr(self.state, "entry_price_L", entry))
        self.E.set_position_state_callback(lambda qty, entry: setattr(self.state, "entry_price_E", entry))

        await asyncio.gather(
            self.L.start(),
            self.E.start(),
        )


async def main():
    runner = MakerRealtimeRunner(
        symbol_lighter="MEGA",
        symbol_extended="MEGA-USD",
        minSpread=0.5,
        spreadTP=0.30,
        spreadInv=0.00,
        repriceTick=0.0001,  # 0.01%
        order_size=25,
        enable_tt=True,
    )
    await runner.start()


if __name__ == "__main__":
    asyncio.run(main())
