import base64
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import aiohttp
import psutil
from datetime import datetime, timezone, timedelta
from fastapi import Depends, FastAPI, HTTPException, Response, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from bot.common.db_client import DBClient

app = FastAPI(title="arb_bot control")
security = HTTPBasic()

ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "bot"
CONFIG_PATH = ROOT / "config.json"
LOG_ROOT = BOT_ROOT / "logs"
TMUX_LOG_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env_server"
VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = str(VENV_PY) if VENV_PY.exists() else "python3"
ENV_DIR = ROOT / "env"


def _load_env(path: Path):
    """Minimal .env loader to populate os.environ."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


_load_env(ENV_PATH)
ENV_DIR.mkdir(exist_ok=True)
for env_file in ENV_DIR.glob(".env_*"):
    try:
        with env_file.open() as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        continue
DB_DSN = os.getenv("DATABASE_URL")
DB_TEST_DSN = os.getenv("TEST_DATABASE_URL")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TG_TOPIC_ID = os.getenv("TELEGRAM_TOPIC_ID")
WATCHDOG_ENABLED = str(os.getenv("DB_WATCHDOG_ENABLED", "false")).lower() == "true"
WATCHDOG_PERIOD = float(os.getenv("DB_WATCHDOG_PERIOD", "60"))

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000").split(",")
    if origin.strip()
]
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX", r"http://[\w\.-]+:5000")

cors_kwargs = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if CORS_ORIGINS:
    cors_kwargs["allow_origins"] = CORS_ORIGINS
if CORS_ORIGIN_REGEX:
    cors_kwargs["allow_origin_regex"] = CORS_ORIGIN_REGEX

if cors_kwargs.get("allow_origins") or cors_kwargs.get("allow_origin_regex"):
    app.add_middleware(CORSMiddleware, **cors_kwargs)


def _auth(credentials: HTTPBasicCredentials = Depends(security)):
    user = os.getenv("AUTH_USER", "admin")
    password = os.getenv("AUTH_PASS", "admin")
    correct = credentials.username == user and credentials.password == password
    if not correct:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return credentials.username


def _rand_id(n: int = 6) -> str:
    import secrets
    import string

    alphabet = string.ascii_uppercase
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _ensure_le(entry: dict | None) -> dict:
    if not isinstance(entry, dict):
        return {}
    # normalize venues
    if not entry.get("VENUE1"):
        entry["VENUE1"] = "LIGHTER"
    if not entry.get("VENUE2"):
        entry["VENUE2"] = "EXTENDED"
    # drop legacy fields
    entry.pop("L", None)
    entry.pop("E", None)
    # ensure id
    if not entry.get("id"):
        entry["id"] = _rand_id()
    return entry


def _pair_dir(symbolL: str, symbolE: str) -> Path:
    return LOG_ROOT / f"{symbolL}:{symbolE}"


def _tmux_session(symbolL: str, symbolE: str) -> str:
    """Session naming: L_<symL>__E_<symE> (underscores only)."""
    safe_l = str(symbolL).replace(":", "_")
    safe_e = str(symbolE).replace(":", "_")
    return f"bot_L_{safe_l}__E_{safe_e}"


def _strip_symbol(value: str | None) -> str:
    if not value:
        return ""
    val = str(value).strip()
    idx = val.find(":")
    return val[idx + 1 :] if idx >= 0 else val


def _load_config_symbols():
    if not CONFIG_PATH.exists():
        return []
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except Exception:
        return []
    symbols = data.get("symbols", []) if isinstance(data, dict) else []
    if not isinstance(symbols, list):
        return []
    return symbols


def _match_config_entry(symbolL: str, symbolE: str):
    norm_l = _strip_symbol(symbolL).upper()
    norm_e = _strip_symbol(symbolE).upper()
    if not norm_l or not norm_e:
        return None
    symbols = _load_config_symbols()
    for entry in symbols:
        if not isinstance(entry, dict):
            continue
        sym1 = _strip_symbol(entry.get("SYM_VENUE1")).upper()
        sym2 = _strip_symbol(entry.get("SYM_VENUE2")).upper()
        if sym1 == norm_l and sym2 == norm_e:
            return entry
        if sym1 == norm_e and sym2 == norm_l:
            return entry
    return None


def _venue_symbol_pair(entry: dict):
    sym1 = _strip_symbol(entry.get("SYM_VENUE1"))
    sym2 = _strip_symbol(entry.get("SYM_VENUE2"))
    venue1 = str(entry.get("VENUE1", "")).upper()
    venue2 = str(entry.get("VENUE2", "")).upper()
    light_sym = sym1 if venue1.startswith("LIGHT") else sym2 if venue2.startswith("LIGHT") else None
    ext_sym = sym2 if venue2.startswith("EXT") else sym1 if venue1.startswith("EXT") else None
    return light_sym, ext_sym, venue1, venue2

def _save_tmux_log(session: str, pane: str = "0") -> None:
    TMUX_LOG_DIR.mkdir(exist_ok=True)
    target = f"{session}:{pane}"
    outfile = TMUX_LOG_DIR / f"tmux_{session}.log"
    try:
        subprocess.check_call(["tmux", "capture-pane", "-t", target, "-S", "-", "-e"])
        subprocess.check_call(["tmux", "save-buffer", str(outfile)])
    finally:
        subprocess.call(["tmux", "delete-buffer"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _gather_server_health() -> dict:
    """Collect lightweight CPU/memory/disk stats for the UI."""
    try:
        per_core = psutil.cpu_percent(interval=0.1, percpu=True)
    except Exception:
        per_core = []
    cpu_percent = round(sum(per_core) / len(per_core), 2) if per_core else round(psutil.cpu_percent(interval=None), 2)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    try:
        load_avg = os.getloadavg()
    except (AttributeError, OSError):
        load_avg = (0.0, 0.0, 0.0)
    uptime = max(0.0, time.time() - psutil.boot_time())
    net = psutil.net_io_counters()
    return {
        "cpu": {
            "percent": cpu_percent,
            "per_core": per_core,
            "count": psutil.cpu_count() or 0,
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "percent": mem.percent,
            "available": mem.available,
        },
        "swap": {
            "total": swap.total,
            "used": swap.used,
            "percent": swap.percent,
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "percent": disk.percent,
            "path": "/",
        },
        "load": load_avg,
        "uptime": uptime,
        "boot_time": psutil.boot_time(),
        "process_count": len(psutil.pids()),
        "net": {
            "bytes_sent": net.bytes_sent if net else 0,
            "bytes_recv": net.bytes_recv if net else 0,
        },
        "timestamp": time.time(),
    }


async def _get_db(mode: str = "live") -> DBClient:
    dsn = DB_TEST_DSN if mode == "test" else DB_DSN
    if not dsn:
        raise HTTPException(status_code=500, detail="DATABASE_URL not set")
    client = await DBClient.get(dsn)
    if client is None:
        raise HTTPException(status_code=500, detail="DB client unavailable")
    return client

# ----------------- Account helpers -----------------

ACCOUNT_FIELD_MAP = {
    "LIGHTER": [
        "LIGHTER_API_PRIVATE_KEY",
        "LIGHTER_ACCOUNT_INDEX",
        "LIGHTER_API_KEY_INDEX",
    ],
    "EXTENDED": [
        "EXTENDED_VAULT_ID",
        "EXTENDED_PRIVATE_KEY",
        "EXTENDED_PUBLIC_KEY",
        "EXTENDED_API_KEY",
    ],
    "HYPERLIQUID": [
        "API_ADDRESS",
        "API_PRIVATE_KEY",
    ],
}

ACCOUNT_TYPE_ALIASES = {
    "LIG": "LIGHTER",
    "EXT": "EXTENDED",
    "HYP": "HYPERLIQUID",
}


def _ensure_env_dir():
    ENV_DIR.mkdir(parents=True, exist_ok=True)


def _slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_") or "account"


def _account_filename(name: str, acc_type: str) -> Path:
    slug = _slugify(name)
    prefix = acc_type.lower()
    return ENV_DIR / f".env_{prefix}_{slug}"


def _list_accounts() -> list[dict]:
    _ensure_env_dir()
    accounts = []
    for path in ENV_DIR.glob(".env_*_*"):
        try:
            parts = path.name.split("_", 2)
            if len(parts) < 3:
                continue
            raw_type = parts[1].upper()
            suffix = parts[2]
            acc_type = raw_type if raw_type in ACCOUNT_FIELD_MAP else ACCOUNT_TYPE_ALIASES.get(raw_type)
            if not acc_type:
                continue
            name = suffix
            if raw_type not in ACCOUNT_FIELD_MAP:
                name = f"{raw_type}_{suffix}" if suffix else raw_type
            accounts.append({"name": name, "type": acc_type})
        except Exception:
            continue
    return accounts


def _write_account(name: str, acc_type: str, values: dict) -> None:
    acc_type_up = acc_type.upper()
    if acc_type_up not in ACCOUNT_FIELD_MAP:
        raise HTTPException(status_code=400, detail="Invalid account type")
    allowed_keys = ACCOUNT_FIELD_MAP[acc_type_up]
    lines = []
    for key in allowed_keys:
        val = values.get(key, "")
        lines.append(f"{key}={val}")
    _ensure_env_dir()
    path = _account_filename(name, acc_type_up)
    path.write_text("\n".join(lines) + "\n")


def _delete_account(name: str, acc_type: str | None = None) -> None:
    _ensure_env_dir()
    targets = []
    if acc_type:
        targets.append(_account_filename(name, acc_type))
    else:
        for t in ENV_DIR.glob(f".env_*_{_slugify(name)}"):
            targets.append(t)
    for t in targets:
        try:
            t.unlink()
        except FileNotFoundError:
            pass


def _tmux_ls() -> List[str]:
    try:
        out = subprocess.check_output(["tmux", "ls"], stderr=subprocess.DEVNULL).decode()
        return [line.split(":")[0] for line in out.splitlines() if line]
    except subprocess.CalledProcessError:
        return []


def _check_token(token: str):
    """Validate base64 user:pass token for websocket auth."""
    try:
        decoded = base64.b64decode(token).decode()
        user_val, password_val = decoded.split(":", 1)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user_expected = os.getenv("AUTH_USER", "admin")
    pass_expected = os.getenv("AUTH_PASS", "admin")
    if user_val != user_expected or password_val != pass_expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user_val


@app.get("/api/accounts")
def get_accounts(user: str = Depends(_auth)):
    try:
        return {"accounts": _list_accounts()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/accounts")
def post_account(payload: dict, user: str = Depends(_auth)):
    try:
        name = str(payload.get("name", "")).strip()
        acc_type = str(payload.get("type", "")).strip().upper()
        values = payload.get("values") or {}
        if not name or not acc_type:
            raise HTTPException(status_code=400, detail="name and type are required")
        _write_account(name, acc_type, values if isinstance(values, dict) else {})
        return {"ok": True, "account": {"name": name, "type": acc_type}}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/accounts/{name}")
def delete_account(name: str, acc_type: Optional[str] = None, user: str = Depends(_auth)):
    try:
        _delete_account(name, acc_type.upper() if acc_type else None)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


async def _send_telegram(msg: str, parse_mode: str | None = "Markdown") -> None:
    """Fire-and-forget Telegram message."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": msg,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    payload["disable_web_page_preview"] = True
    try:
        if TG_TOPIC_ID:
            payload["message_thread_id"] = int(TG_TOPIC_ID)
    except Exception:
        pass
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    body = ""
                    try:
                        body = await resp.text()
                    except Exception:
                        body = "<no body>"
                    print(f"[watchdog] telegram send failed HTTP {resp.status} body={body}")
                    # fallback: retry without parse_mode if HTML/Markdown was rejected
                    if parse_mode is not None and resp.status == 400:
                        payload_no_mode = dict(payload)
                        payload_no_mode.pop("parse_mode", None)
                        async with session.post(url, json=payload_no_mode, timeout=10) as resp2:
                            if resp2.status != 200:
                                body2 = ""
                                try:
                                    body2 = await resp2.text()
                                except Exception:
                                    body2 = "<no body>"
                                print(f"[watchdog] telegram fallback failed HTTP {resp2.status} body={body2}")
    except Exception as exc:
        print(f"[watchdog] telegram send error: {exc}")


