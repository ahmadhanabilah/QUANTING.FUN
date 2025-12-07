# ~/QUANTING.FUN/.venv/bin/python -m bot.core.tt_runner BTC BTC-USD

import asyncio

from bot.core.tt_bot import run_tt_bot


async def main():
    await run_tt_bot()


if __name__ == "__main__":
    asyncio.run(main())
