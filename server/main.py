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
ENV_PATH = ROOT / ".env_server"
ENV_BOT_PATH = ROOT / ".env_bot"
VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = str(VENV_PY) if VENV_PY.exists() else "python3"


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
_load_env(ENV_BOT_PATH)
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


def _pair_dir(symbolL: str, symbolE: str) -> Path:
    return LOG_ROOT / f"{symbolL}:{symbolE}"


def _tmux_session(symbolL: str, symbolE: str) -> str:
    return f"bot_{symbolL}_{symbolE}"


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

    def _render_watchdog_table(row: dict) -> str:
        inv_lines = row.get("inv_lines", [])
        lines = [
            f"{row['pair']} - Last 1m",
            "",
            f"Entries LE/EL   : {row['entries']}",
            f"Exits LE/EL     : {row['exits']}",
            f"Trades L/E      : {row['trades']}",
            f"Fills L/E       : {row['fills']}",
            f"Lat Orders L/E  : {row['lat_orders']}",
            f"Lat Fills L/E   : {row['lat_fills']}",
        ]
        if inv_lines:
            lines.append("")
            lines.append("Latest Inv")
            lines.extend(inv_lines)
        body = "\n".join(lines)
        return f"<pre>{body}</pre>"

    # # Send a one-time sample so formatting is obvious
    # try:
    #     sample_row = {
    #         "pair": "SAMPLE:PAIR",
    #         "entries": "2/1",
    #         "exits": "1/0",
    #         "trades": "3/1",
    #         "fills": "2/1",
    #         "lat_orders": "62ms / 410ms",
    #         "lat_fills": "180ms / 200ms",
    #         "inv_lines": [
    #             "E -> Qty: -0.001, Price: 93273.0",
    #             "L -> Qty:  0.001, Price: 93254.0",
    #             "Δ -> -0.02%",
    #         ],
    #     }
    #     await _send_telegram(_render_watchdog_table(sample_row), parse_mode="HTML")
    # except Exception:
    #     pass

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
            now = datetime.now(tz=timezone.utc)
            since = now - timedelta(seconds=60)
            table_rows = []
            for item in symbols:
                sym_l = item.get("SYMBOL_LIGHTER")
                sym_e = item.get("SYMBOL_EXTENDED")
                if not sym_l or not sym_e:
                    continue
                bot_name = f"TT:{sym_l}:{sym_e}"
                summary = await db.recent_summary(bot_name, since)
                # only notify if there are trades in the last minute
                if summary.get("trades_1m", 0) <= 0:
                    continue
                inv_str = summary.get("latest_inv_after") or summary.get("latest_inv_before") or "n/a"
                avg_o = summary.get("avg_lat_order_ms")
                avg_f = summary.get("avg_lat_fill_ms")
                inv_lines = []
                if isinstance(inv_str, str):
                    parts = [p.strip() for p in inv_str.split("|") if p.strip()]
                    for part in parts:
                        if part.startswith("E ->") or part.startswith("L ->"):
                            # strip order/fill details, keep qty/price only
                            tokens = [t.strip() for t in part.split(",") if t.strip()]
                            trimmed = []
                            for tok in tokens:
                                if tok.lower().startswith("order") or tok.lower().startswith("fill"):
                                    continue
                                trimmed.append(tok)
                            inv_lines.append(", ".join(trimmed))
                        elif "Δ" in part:
                            inv_lines.append(part)
                entries_le = summary.get("entries_le", 0)
                entries_el = summary.get("entries_el", 0)
                exits_le = summary.get("exits_le", 0)
                exits_el = summary.get("exits_el", 0)
                trades_L = summary.get("trades_L", 0)
                trades_E = summary.get("trades_E", 0)
                fills_L = summary.get("fills_L", 0)
                fills_E = summary.get("fills_E", 0)
                avg_o_L = summary.get("avg_lat_order_ms_L")
                avg_o_E = summary.get("avg_lat_order_ms_E")
                avg_f_L = summary.get("avg_lat_fill_ms_L")
                avg_f_E = summary.get("avg_lat_fill_ms_E")

                table_rows.append(
                    {
                        "pair": f"{sym_l}:{sym_e}",
                        "entries": f"{entries_le}/{entries_el}",
                        "exits": f"{exits_le}/{exits_el}",
                        "trades": f"{trades_L}/{trades_E}",
                        "fills": f"{fills_L}/{fills_E}",
                        "lat_orders": f"{_fmt_lat(avg_o_L)} / {_fmt_lat(avg_o_E)}",
                        "lat_fills": f"{_fmt_lat(avg_f_L)} / {_fmt_lat(avg_f_E)}",
                        "inv_lines": inv_lines,
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
        return json.loads(CONFIG_PATH.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/config")
def put_config(payload: dict, user: str = Depends(_auth)):
    try:
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
        sym_l = str(new_sym.get("SYMBOL_LIGHTER", "")).strip()
        sym_e = str(new_sym.get("SYMBOL_EXTENDED", "")).strip()
        if not sym_l or not sym_e:
            raise HTTPException(status_code=400, detail="SYMBOL_LIGHTER and SYMBOL_EXTENDED are required")
        if any(s.get("SYMBOL_LIGHTER") == sym_l and s.get("SYMBOL_EXTENDED") == sym_e for s in symbols):
            raise HTTPException(status_code=400, detail="Symbol already exists")

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
    session = _tmux_session(symbolL, symbolE)
    if session in _tmux_ls():
        return {"ok": True, "msg": "already running"}
    cmd = f"cd {ROOT} && {PYTHON_BIN} -m bot.core.tt_runner {symbolL} {symbolE}"
    try:
        subprocess.check_call(["tmux", "new-session", "-d", "-s", session, cmd])
        return {"ok": True}
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail=f"tmux start failed: {exc}")


@app.post("/api/stop")
def stop_bot(symbolL: str, symbolE: str, user: str = Depends(_auth)):
    session = _tmux_session(symbolL, symbolE)
    if session not in _tmux_ls():
        return {"ok": True, "msg": "not running"}
    try:
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
                "ob_l": r.get("ob_l"),
                "ob_e": r.get("ob_e"),
                "inv_before_str": r.get("inv_before"),
                "inv_after_str": r.get("inv_after"),
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


@app.get("/api/env")
def get_env(user: str = Depends(_auth)):
    env_path = ROOT / ".env_bot"
    if not env_path.exists():
        return PlainTextResponse("", media_type="text/plain")
    return PlainTextResponse(env_path.read_text())


@app.put("/api/env")
def put_env(body: dict, user: str = Depends(_auth)):
    env_path = ROOT / ".env_bot"
    text = body.get("text", "")
    env_path.write_text(text)
    return {"ok": True}


# Convenience: encode auth for SSE URLs (frontend can also send Authorization header)
@app.get("/api/auth_token")
def auth_token(user: str = Depends(_auth)):
    token = base64.b64encode(f"{user}:{os.getenv('AUTH_PASS','admin')}".encode()).decode()
    return {"token": token}