async def _db_watchdog_loop():
    """Periodically summarize latest DB activity per symbol and send to Telegram."""
    if not DB_DSN or not WATCHDOG_ENABLED:
        return
    await asyncio.sleep(5)  # give app time to finish startup
    db = await DBClient.get(DB_DSN)
    if db is None:
        return

    def _fmt_lat(val):
        return f"{val:.0f}ms" if val is not None else "n/a"

    def _format_price(val):
        if val is None:
            return "—"
        try:
            return f"{float(val):.6f}"
        except Exception:
            return str(val)

    def _calc_spread_inv(snapshot: dict | None) -> float | None:
        if not snapshot:
            return None
        try:
            qty_v1 = float(snapshot.get("qty_v1") or 0)
            qty_v2 = float(snapshot.get("qty_v2") or 0)
            price_v1 = float(snapshot.get("price_v1") or 0)
            price_v2 = float(snapshot.get("price_v2") or 0)
        except Exception:
            return None
        if qty_v1 > 0 and qty_v2 < 0 and price_v1:
            return (price_v2 - price_v1) / price_v1 * 100
        if qty_v1 < 0 and qty_v2 > 0 and price_v2:
            return (price_v1 - price_v2) / price_v2 * 100
        return None

    def _normalize_inventory_snapshot(value: any) -> dict | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return None
        if isinstance(value, (bytes, bytearray)):
            try:
                return json.loads(value.decode())
            except Exception:
                return None
        return None

    def _format_inventory_lines(snapshot: dict | None) -> list[str]:
        snap = _normalize_inventory_snapshot(snapshot)
        lines = ["Latest Inv"]

        qty_v1 = snap.get("qty_v1")
        price_v1 = snap.get("price_v1")
        qty_v2 = snap.get("qty_v2")
        price_v2 = snap.get("price_v2")
        spread_inv = _calc_spread_inv(snap)

        lines = [
            f"Latest Inv",
            f"V1                    : {qty_v1 if qty_v1 is not None else '—'} @ {_format_price(price_v1)}",
            f"V2                    : {qty_v2 if qty_v2 is not None else '—'} @ {_format_price(price_v2)}",
            f"Δ                     : {f'{spread_inv:.2f}%' if spread_inv is not None else '—'}"
        ]
        return lines

    def _watchdog_lines(row: dict) -> list[str]:
        lines = [
            f"{row.get('bot_name', row.get('pair', 'Unknown'))} ({row.get('bot_id','—')}) - Last 1m",
            f"V1:{row.get('venue1','1')} / V2:{row.get('venue2','2')}",
            "",
            f"Entries 1_2/2_1       : {row.get('entries_1_2', 0)}/{row.get('entries_2_1', 0)}",
            f"Exits 1_2/2_1         : {row.get('exits_1_2', 0)}/{row.get('exits_2_1', 0)}",
            f"Trades 1/2            : {row.get('trades_1', 0)}/{row.get('trades_2', 0)}",
            f"Fills 1/2             : {row.get('fills_1', 0)}/{row.get('fills_2', 0)}",
            f"Avg Lat Orders 1/2    : {_fmt_lat(row.get('avg_lat_order_ms_1'))} / {_fmt_lat(row.get('avg_lat_order_ms_2'))}",
            f"Avg Lat Fills 1/2     : {_fmt_lat(row.get('avg_lat_fill_ms_1'))} / {_fmt_lat(row.get('avg_lat_fill_ms_2'))}",
            "",
        ]
        lines.extend(_format_inventory_lines(row.get("latest_inv_after")))
        return lines

    def _render_watchdog_table(row: dict) -> str:
        body = "\n".join(_watchdog_lines(row))
        return f"<pre>{body}</pre>"

    def _summarize_activity_rows(rows: list[dict], bot_name: str, bot_id: str, venue1: str, venue2: str) -> dict:
        stats = {
            "bot_name": bot_name,
            "bot_id": bot_id,
            "venue1": venue1 or "1",
            "venue2": venue2 or "2",
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

        for row in rows:
            decision = _parse_trace_json(row.get("decision_data")) if row.get("decision_data") else {}
            if decision:
                reason = (decision.get("reason") or "").upper()
                direction = (decision.get("direction") or "").lower()
                ts_val = _parse_number(decision.get("ts"))
                if ts_val and ts_val > latest_ts:
                    latest_ts = ts_val
                    stats["latest_inv_after"] = decision.get("inv_after")
                if reason == "TT_LE":
                    if direction == "entry":
                        stats["entries_1_2"] += 1
                    elif direction == "exit":
                        stats["exits_1_2"] += 1
                if reason == "TT_EL":
                    if direction == "entry":
                        stats["entries_2_1"] += 1
                    elif direction == "exit":
                        stats["exits_2_1"] += 1
            trade1 = _parse_trace_json(row.get("trade_v1")) if row.get("trade_v1") else {}
            if trade1:
                stats["trades_1"] += 1
                lat = _parse_number(trade1.get("lat"))
                if lat is not None:
                    order_lat_sum_1 += lat
                    order_lat_cnt_1 += 1
            trade2 = _parse_trace_json(row.get("trade_v2")) if row.get("trade_v2") else {}
            if trade2:
                stats["trades_2"] += 1
                lat = _parse_number(trade2.get("lat"))
                if lat is not None:
                    order_lat_sum_2 += lat
                    order_lat_cnt_2 += 1
            fill1 = _parse_trace_json(row.get("fill_v1")) if row.get("fill_v1") else {}
            if fill1:
                stats["fills_1"] += 1
                fill_ts = _parse_number(fill1.get("ts"))
                decision_ts = _parse_number(decision.get("ts"))
                if fill_ts is not None and decision_ts is not None:
                    fill_lat_sum_1 += (fill_ts - decision_ts) * 1000
                    fill_lat_cnt_1 += 1
            fill2 = _parse_trace_json(row.get("fill_v2")) if row.get("fill_v2") else {}
            if fill2:
                stats["fills_2"] += 1
                fill_ts = _parse_number(fill2.get("ts"))
                decision_ts = _parse_number(decision.get("ts"))
                if fill_ts is not None and decision_ts is not None:
                    fill_lat_sum_2 += (fill_ts - decision_ts) * 1000
                    fill_lat_cnt_2 += 1
        if order_lat_cnt_1:
            stats["avg_lat_order_ms_1"] = order_lat_sum_1 / order_lat_cnt_1
        if order_lat_cnt_2:
            stats["avg_lat_order_ms_2"] = order_lat_sum_2 / order_lat_cnt_2
        if fill_lat_cnt_1:
            stats["avg_lat_fill_ms_1"] = fill_lat_sum_1 / fill_lat_cnt_1
        if fill_lat_cnt_2:
            stats["avg_lat_fill_ms_2"] = fill_lat_sum_2 / fill_lat_cnt_2
        return stats

    async def _send_initial_activity_sample(symbols: list[dict]):
        def _is_mega(item: dict) -> bool:
            name_val = (item.get("name") or "").upper()
            sym_val = (item.get("SYM_VENUE1") or "").upper()
            return name_val == "MEGA" or sym_val == "MEGA"

        target = next((item for item in symbols if _is_mega(item)), None)
        if not target:
            return
        target_id = target.get("id") or target.get("BOT_ID")
        if not target_id:
            return
        sym_l = target.get("SYM_VENUE1")
        sym_e = target.get("SYM_VENUE2")
        bot_name = f"TT:{sym_l}:{sym_e}" if sym_l and sym_e else target.get("name") or "MEGA"
        try:
            rows = await db.fetch_traces(target_id, limit=5)
        except Exception:
            return
        if not rows:
            return
        venue1 = target.get("VENUE1") or target.get("venue1") or "LIGHTER"
        venue2 = target.get("VENUE2") or target.get("venue2") or "EXTENDED"
        summary_stats = _summarize_activity_rows(rows, bot_name, target_id, venue1, venue2)
        lines = _watchdog_lines(summary_stats)

        try:
            await _send_telegram("<pre>" + "\n".join(lines) + "</pre>", parse_mode="HTML")
        except Exception as exc:
            print(f"[watchdog] initial sample error: {exc}")

    initial_sample_sent = False

    while True:
        try:
            cfg = {}
            if CONFIG_PATH.exists():
                try:
                    cfg = json.loads(CONFIG_PATH.read_text() or "{}")
                except Exception:
                    cfg = {}
            symbols = cfg.get("symbols") if isinstance(cfg, dict) else []
            if not symbols:
                await asyncio.sleep(WATCHDOG_PERIOD)
                continue
            if not initial_sample_sent:
                await _send_initial_activity_sample(symbols)
                initial_sample_sent = True
            now = datetime.now(tz=timezone.utc)
            since = now - timedelta(seconds=60)
            table_rows = []
            for item in symbols:
                sym_l = item.get("SYM_VENUE1")
                sym_e = item.get("SYM_VENUE2")
                if not sym_l or not sym_e:
                    continue
                default_name = f"TT:{sym_l}:{sym_e}"
                bot_name = item.get("name") or default_name
                bot_id = item.get("id") or item.get("BOT_ID") or bot_name
                stats = await db.recent_activity_stats(bot_id, since.timestamp())
                if not stats:
                    continue
                activity_count = sum(
                    stats.get(key, 0)
                    for key in [
                        "entries_1_2",
                        "entries_2_1",
                        "exits_1_2",
                        "exits_2_1",
                        "trades_1",
                        "trades_2",
                        "fills_1",
                        "fills_2",
                    ]
                )
                if activity_count <= 0:
                    continue
                table_rows.append(
                    {
                        "bot_id": bot_id,
                        "bot_name": bot_name,
                        "venue1": item.get("VENUE1") or item.get("venue1") or "1",
                        "venue2": item.get("VENUE2") or item.get("venue2") or "2",
                        "entries_1_2": stats.get("entries_1_2", 0),
                        "entries_2_1": stats.get("entries_2_1", 0),
                        "exits_1_2": stats.get("exits_1_2", 0),
                        "exits_2_1": stats.get("exits_2_1", 0),
                        "trades_1": stats.get("trades_1", 0),
                        "trades_2": stats.get("trades_2", 0),
                        "fills_1": stats.get("fills_1", 0),
                        "fills_2": stats.get("fills_2", 0),
                        "avg_lat_order_ms_1": stats.get("avg_lat_order_ms_1"),
                        "avg_lat_order_ms_2": stats.get("avg_lat_order_ms_2"),
                        "avg_lat_fill_ms_1": stats.get("avg_lat_fill_ms_1"),
                        "avg_lat_fill_ms_2": stats.get("avg_lat_fill_ms_2"),
                        "latest_inv_after": stats.get("latest_inv_after"),
                    }
                )

            if not table_rows:
                await asyncio.sleep(WATCHDOG_PERIOD)
                continue
            # send one message per symbol to keep tables short and avoid formatting errors
            for row in table_rows:
                msg = _render_watchdog_table(row)
                await _send_telegram(msg, parse_mode="HTML")
        except Exception as exc:
            print(f"[watchdog] loop error: {exc}")
        await asyncio.sleep(WATCHDOG_PERIOD)


@app.get("/api/auth_check")
def auth_check(user: str = Depends(_auth)):
    return {"ok": True, "user": user}


@app.get("/api/config")
def get_config(user: str = Depends(_auth)):
    if not CONFIG_PATH.exists():
        return {"symbols": []}
    try:
        data = json.loads(CONFIG_PATH.read_text())
        symbols = data.get("symbols", []) if isinstance(data, dict) else []
        changed = False
        for sym in symbols:
            if not isinstance(sym, dict):
                continue
            prev_id = sym.get("id")
            had_LE = "L" in sym or "E" in sym
            _ensure_le(sym)
            if (not prev_id and sym.get("id")) or had_LE:
                changed = True
        if changed and isinstance(data, dict):
            data["symbols"] = symbols
            try:
                CONFIG_PATH.write_text(json.dumps(data, indent=2))
            except Exception:
                pass
        if isinstance(data, dict):
            data["symbols"] = symbols
            return data
        return {"symbols": symbols}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/config")
def put_config(payload: dict, user: str = Depends(_auth)):
    try:
        symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if isinstance(symbols, list):
            for sym in symbols:
                _ensure_le(sym if isinstance(sym, dict) else {})
        CONFIG_PATH.write_text(json.dumps(payload, indent=2))
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/symbols")
def add_symbol(payload: dict, user: str = Depends(_auth)):
    try:
        config = {"symbols": []}
        if CONFIG_PATH.exists():
            config = json.loads(CONFIG_PATH.read_text() or "{}")
            if not isinstance(config, dict):
                config = {"symbols": []}
        symbols = config.get("symbols") or []
        if not isinstance(symbols, list):
            symbols = []

        new_sym = payload or {}
        sym_l = str(new_sym.get("SYM_VENUE1", "")).strip()
        sym_e = str(new_sym.get("SYM_VENUE2", "")).strip()
        if not sym_l or not sym_e:
            raise HTTPException(status_code=400, detail="SYM_VENUE1 and SYM_VENUE2 are required")
        if any(s.get("SYM_VENUE1") == sym_l and s.get("SYM_VENUE2") == sym_e for s in symbols):
            raise HTTPException(status_code=400, detail="Symbol already exists")

        _ensure_le(new_sym)
        symbols.append(new_sym)
        config["symbols"] = symbols
        CONFIG_PATH.write_text(json.dumps(config, indent=2))
        return {"ok": True, "symbols": symbols}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/symbols")
def get_symbols(user: str = Depends(_auth)):
    sessions = _tmux_ls()
    running = [s.replace("bot_", "", 1) for s in sessions if s.startswith("bot_")]
    return {"running": running}


@app.post("/api/start")
def start_bot(symbolL: str, symbolE: str, user: str = Depends(_auth)):
    sym_l = _strip_symbol(symbolL)
    sym_e = _strip_symbol(symbolE)
    session_sym_l = sym_l
    session_sym_e = sym_e
    module = "bot.core.tt_bot_lig_ext"
    entry = _match_config_entry(sym_l, sym_e)
    if entry:
        light_sym, ext_sym, _, _ = _venue_symbol_pair(entry)
        if light_sym and ext_sym:
            session_sym_l = light_sym
            session_sym_e = ext_sym
    if not session_sym_l:
        session_sym_l = sym_l
    if not session_sym_e:
        session_sym_e = sym_e
    session = _tmux_session(session_sym_l, session_sym_e)
    if session in _tmux_ls():
        return {"ok": True, "msg": "already running"}
    cmd = f"cd {ROOT} && {PYTHON_BIN} -m {module} {session_sym_l} {session_sym_e}"
    try:
        subprocess.check_call(["tmux", "new-session", "-d", "-s", session, cmd])
        return {"ok": True}
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"tmux start failed: {exc}")


