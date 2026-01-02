import asyncio
import json
import os
from typing import Optional

import asyncpg


class DBClient:
    """Lightweight async Postgres helper with lazy pool + schema ensure."""

    _instance = None
    _lock = asyncio.Lock()
    _TRACE_SECTION_COLUMNS = {
        "bot_configs": "bot_configs",
        "decision_data": "decision_data",
        "decision_ob_v1": "decision_ob_v1",
        "decision_ob_v2": "decision_ob_v2",
        "trade_v1": "trade_v1",
        "trade_v2": "trade_v2",
        "fill_v1": "fill_v1",
        "fill_v2": "fill_v2",
    }

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._tables_ready = False

    @classmethod
    async def get(cls, dsn: str | None):
        if not dsn:
            return None
        async with cls._lock:
            if cls._instance is None or cls._instance.dsn != dsn:
                cls._instance = cls(dsn)
            return cls._instance

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4)
        if not self._tables_ready:
            async with self._pool.acquire() as conn:
                await self._ensure_tables(conn)
            self._tables_ready = True
        return self._pool

    async def _ensure_tables(self, conn):
        await conn.execute(
            """
            create table if not exists decisions (
                trace text primary key,
                ts timestamptz not null,
                bot_name text not null,
                ob_l text,
                ob_e text,
                inv_before text,
                inv_after text,
                reason text,
                direction text,
                spread_signal double precision
            );
            create table if not exists trades (
                id bigserial primary key,
                trace text not null,
                ts timestamptz not null,
                bot_name text not null,
                venue text not null,
                size double precision,
                ob_price double precision,
                exec_price double precision,
                lat_order double precision,
                status text,
                payload text,
                resp text,
                reason text,
                direction text
            );
            create table if not exists fills (
                id bigserial primary key,
                trace text not null,
                ts timestamptz not null,
                bot_name text not null,
                venue text not null,
                base_amount double precision,
                fill_price double precision,
                latency double precision
            );
            create table if not exists traces (
                bot_id text not null,
                trace text not null,
                bot_configs jsonb,
                decision_data jsonb,
                decision_ob_v1 jsonb,
                decision_ob_v2 jsonb,
                trade_v1 jsonb,
                trade_v2 jsonb,
                fill_v1 jsonb,
                fill_v2 jsonb,
                primary key (bot_id, trace)
            );
            alter table decisions add column if not exists reason text;
            alter table decisions add column if not exists direction text;
            alter table decisions add column if not exists spread_signal double precision;
            alter table decisions add column if not exists size double precision;
            alter table trades add column if not exists reason text;
            alter table trades add column if not exists direction text;
            alter table trades add column if not exists status text;
            alter table trades add column if not exists payload text;
            alter table trades add column if not exists resp text;
            """
        )
        # Ensure traces table exists before any trace-based writes occur.
        # (Table creation is idempotent thanks to IF NOT EXISTS.)


    def _serialize(self, payload) -> str | None:
        if payload is None:
            return None
        if isinstance(payload, str):
            return payload
        try:
            return json.dumps(payload)
        except Exception:
            return str(payload)

    async def init_trace(self, bot_id: str, trace: str, bot_configs: dict | None,
                         decision_data: dict | None, ob_v1: dict | None, ob_v2: dict | None):
        pool = await self._get_pool()
        bot_configs_val = self._serialize(bot_configs)
        decision_data_val = self._serialize(decision_data)
        ob_v1_val = self._serialize(ob_v1)
        ob_v2_val = self._serialize(ob_v2)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into traces (bot_id, trace, bot_configs, decision_data, decision_ob_v1, decision_ob_v2)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (bot_id, trace) do update
                set bot_configs = excluded.bot_configs,
                    decision_data = excluded.decision_data,
                    decision_ob_v1 = excluded.decision_ob_v1,
                    decision_ob_v2 = excluded.decision_ob_v2;
                """,
                bot_id, trace, bot_configs_val, decision_data_val, ob_v1_val, ob_v2_val
            )

    async def update_trace_section(self, bot_id: str, trace: str, section: str, payload: dict | None):
        col = self._TRACE_SECTION_COLUMNS.get(section)
        if col is None:
            raise ValueError(f"Unknown trace section: {section}")
        pool = await self._get_pool()
        value = self._serialize(payload)
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                insert into traces (bot_id, trace, {col})
                values ($1, $2, $3)
                on conflict (bot_id, trace) do update
                set {col} = $3;
                """,
                bot_id, trace, value
            )
    async def upsert_decision(self, trace: str, ts, bot_name: str, ob_l: str, ob_e: str,
                              inv_before: str, inv_after: str,
                              reason: str | None = None, direction: str | None = None,
                              spread_signal: float | None = None, size: float | None = None):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into decisions (trace, ts, bot_name, ob_l, ob_e, inv_before, inv_after, reason, direction, spread_signal, size)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                on conflict (trace) do update
                set ob_l = excluded.ob_l,
                    ob_e = excluded.ob_e,
                    inv_before = excluded.inv_before,
                    inv_after = excluded.inv_after,
                    reason = excluded.reason,
                    direction = excluded.direction,
                    spread_signal = excluded.spread_signal,
                    size = excluded.size,
                    ts = excluded.ts,
                    bot_name = excluded.bot_name;
                """,
                trace, ts, bot_name, ob_l, ob_e, inv_before, inv_after, reason, direction, spread_signal, size
            )

    async def insert_trade(self, trace: str, ts, bot_name: str, venue: str,
                           size: float, ob_price: float, exec_price: float, lat_order: float,
                           reason: str | None = None, direction: str | None = None,
                           status: str | None = None,
                           payload: str | None = None, resp: str | None = None):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into trades (trace, ts, bot_name, venue, size, ob_price, exec_price, lat_order, reason, direction, status, payload, resp)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13);
                """,
                trace, ts, bot_name, venue, size, ob_price, exec_price, lat_order, reason, direction, status, payload, resp
            )

    async def insert_fill(self, trace: str, ts, bot_name: str, venue: str,
                          base_amount: float, fill_price: float, latency: float):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                insert into fills (trace, ts, bot_name, venue, base_amount, fill_price, latency)
                values ($1, $2, $3, $4, $5, $6, $7);
                """,
                trace, ts, bot_name, venue, base_amount, fill_price, latency
            )

    async def fetch_decisions(self, bot_name: str, limit: int = 200):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select trace, ts, bot_name, ob_l, ob_e, inv_before, inv_after, reason, direction, spread_signal, size
                from decisions
                where bot_name = $1
                order by ts desc
                limit $2;
                """,
                bot_name, limit
            )

    async def fetch_decisions_all(self, limit: int = 200):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select trace, ts, bot_name, ob_l, ob_e, inv_before, inv_after, reason, direction, spread_signal, size
                from decisions
                order by ts desc
                limit $1;
                """,
                limit
            )

    async def fetch_trades(self, bot_name: str, limit: int = 200):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select trace, ts, bot_name, venue, size, ob_price, exec_price, lat_order, reason, direction, status, payload, resp
                from trades
                where bot_name = $1
                order by ts desc
                limit $2;
                """,
                bot_name, limit
            )

    async def fetch_trades_all(self, limit: int = 200):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select trace, ts, bot_name, venue, size, ob_price, exec_price, lat_order, reason, direction, status, payload, resp
                from trades
                order by ts desc
                limit $1;
                """,
                limit
            )

    async def fetch_fills(self, bot_name: str, limit: int = 200):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select trace, ts, bot_name, venue, base_amount, fill_price, latency
                from fills
                where bot_name = $1
                order by ts desc
                limit $2;
                """,
                bot_name, limit
            )

    async def fetch_fills_all(self, limit: int = 200):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select trace, ts, bot_name, venue, base_amount, fill_price, latency
                from fills
                order by ts desc
                limit $1;
                """,
                limit
            )

    async def recent_summary(self, bot_name: str, since_ts) -> dict:
        """
        Return counts of decisions/trades/fills since `since_ts` and latest inv_after.
        since_ts should be a timezone-aware datetime.
        """
        import re

        def _norm_delta_str(val):
            if not val:
                return val
            return re.sub(r"Δ\s*:\s*", "Δ -> ", val)

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            dec_count = await conn.fetchval(
                "select count(*) from decisions where bot_name=$1 and ts >= $2",
                bot_name, since_ts
            )
            trade_count = await conn.fetchval(
                "select count(*) from trades where bot_name=$1 and ts >= $2",
                bot_name, since_ts
            )
            fill_count = await conn.fetchval(
                "select count(*) from fills where bot_name=$1 and ts >= $2",
                bot_name, since_ts
            )
            fills_L = await conn.fetchval(
                "select count(*) from fills where bot_name=$1 and ts >= $2 and venue='L'",
                bot_name, since_ts
            )
            fills_E = await conn.fetchval(
                "select count(*) from fills where bot_name=$1 and ts >= $2 and venue='E'",
                bot_name, since_ts
            )
            entry_le = await conn.fetchval(
                "select count(distinct trace) from trades where bot_name=$1 and ts >= $2 and reason='TT_LE' and direction='entry'",
                bot_name, since_ts
            )
            entry_el = await conn.fetchval(
                "select count(distinct trace) from trades where bot_name=$1 and ts >= $2 and reason='TT_EL' and direction='entry'",
                bot_name, since_ts
            )
            exit_le = await conn.fetchval(
                "select count(distinct trace) from trades where bot_name=$1 and ts >= $2 and reason='TT_LE' and direction='exit'",
                bot_name, since_ts
            )
            exit_el = await conn.fetchval(
                "select count(distinct trace) from trades where bot_name=$1 and ts >= $2 and reason='TT_EL' and direction='exit'",
                bot_name, since_ts
            )
            trades_L = await conn.fetchval(
                "select count(*) from trades where bot_name=$1 and ts >= $2 and venue='L'",
                bot_name, since_ts
            )
            trades_E = await conn.fetchval(
                "select count(*) from trades where bot_name=$1 and ts >= $2 and venue='E'",
                bot_name, since_ts
            )
            lat_orders = await conn.fetchval(
                "select avg(lat_order) from trades where bot_name=$1 and ts >= $2",
                bot_name, since_ts
            )
            lat_orders_L = await conn.fetchval(
                "select avg(lat_order) from trades where bot_name=$1 and ts >= $2 and venue='L'",
                bot_name, since_ts
            )
            lat_orders_E = await conn.fetchval(
                "select avg(lat_order) from trades where bot_name=$1 and ts >= $2 and venue='E'",
                bot_name, since_ts
            )
            lat_fills = await conn.fetchval(
                "select avg(latency) from fills where bot_name=$1 and ts >= $2",
                bot_name, since_ts
            )
            lat_fills_L = await conn.fetchval(
                "select avg(latency) from fills where bot_name=$1 and ts >= $2 and venue='L'",
                bot_name, since_ts
            )
            lat_fills_E = await conn.fetchval(
                "select avg(latency) from fills where bot_name=$1 and ts >= $2 and venue='E'",
                bot_name, since_ts
            )
            latest_row = await conn.fetchrow(
                "select inv_after, inv_before, ts from decisions where bot_name=$1 order by ts desc limit 1",
                bot_name
            )
            inv_after = _norm_delta_str(latest_row["inv_after"]) if latest_row else None
            inv_before = _norm_delta_str(latest_row["inv_before"]) if latest_row else None
            latest_dec_ts = latest_row["ts"] if latest_row else None
        return {
            "decisions_1m": dec_count or 0,
            "trades_1m": trade_count or 0,
            "fills_1m": fill_count or 0,
            "fills_L": fills_L or 0,
            "fills_E": fills_E or 0,
            "entries_le": entry_le or 0,
            "entries_el": entry_el or 0,
            "exits_le": exit_le or 0,
            "exits_el": exit_el or 0,
            "trades_L": trades_L or 0,
            "trades_E": trades_E or 0,
            "avg_lat_order_ms": float(lat_orders) if lat_orders is not None else None,
            "avg_lat_order_ms_L": float(lat_orders_L) if lat_orders_L is not None else None,
            "avg_lat_order_ms_E": float(lat_orders_E) if lat_orders_E is not None else None,
            "avg_lat_fill_ms": float(lat_fills) if lat_fills is not None else None,
            "avg_lat_fill_ms_L": float(lat_fills_L) if lat_fills_L is not None else None,
            "avg_lat_fill_ms_E": float(lat_fills_E) if lat_fills_E is not None else None,
            "latest_inv_after": inv_after,
            "latest_inv_before": inv_before,
            "latest_decision_ts": latest_dec_ts,
        }

    async def fetch_traces(self, bot_id: str, limit: int = 200, offset: int = 0):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select bot_id, trace, bot_configs, decision_data, decision_ob_v1, decision_ob_v2,
                       trade_v1, trade_v2, fill_v1, fill_v2
                from traces
                where bot_id = $1
                order by coalesce((decision_data->>'ts')::double precision, 0) desc, trace desc
                limit $2
                offset $3;
                """,
                bot_id, limit, offset
            )

    async def fetch_traces_all(self, limit: int = 200, offset: int = 0):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                select bot_id, trace, bot_configs, decision_data, decision_ob_v1, decision_ob_v2,
                       trade_v1, trade_v2, fill_v1, fill_v2
                from traces
                order by coalesce((decision_data->>'ts')::double precision, 0) desc, trace desc
                limit $1
                offset $2;
                """,
                limit, offset
            )

    async def recent_activity_stats(self, bot_id: str, since_ts: float):
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select trace, decision_data, trade_v1, trade_v2, fill_v1, fill_v2
                from traces
                where bot_id = $1
                  and coalesce((decision_data->>'ts')::double precision, 0) >= $2
                order by coalesce((decision_data->>'ts')::double precision, 0) desc
                """,
                bot_id,
                since_ts,
            )
        stats = {
            "entries_1_2": 0,
            "entries_2_1": 0,
            "exits_1_2": 0,
            "exits_2_1": 0,
            "trades_1": 0,
            "trades_2": 0,
            "fills_1": 0,
            "fills_2": 0,
            "avg_lat_order_ms_1": None,
            "avg_lat_order_ms_2": None,
            "avg_lat_fill_ms_1": None,
            "avg_lat_fill_ms_2": None,
            "latest_inv_after": None,
            "latest_inv_before": None,
            "latest_decision_ts": None,
        }
        order_lat_sum_1 = order_lat_cnt_1 = 0
        order_lat_sum_2 = order_lat_cnt_2 = 0
        fill_lat_sum_1 = fill_lat_cnt_1 = 0
        fill_lat_sum_2 = fill_lat_cnt_2 = 0
        latest_ts = 0

        def _parse_number(value: any) -> float | None:
            try:
                return float(value)
            except Exception:
                return None

        def _parse_json(val):
            if val is None:
                return {}
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
            return {}

        for row in rows:
            decision = _parse_json(row.get("decision_data"))
            reason = (decision.get("reason") or "").upper()
            direction = (decision.get("direction") or "").lower()
            ts_val = _parse_number(decision.get("ts"))
            if ts_val and ts_val > latest_ts:
                latest_ts = ts_val
                stats["latest_inv_after"] = decision.get("inv_after")
                stats["latest_inv_before"] = decision.get("inv_before")
                stats["latest_decision_ts"] = ts_val
            if reason == "TT_LE":
                if direction == "entry":
                    stats["entries_1_2"] += 1
                elif direction == "exit":
                    stats["exits_1_2"] += 1
            elif reason == "TT_EL":
                if direction == "entry":
                    stats["entries_2_1"] += 1
                elif direction == "exit":
                    stats["exits_2_1"] += 1

            trade1 = _parse_json(row.get("trade_v1"))
            if trade1:
                stats["trades_1"] += 1
                lat = _parse_number(trade1.get("lat"))
                if lat is not None:
                    order_lat_sum_1 += lat
                    order_lat_cnt_1 += 1
            trade2 = _parse_json(row.get("trade_v2"))
            if trade2:
                stats["trades_2"] += 1
                lat = _parse_number(trade2.get("lat"))
                if lat is not None:
                    order_lat_sum_2 += lat
                    order_lat_cnt_2 += 1

            fill1 = _parse_json(row.get("fill_v1"))
            if fill1:
                stats["fills_1"] += 1
                fill_ts = _parse_number(fill1.get("ts"))
                if fill_ts is not None and ts_val is not None:
                    fill_lat_sum_1 += (fill_ts - ts_val) * 1000
                    fill_lat_cnt_1 += 1
            fill2 = _parse_json(row.get("fill_v2"))
            if fill2:
                stats["fills_2"] += 1
                fill_ts = _parse_number(fill2.get("ts"))
                if fill_ts is not None and ts_val is not None:
                    fill_lat_sum_2 += (fill_ts - ts_val) * 1000
                    fill_lat_cnt_2 += 1

        if order_lat_cnt_1:
            stats["avg_lat_order_ms_1"] = order_lat_sum_1 / order_lat_cnt_1
        if order_lat_cnt_2:
            stats["avg_lat_order_ms_2"] = order_lat_sum_2 / order_lat_cnt_2
        if fill_lat_cnt_1:
            stats["avg_lat_fill_ms_1"] = fill_lat_sum_1 / fill_lat_cnt_1
        if fill_lat_cnt_2:
            stats["avg_lat_fill_ms_2"] = fill_lat_sum_2 / fill_lat_cnt_2
        stats["latest_ts"] = latest_ts
        if not rows:
            return None
        return stats
