#!/usr/bin/env python3
# Run: /home/ubuntu/QUANTING.FUN/.venv/bin/python /home/ubuntu/QUANTING.FUN/server/test.py
import asyncio
import json
import os
from pathlib import Path

import aiohttp
from x10.perpetual.accounts import StarkPerpetualAccount
from x10.perpetual.configuration import MAINNET_CONFIG
from x10.perpetual.trading_client import PerpetualTradingClient


async def main() -> None:
    env_dir = Path("/home/ubuntu/QUANTING.FUN/env")
    for env_path in env_dir.glob(".env_*"):
        if not env_path.exists():
            continue
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    vault_id = os.getenv("EXTENDED_VAULT_ID")
    private_key = os.getenv("EXTENDED_PRIVATE_KEY")
    public_key = os.getenv("EXTENDED_PUBLIC_KEY")
    api_key = os.getenv("EXTENDED_API_KEY")

    if not all([vault_id, private_key, public_key, api_key]):
        raise RuntimeError("Missing EXTENDED_* credentials in environment")

    stark_acc = StarkPerpetualAccount(
        vault=int(vault_id),
        private_key=private_key,
        public_key=public_key,
        api_key=api_key,
    )
    client = PerpetualTradingClient(MAINNET_CONFIG, stark_acc)

    url = f"{MAINNET_CONFIG.api_base_url}/user/assetOperations"
    headers = {"accept": "application/json", "X-Api-Key": api_key}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            print(f"[raw] status={resp.status} body={text}")
            try:
                payload = json.loads(text)
            except Exception:
                return
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                return

            deposit_total = 0.0
            inbound_total = 0.0
            outbound_total = 0.0

            for row in rows:
                if not isinstance(row, dict):
                    continue
                typ = str(row.get("type", "")).upper()
                amount = float(row.get("amount") or 0)
                fee = float(row.get("fee") or 0)
                direction = str(row.get("direction") or row.get("side") or row.get("transfer_type") or "").upper()

                if typ == "DEPOSIT":
                    deposit_total += amount
                    continue
                if typ in ("WITHDRAWAL", "FAST_WITHDRAWAL", "SLOW_WITHDRAWAL"):
                    outbound_total += abs(amount) + (fee or 0)
                    continue
                if typ == "TRANSFER":
                    if direction in ("IN", "INCOMING", "RECEIVE", "DEPOSIT"):
                        inbound_total += amount
                        continue
                    if direction in ("OUT", "OUTGOING", "SEND", "WITHDRAW"):
                        outbound_total += amount + (fee or 0)
                        continue
                    if amount < 0:
                        outbound_total += abs(amount)
                    elif amount > 0:
                        inbound_total += amount

            net_inflow = deposit_total + inbound_total - outbound_total
            print(
                f"[net_inflow] deposits={deposit_total:.6f} inbound={inbound_total:.6f} "
                f"outbound={outbound_total:.6f} net_inflow={net_inflow:.6f}"
            )


if __name__ == "__main__":
    asyncio.run(main())