@app.post("/api/stop")
def stop_bot(symbolL: str, symbolE: str, user: str = Depends(_auth)):
    sym_l = _strip_symbol(symbolL)
    sym_e = _strip_symbol(symbolE)
    session_sym_l = sym_l
    session_sym_e = sym_e
    entry = _match_config_entry(sym_l, sym_e)
    if entry:
        light_sym, ext_sym, _, _ = _venue_symbol_pair(entry)
        if light_sym and ext_sym:
            session_sym_l = light_sym
            session_sym_e = ext_sym
    if not session_sym_l:
        session_sym_l = sym_l
    if not session_sym_e:
        session_sym_e = sym_e
    session = _tmux_session(session_sym_l, session_sym_e)
    if session not in _tmux_ls():
        return {"ok": True, "msg": "not running"}
    try:
        try:
            _save_tmux_log(session)
        except Exception as exc:
            print(f"[stop_bot] failed to capture logs for {session}: {exc}")
        subprocess.check_call(["tmux", "kill-session", "-t", session])
        return {"ok": True}
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"tmux stop failed: {exc}")


@app.on_event("startup")
async def _start_watchdog():
    if WATCHDOG_ENABLED and TG_TOKEN and TG_CHAT_ID:
        print("[watchdog] starting db watchdog task")
        # await _send_telegram("DB WATCHDOG is READY")
        asyncio.create_task(_db_watchdog_loop())
    else:
        print("[watchdog] disabled (set DB_WATCHDOG_ENABLED=true and TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")


