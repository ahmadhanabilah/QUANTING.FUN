import base64
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Response, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials


app = FastAPI(title="arb_bot control")
security = HTTPBasic()

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
LOG_ROOT = ROOT / "logs"
ENV_PATH = ROOT / ".env"


def _load_env():
    """Minimal .env loader to populate os.environ."""
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


_load_env()

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
    cmd = f"cd {ROOT} && python -m core.tt_runner {symbolL} {symbolE}"
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


@app.get("/api/env")
def get_env(user: str = Depends(_auth)):
    env_path = ROOT / ".env"
    if not env_path.exists():
        return PlainTextResponse("", media_type="text/plain")
    return PlainTextResponse(env_path.read_text())


@app.put("/api/env")
def put_env(body: dict, user: str = Depends(_auth)):
    env_path = ROOT / ".env"
    text = body.get("text", "")
    env_path.write_text(text)
    return {"ok": True}


# Convenience: encode auth for SSE URLs (frontend can also send Authorization header)
@app.get("/api/auth_token")
def auth_token(user: str = Depends(_auth)):
    token = base64.b64encode(f"{user}:{os.getenv('AUTH_PASS','admin')}".encode()).decode()
    return {"token": token}
