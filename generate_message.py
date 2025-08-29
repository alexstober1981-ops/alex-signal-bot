#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Alex Signal Bot – generate_message.py (Fallback 3-Way, 2025-08)
- Provider-Kette wählbar: BinanceUS / Binance.com / Bybit / OKX
- Robust gegen 451 (Geo-Block), Netzwerkfehler, wenige Kerzen
- Kompatibel zu deinem Setup: coins.json, message.txt, alerts.txt, signal_state.json, CSV-Log
"""

import os, json, time, math, csv, sys
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

import requests

# ================== EINSTELLUNGEN ==================

ROOT = os.path.dirname(os.path.abspath(__file__))
MSG_PATH     = os.path.join(ROOT, "message.txt")
ALERTS_PATH  = os.path.join(ROOT, "alerts.txt")
STATE_PATH   = os.path.join(ROOT, "signal_state.json")
CSV_PATH     = os.path.join(ROOT, "signal_log.csv")

# Coins-Datei: nimmt ENV, sonst coins.json, sonst coins_balanced.json (falls vorhanden)
COINS_FILE_ENV = os.getenv("COINS_FILE", "").strip()
if COINS_FILE_ENV:
    COINS_PATH = os.path.join(ROOT, COINS_FILE_ENV)
elif os.path.exists(os.path.join(ROOT, "coins.json")):
    COINS_PATH = os.path.join(ROOT, "coins.json")
elif os.path.exists(os.path.join(ROOT, "coins_balanced.json")):
    COINS_PATH = os.path.join(ROOT, "coins_balanced.json")
else:
    COINS_PATH = os.path.join(ROOT, "coins.json")  # fallback

# 👉 HIER stellst du die Provider-Reihenfolge ein:
# Variante A (empfohlen): BinanceUS → Binance.com → Bybit → OKX
PROVIDERS = ["binance_us", "binance_com", "bybit", "okx"]

# Du kannst später mit ENV übersteuern (z.B. in Actions):
# PROVIDER_CHAIN="bybit,binance_com,okx"
ENV_CHAIN = os.getenv("PROVIDER_CHAIN", "").strip()
if ENV_CHAIN:
    PROVIDERS = [p.strip() for p in ENV_CHAIN.split(",") if p.strip()]

# Minimal benötigte Kerzen pro TF
NEEDED_KLINES = {"5m": 120, "15m": 120}

# Cooldown in Minuten (ENV, sonst 0 = aus)
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "0"))

# HTTP Defaults
UA = {"User-Agent": "AlexSignalBot/1.0 (+github actions)"}
TIMEOUT = 15
RETRY_DELAYS = [0.0, 0.7, 1.2]

# ================== HILFSFUNKTIONEN ==================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def ts_ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")

def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_state() -> Dict[str, Any]:
    return read_json(STATE_PATH, default={})

def append_csv_row(ts: datetime, symbol: str, level: str, ch5: float, ch15: float, price: float) -> None:
    row = {
        "ts": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "symbol": symbol,
        "level": level,
        "chg_5m_pct": round(ch5, 3),
        "chg_15m_pct": round(ch15, 3),
        "price": round(price, 8),
    }
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)

def pct(a: float, b: float) -> float:
    return 0.0 if b == 0 else (a / b - 1.0) * 100.0

# ================== PROVIDER-FETCH ==================

# Bybit Interval-Mapping
_BYBIT_INT = {"1m":"1", "3m":"3", "5m":"5", "15m":"15"}
# OKX Interval-Mapping (OKX nennt 5m „5m“, passt)
_OKX_INT = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m"}

def _http_get(url: str, params: Optional[Dict[str,Any]]=None, headers: Optional[Dict[str,str]]=None) -> requests.Response:
    headers = headers or UA
    last = None
    for d in RETRY_DELAYS:
        if d: time.sleep(d)
        try:
            r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            return r
        except Exception as e:
            last = e
    if last: raise last
    raise RuntimeError("HTTP GET failed")

def _binance_klines(host: str, pair: str, interval: str, limit: int):
    url = f"https://{host}/api/v3/klines"
    r = _http_get(url, {"symbol": pair, "interval": interval, "limit": limit})
    if r.status_code == 451:
        raise requests.HTTPError("451 blocked", response=r)
    r.raise_for_status()
    data = r.json()
    out: List[Tuple[datetime,float,float,float,float]] = []
    for c in data:
        if len(c) < 6: continue
        dt = ts_ms_to_dt(int(c[0]))
        o,h,l,cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        if any(math.isnan(x) for x in (o,h,l,cl)): continue
        out.append((dt,o,h,l,cl))
    return out

def _bybit_klines(pair: str, interval: str, limit: int):
    iv = _BYBIT_INT.get(interval)
    if not iv: raise ValueError(f"Bybit interval unsupported: {interval}")
    url = "https://api.bybit.com/v5/market/kline"
    params = {"category":"spot", "symbol":pair, "interval":iv, "limit": str(min(limit,200))}
    r = _http_get(url, params)
    r.raise_for_status()
    js = r.json()
    if js.get("retCode",0) != 0: raise RuntimeError(f"Bybit error: {js.get('retMsg')}")
    rows = js.get("result",{}).get("list",[])
    out: List[Tuple[datetime,float,float,float,float]] = []
    for x in rows:
        dt = ts_ms_to_dt(int(x[0]))
        o,h,l,cl = float(x[1]), float(x[2]), float(x[3]), float(x[4])
        out.append((dt,o,h,l,cl))
    out.sort(key=lambda z: z[0])  # alt -> neu
    return out

def _to_okx_inst(pair: str) -> str:
    # "BTCUSDT" -> "BTC-USDT", "SEIUSDC" -> "SEI-USDC"
    quotes = ("USDT","USDC","BTC","ETH")
    for q in quotes:
        if pair.endswith(q):
            base = pair[:-len(q)]
            if not base:
                break
            return f"{base}-{q}"
    # fallback (schlecht, aber besser als nix)
    return f"{pair[:-4]}-{pair[-4:]}"

def _okx_klines(pair: str, interval: str, limit: int):
    bar = _OKX_INT.get(interval, "5m")
    inst = _to_okx_inst(pair)
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": inst, "bar": bar, "limit": str(min(limit,300))}
    r = _http_get(url, params)
    r.raise_for_status()
    js = r.json()
    if js.get("code") not in ("0", 0, None):
        # OKX manchmal "0" als str
        if js.get("code") != "0":
            raise RuntimeError(f"OKX error: {js}")
    rows = js.get("data", [])
    out: List[Tuple[datetime,float,float,float,float]] = []
    for x in rows:
        # OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, ...] -> neu->alt
        dt = ts_ms_to_dt(int(x[0]))
        o,h,l,cl = float(x[1]), float(x[2]), float(x[3]), float(x[4])
        out.append((dt,o,h,l,cl))
    out.sort(key=lambda z: z[0])
    return out

def fetch_klines(pair: str, interval: str, limit: int) -> List[Tuple[datetime,float,float,float,float]]:
    last_err = None
    for p in PROVIDERS:
        try:
            if p == "binance_us":
                out = _binance_klines("api.binance.us", pair, interval, limit)
            elif p == "binance_com":
                out = _binance_klines("api.binance.com", pair, interval, limit)
            elif p == "bybit":
                out = _bybit_klines(pair, interval, limit)
            elif p == "okx":
                out = _okx_klines(pair, interval, limit)
            else:
                continue
            if len(out) >= max(NEEDED_KLINES.get(interval, 60), 50):
                return out
            raise RuntimeError(f"{p}: too few klines ({len(out)})")
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All providers failed for {pair} {interval}: {last_err}")

# ================== ANALYSE ==================

def analyze_coin(symbol: str, pair: str, th: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    # 5m & 15m
    k5  = fetch_klines(pair, "5m",  NEEDED_KLINES["5m"])
    k15 = fetch_klines(pair, "15m", NEEDED_KLINES["15m"])

    c5 = [x[4] for x in k5]
    c15 = [x[4] for x in k15]

    price = c5[-1]
    ch5  = pct(c5[-1], c5[-6]) if len(c5) >= 6 else 0.0
    ch15 = pct(c15[-1], c15[-2]) if len(c15) >= 2 else 0.0

    # Schwellen
    move = (th.get("move") or {})
    info = (th.get("info") or {})

    th5_move  = float(move.get("5m", 0.8))
    th15_move = float(move.get("15m", 1.2))
    th5_info  = float(info.get("5m", 0.3))
    th15_info = float(info.get("15m", 0.6))

    # Level bestimmen
    level = "hold"; emoji = "🟡"; label = "HOLD"
    if abs(ch5) >= th5_move or abs(ch15) >= th15_move:
        level = "signal"; emoji = "📈" if (ch5>0 or ch15>0) else "📉"; label="SIGNAL"
    if abs(ch5) >= th5_info*2 or abs(ch15) >= th15_info*2:
        # stärkere Info ~ Richtung Alert (ohne 24h)
        level = "alert"; emoji = "🚀" if (ch5>0 or ch15>0) else "🔻"; label="ALERT"

    line = f"{emoji} {symbol}: ${price:,.4f} • 15m {ch15:+.2f}% • 5m {ch5:+.2f}% — {label}"
    meta = {"level": level, "price": price, "ch5": round(ch5,2), "ch15": round(ch15,2)}
    return line, meta

# ================== MESSAGES ==================

def load_coins() -> List[Dict[str, Any]]:
    data = read_json(COINS_PATH, [])
    if not data or not isinstance(data, list):
        raise ValueError(f"{COINS_PATH} ist leer/ungültig.")
    out = []
    for c in data:
        sym = c.get("symbol"); pair = c.get("binance") or c.get("pair")
        th = c.get("thresholds", {})
        if sym and (pair):
            out.append({"symbol": sym, "pair": pair.upper(), "thresholds": th})
    if not out: raise ValueError("Keine gültigen Coins in coins.json")
    return out

def cooldown_ok(state: Dict[str, Any], symbol: str, now: datetime) -> bool:
    if COOLDOWN_MINUTES <= 0: return True
    last = ((state.get("coins") or {}).get(symbol) or {}).get("last_ts")
    if not last: return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return (now - last_dt) >= timedelta(minutes=COOLDOWN_MINUTES)

def mark_sent(state: Dict[str, Any], symbol: str, now: datetime, level: str) -> None:
    state.setdefault("coins", {}).setdefault(symbol, {})
    state["coins"][symbol]["last_ts"] = now.isoformat()
    state["coins"][symbol]["last_level"] = level

def build_messages() -> None:
    now = utc_now()
    coins = load_coins()
    state = load_state()

    lines: List[str] = []
    alerts: List[str] = []

    header = f"📊 Signal Snapshot — {now.strftime('%Y-%m-%d %H:%M')} UTC\nQuelle: { ' > '.join(PROVIDERS).replace('_','.') }"
    lines += [header, ""]

    for c in coins:
        sym, pair, th = c["symbol"], c["pair"], (c.get("thresholds") or {})
        try:
            if not cooldown_ok(state, sym, now):
                lines.append(f"🟡 {sym}: — (Cooldown aktiv) — HOLD")
                continue

            line, meta = analyze_coin(sym, pair, th)
            lines.append(line)

            if meta["level"] in ("signal", "alert"):
                alerts.append(line)
                mark_sent(state, sym, now, meta["level"])

            append_csv_row(now, sym, meta["level"], meta["ch5"], meta["ch15"], meta["price"])

        except Exception as e:
            print(f"[{sym}] ERROR: {e}", file=sys.stderr)
            lines.append(f"🟡 {sym}: Datenfehler — HOLD")

    if not alerts:
        lines += ["", "🟡 Keine starken Bewegungen über den Schwellen."]

    write_text(MSG_PATH, "\n".join(lines))
    write_text(ALERTS_PATH, "🚨 Alerts\n" + "\n".join(alerts) if alerts else "")
    save_state(state)

def main():
    try:
        build_messages()
        print("message.txt + alerts.txt geschrieben ✔")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