def _read_log(symbolL: str, symbolE: str, fname: str, tail: int = 4000) -> str:
    path = _pair_dir(symbolL, symbolE) / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail="log not found")
    text = path.read_text(errors="ignore")
    return text[-tail:]


@app.get("/api/logs/{symbolL}/{symbolE}/{logname}")
def get_log(symbolL: str, symbolE: str, logname: str, user: str = Depends(_auth)):
    allowed = {"maker": "maker.log", "realtime": "realtime.log", "spread": "spread.log"}
    if logname not in allowed:
        raise HTTPException(status_code=400, detail="invalid log")
    data = _read_log(symbolL, symbolE, allowed[logname])
    return PlainTextResponse(data)


@app.get("/api/logs/{symbolL}/{symbolE}/realtime/stream")
async def stream_realtime(symbolL: str, symbolE: str, user: str = Depends(_auth)):
    path = _pair_dir(symbolL, symbolE) / "realtime.log"
    if not path.exists():
        raise HTTPException(status_code=404, detail="log not found")

    tail_lines = _read_log(symbolL, symbolE, "realtime.log").splitlines()
    tail = tail_lines[-400:] if tail_lines else []

    async def event_stream():
        for line in tail:
            yield line + "\n"
        with path.open("r") as fh:
            fh.seek(0, os.SEEK_END)
            while True:
                line = fh.readline()
                if line:
                    yield line
                else:
                    await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/plain")


async def _ws_poll_stream(symbolL: str, symbolE: str, filename: str, websocket: WebSocket, token: Optional[str], max_bytes: int = 4000):
    try:
        if not token:
            await websocket.close(code=1008)
            return
        _check_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    path = _pair_dir(symbolL, symbolE) / filename
    last_sent: Optional[str] = None
    try:
        while True:
            if not path.exists():
                await websocket.send_text("log not found")
                await asyncio.sleep(1.0)
                continue
            try:
                payload = path.read_text(errors="ignore")
            except Exception:
                await asyncio.sleep(1.0)
                continue
            if max_bytes and len(payload) > max_bytes:
                payload = payload[-max_bytes:]
            if payload != last_sent:
                last_sent = payload
                await websocket.send_text(payload)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


@app.websocket("/ws/logs/{symbolL}/{symbolE}/realtime")
async def websocket_realtime(symbolL: str, symbolE: str, websocket: WebSocket, token: Optional[str] = None):
    """Stream the latest realtime snapshot; file is overwritten on each log."""
    return await _ws_poll_stream(symbolL, symbolE, "realtime.log", websocket, token, max_bytes=2000)


@app.websocket("/ws/logs/{symbolL}/{symbolE}/{logname}")
async def websocket_logs(symbolL: str, symbolE: str, logname: str, websocket: WebSocket, token: Optional[str] = None):
    allowed = {"realtime": "realtime.log", "maker": "maker.log", "spread": "spread.log"}
    if logname not in allowed:
        await websocket.close(code=1008)
        return
    filename = allowed[logname]
    # poll-based stream works with prepend/overwrite handlers
    return await _ws_poll_stream(symbolL, symbolE, filename, websocket, token)


@app.get("/api/trades/{symbolL}/{symbolE}")
def trades(symbolL: str, symbolE: str, user: str = Depends(_auth)):
    path = _pair_dir(symbolL, symbolE) / "trades.csv"
    if not path.exists():
        return {"rows": []}
    rows = path.read_text().splitlines()
    if not rows:
        return {"rows": []}
    header, *body = rows
    parsed = []
    for line in body:
        parsed.append({"raw": line})
    return {"header": header, "rows": parsed}


@app.get("/api/trades/{symbolL}/{symbolE}/csv")
def trades_csv(symbolL: str, symbolE: str, user: str = Depends(_auth)):
    path = _pair_dir(symbolL, symbolE) / "trades.csv"
    if not path.exists():
        raise HTTPException(status_code=404)
    return PlainTextResponse(path.read_text(), media_type="text/csv")


@app.get("/api/server/health")
def api_server_health(user: str = Depends(_auth)):
    return _gather_server_health()


@app.get("/api/tt/decisions")
async def api_tt_decisions(symbolL: str, symbolE: str, mode: str = "live", limit: int = 200, user: str = Depends(_auth)):
    db = await _get_db(mode)
    bot_name = f"TT:{symbolL}:{symbolE}"
    records = await db.fetch_decisions(bot_name=bot_name, limit=limit)
    rows = []
    for r in records:
        rows.append(
            {
                "trace_id": r.get("trace"),
                "ts": r.get("ts"),
                "reason": r.get("reason"),
                "direction": r.get("direction"),
                "spread_signal": r.get("spread_signal"),
                "size": r.get("size"),
                "ob_l": r.get("ob_l"),
                "ob_e": r.get("ob_e"),
                "inv_before_str": r.get("inv_before"),
                "inv_after_str": r.get("inv_after"),
            }
        )
    return {"rows": rows}


def _parse_bot_name(bot_name: str):
    try:
        parts = bot_name.split(":")
        if len(parts) >= 3 and parts[0] == "TT":
            return parts[1], parts[2]
    except Exception:
        pass
    return None, None


@app.get("/api/tt/decisions_all")
async def api_tt_decisions_all(mode: str = "live", limit: int = 200, user: str = Depends(_auth)):
    db = await _get_db(mode)
    records = await db.fetch_decisions_all(limit=limit)
    rows = []
    for r in records:
        symL, symE = _parse_bot_name(r.get("bot_name", ""))
        rows.append(
            {
                "trace_id": r.get("trace"),
                "ts": r.get("ts"),
                "reason": r.get("reason"),
                "direction": r.get("direction"),
                "spread_signal": r.get("spread_signal"),
                "size": r.get("size"),
                "ob_l": r.get("ob_l"),
                "ob_e": r.get("ob_e"),
                "inv_before_str": r.get("inv_before"),
                "inv_after_str": r.get("inv_after"),
                "bot_name": r.get("bot_name"),
                "symbolL": symL,
                "symbolE": symE,
            }
        )
    return {"rows": rows}


@app.get("/api/tt/trades")
async def api_tt_trades(symbolL: str, symbolE: str, mode: str = "live", limit: int = 200, user: str = Depends(_auth)):
    db = await _get_db(mode)
    bot_name = f"TT:{symbolL}:{symbolE}"
    records = await db.fetch_trades(bot_name=bot_name, limit=limit)
    rows = []
    for r in records:
        rows.append(
            {
                "trace": r.get("trace"),
                "ts": r.get("ts"),
                "venue": r.get("venue"),
                "size": r.get("size"),
                "ob_price": r.get("ob_price"),
                "exec_price": r.get("exec_price"),
                "lat_order": r.get("lat_order"),
                "status": r.get("status"),
                "payload": r.get("payload"),
                "resp": r.get("resp"),
                "reason": r.get("reason"),
                "direction": r.get("direction"),
            }
        )
    return {"rows": rows}


@app.get("/api/tt/trades_all")
async def api_tt_trades_all(mode: str = "live", limit: int = 200, user: str = Depends(_auth)):
    db = await _get_db(mode)
    records = await db.fetch_trades_all(limit=limit)
    rows = []
    for r in records:
        symL, symE = _parse_bot_name(r.get("bot_name", ""))
        rows.append(
            {
                "trace": r.get("trace"),
                "ts": r.get("ts"),
                "venue": r.get("venue"),
                "size": r.get("size"),
                "ob_price": r.get("ob_price"),
                "exec_price": r.get("exec_price"),
                "lat_order": r.get("lat_order"),
                "status": r.get("status"),
                "payload": r.get("payload"),
                "resp": r.get("resp"),
                "reason": r.get("reason"),
                "direction": r.get("direction"),
                "bot_name": r.get("bot_name"),
                "symbolL": symL,
                "symbolE": symE,
            }
        )
    return {"rows": rows}


@app.get("/api/tt/fills")
async def api_tt_fills(symbolL: str, symbolE: str, mode: str = "live", limit: int = 200, user: str = Depends(_auth)):
    db = await _get_db(mode)
    bot_name = f"TT:{symbolL}:{symbolE}"
    records = await db.fetch_fills(bot_name=bot_name, limit=limit)
    rows = []
    for r in records:
        rows.append(
            {
                "trace": r.get("trace"),
                "ts": r.get("ts"),
                "venue": r.get("venue"),
                "base_amount": r.get("base_amount"),
                "fill_price": r.get("fill_price"),
                "latency": r.get("latency"),
            }
        )
    return {"rows": rows}


@app.get("/api/tt/fills_all")
async def api_tt_fills_all(mode: str = "live", limit: int = 200, user: str = Depends(_auth)):
    db = await _get_db(mode)
    records = await db.fetch_fills_all(limit=limit)
    rows = []
    for r in records:
        symL, symE = _parse_bot_name(r.get("bot_name", ""))
        rows.append(
            {
                "trace": r.get("trace"),
                "ts": r.get("ts"),
                "venue": r.get("venue"),
                "base_amount": r.get("base_amount"),
                "fill_price": r.get("fill_price"),
                "latency": r.get("latency"),
                "bot_name": r.get("bot_name"),
                "symbolL": symL,
                "symbolE": symE,
            }
        )
    return {"rows": rows}


@app.get("/api/tt/activities")
async def api_tt_activities(botId: str | None = None, mode: str = "live", limit: int = 200, offset: int = 0, user: str = Depends(_auth)):
    db = await _get_db(mode)
    if botId:
        records = await db.fetch_traces(botId, limit=limit, offset=offset)
    else:
        records = await db.fetch_traces_all(limit=limit, offset=offset)
    rows = []
    for r in records:
        rows.append(
            {
                "bot_id": r.get("bot_id"),
                "trace": r.get("trace"),
                "bot_configs": _parse_trace_json(r.get("bot_configs")),
                "decision_data": _parse_trace_json(r.get("decision_data")),
                "decision_ob_v1": _parse_trace_json(r.get("decision_ob_v1")),
                "decision_ob_v2": _parse_trace_json(r.get("decision_ob_v2")),
                "trade_v1": _parse_trace_json(r.get("trade_v1")),
                "trade_v2": _parse_trace_json(r.get("trade_v2")),
                "fill_v1": _parse_trace_json(r.get("fill_v1")),
                "fill_v2": _parse_trace_json(r.get("fill_v2")),
            }
        )
    return {"rows": rows}


@app.get("/api/env")
def get_env(user: str = Depends(_auth)):
    env_path = ENV_PATH
    if not env_path.exists():
        return PlainTextResponse("", media_type="text/plain")
    return PlainTextResponse(env_path.read_text())


@app.put("/api/env")
def put_env(body: dict, user: str = Depends(_auth)):
    env_path = ENV_PATH
    text = body.get("text", "")
    env_path.write_text(text)
    return {"ok": True}


# Convenience: encode auth for SSE URLs (frontend can also send Authorization header)
@app.get("/api/auth_token")
def auth_token(user: str = Depends(_auth)):
    token = base64.b64encode(f"{user}:{os.getenv('AUTH_PASS','admin')}".encode()).decode()
    return {"token": token}

def _parse_trace_json(value):
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        raw = value.decode() if isinstance(value, bytes) else value
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return value
