#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_message.py
Robuste Signal-Generierung mit Daten-Fallback:
BinanceUS  →  Bybit (Spot)  →  OKX

- Liest Coins aus coins.json (Liste von {"symbol": "BTC", ...}).
- Berechnet 5m/15m-Change, RSI(14), ATR%(14).
- Schreibt message.txt (Snapshot) und alerts.txt (nur starke Signale).
- Merkt sich zuletzt gesendete Alerts in signal_state.json (Cooldown).
"""

from __future__ import annotations
import json, os, time, math, statistics
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import requests

# ----------------------------
# Einstellungen / Defaults
# ----------------------------
BASE = "USD"
INTERVALS = ("5m", "15m")
COOLDOWN_MIN = 30                     # Cooldown je Coin (Minuten)
STATE_PATH = "signal_state.json"
MSG_PATH = "message.txt"
ALERTS_PATH = "alerts.txt"
LOG_PATH = "signals_log.csv"          # optional, wird nur benutzt falls vorhanden

# Standard-Schwellen (können pro Coin via coins.json überschrieben werden)
DEFAULTS = {
    "min_rsi": 50,                    # ab welchem RSI überhaupt interessant
    "min_change_5m": 0.25,            # % absolute Veränderung 5m
    "min_change_15m": 0.40,           # % absolute Veränderung 15m
    "min_atr_pct": 0.20,              # % ATR (Volatilität) mind.
}

# User Agent
_HEADERS = {"User-Agent": "Mozilla/5.0 (signals-bot)"}

# ----------------------------
# Exchange-spezifische Symbol-Mappings
# base_pair ist unser internes Schema: z. B. "SEIUSDT", "KASUSDT"
# ----------------------------
EX_SYMBOLS = {
    "binanceus": {
        "SEIUSDT": "SEIUSDT",
        "KASUSDT": None,            # KAS ist nicht auf BinanceUS -> None = überspringen
    },
    "bybit": {
        "SEIUSDT": "SEIUSDT",
        "KASUSDT": "KASUSDT",
    },
    "okx": {
        "SEIUSDT": "SEI-USDT",      # OKX nutzt Bindestriche
        "KASUSDT": "KAS-USDT",
    },
}

# Bybit Intervall-Map
_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15"}
# OKX Intervall-Map
_OKX_INTERVAL = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m"}

# ----------------------------
# Utilities
# ----------------------------
def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def write_text(path: str, txt: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

def append_csv_row(ts: str, sym: str, price: float, ch5: float, ch15: float, atrp: float, rsi: float):
    if not LOG_PATH:
        return
    try:
        header_needed = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("ts,symbol,price,chg5,chg15,atrpct,rsi\n")
            f.write(f"{ts},{sym},{price:.8f},{ch5:.4f},{ch15:.4f},{atrp:.4f},{rsi:.2f}\n")
    except Exception:
        pass

def fmt_price(v: float) -> str:
    # rudimentär: je nach Größe
    if v >= 1000: return f"${v:,.2f}"
    if v >= 1:    return f"${v:,.2f}"
    return f"${v:.4f}"

def now_utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# ----------------------------
# Indikatoren
# ----------------------------
def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return float("nan")
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = values[-i] - values[-i-1]
        if diff >= 0:
            gains.append(diff)
        else:
            losses.append(-diff)
    avg_gain = (sum(gains) / period) if gains else 0.0
    avg_loss = (sum(losses) / period) if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def atr_pct(ohlc: List[Tuple[float,float,float,float]], period: int = 14) -> float:
    # ohlc: [(o,h,l,c), ...] -> ATR/close in %
    if len(ohlc) < period + 1:
        return float("nan")
    trs = []
    prev_close = ohlc[-period-1][3]
    for i in range(-period, 0):
        o, h, l, c = ohlc[i]
        tr = max(h-l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    atr = sum(trs) / period
    last_close = ohlc[-1][3]
    if last_close == 0:
        return float("nan")
    return (atr / last_close) * 100.0

def pct_change(a: float, b: float) -> float:
    if b == 0: return 0.0
    return (a - b) / b * 100.0

# ----------------------------
# Daten-Fetch inkl. Fallback
# ----------------------------
def map_symbol(exchange: str, base_pair: str) -> Optional[str]:
    # Versuch über EX_SYMBOLS
    ex = exchange.lower()
    if ex in EX_SYMBOLS and base_pair in EX_SYMBOLS[ex]:
        return EX_SYMBOLS[ex][base_pair]
    # generischer Fallback
    if ex == "okx":
        return base_pair.replace("USDT", "-USDT")
    return base_pair

def fetch_binanceus(pair: str, interval: str, limit: int = 300):
    url = f"https://api.binance.us/api/v3/klines?symbol={pair}&interval={interval}&limit={limit}"
    r = requests.get(url, headers=_HEADERS, timeout=15)
    if r.status_code == 451:
        raise RuntimeError("451 (BinanceUS Blockiert)")
    r.raise_for_status()
    raw = r.json()
    # [open_time, open, high, low, close, ...]
    out = []
    for c in raw:
        out.append((float(c[1]), float(c[2]), float(c[3]), float(c[4])))
    return out

def fetch_bybit(pair: str, interval: str, limit: int = 200):
    iv = _BYBIT_INTERVAL.get(interval, "5")
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={pair}&interval={iv}&limit={limit}"
    r = requests.get(url, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode {data.get('retCode')}")
    # result->list: [ [start,open,high,low,close,volume,turnover], ... ] neueste zuerst
    ls = data["result"]["list"]
    ls.sort(key=lambda x: int(x[0]))
    out = []
    for c in ls:
        out.append((float(c[1]), float(c[2]), float(c[3]), float(c[4])))
    return out

def fetch_okx(pair: str, interval: str, limit: int = 200):
    iv = _OKX_INTERVAL.get(interval, "5m")
    url = f"https://www.okx.com/api/v5/market/candles?instId={pair}&bar={iv}&limit={limit}"
    r = requests.get(url, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX code {data.get('code')}")
    # data -> list: [ [ts,open,high,low,close,vol,volCcy,volCcyQuote,confirm] ] neueste zuerst
    ls = data["data"]
    ls.sort(key=lambda x: int(x[0]))
    out = []
    for c in ls:
        out.append((float(c[1]), float(c[2]), float(c[3]), float(c[4])))
    return out

def fetch_klines(base_pair: str, interval: str) -> List[Tuple[float,float,float,float]]:
    last_err = None
    # 1) BinanceUS
    try:
        sym = map_symbol("binanceus", base_pair)
        if sym:
            return fetch_binanceus(sym, interval)
    except Exception as e:
        last_err = e
    # 2) Bybit
    try:
        sym = map_symbol("bybit", base_pair)
        if sym:
            return fetch_bybit(sym, interval)
    except Exception as e:
        last_err = e
    # 3) OKX
    try:
        sym = map_symbol("okx", base_pair)
        if sym:
            return fetch_okx(sym, interval)
    except Exception as e:
        last_err = e
    raise RuntimeError(f"Klines-Fetch fehlgeschlagen: {last_err}")

# ----------------------------
# Analyse
# ----------------------------
def analyze_symbol(symbol: str, th: Dict[str, Any]) -> Dict[str, Any]:
    pair = symbol + "USDT"
    try:
        d5  = fetch_klines(pair, "5m")
        d15 = fetch_klines(pair, "15m")
    except Exception as e:
        return {"symbol": symbol, "error": f"Datenfehler: {e}"}

    if len(d5) < 20 or len(d15) < 20:
        return {"symbol": symbol, "error": "Zu wenig Daten"}

    close5  = [c[3] for c in d5]
    close15 = [c[3] for c in d15]
    price = close5[-1]

    # Veränderungen
    ch5  = pct_change(close5[-1], close5[-2])
    ch15 = pct_change(close15[-1], close15[-4])  # ~15m

    # Indikatoren
    r = rsi(close5, 14)
    a = atr_pct(d5, 14)

    # Schwellen
    min_rsi   = th.get("min_rsi",   DEFAULTS["min_rsi"])
    min_c5    = th.get("min_change_5m",  DEFAULTS["min_change_5m"])
    min_c15   = th.get("min_change_15m", DEFAULTS["min_change_15m"])
    min_atrp  = th.get("min_atr_pct",    DEFAULTS["min_atr_pct"])

    strong = (
        (abs(ch5) >= min_c5 or abs(ch15) >= min_c15) and
        (not math.isnan(r) and r >= min_rsi) and
        (not math.isnan(a) and a >= min_atrp)
    )

    return {
        "symbol": symbol,
        "price": price,
        "chg5": ch5,
        "chg15": ch15,
        "rsi": r,
        "atrp": a,
        "strong": strong,
    }

# ----------------------------
# Main Build
# ----------------------------
def load_coins() -> List[Dict[str, Any]]:
    coins = load_json("coins.json", [])
    # Wenn keine Datei: Minimum-Set
    if not coins:
        coins = [{"symbol": s} for s in
                 ("BTC","ETH","SOL","HBAR","XRP","SEI","KAS","RNDR","FET","SUI","AVAX","ADA","DOT")]
    return coins

def build_messages():
    coins = load_coins()
    state = load_json(STATE_PATH, {"last_alerts": {}})
    now_ts = now_utc_ts()

    lines = []
    alert_lines = []

    header = (f"📊 Signal Snapshot — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
              f"Basis: {BASE} • Intervalle: 5m/15m •\n"
              f"Quellen: BinanceUS → Bybit → OKX")
    lines.append(header)

    for c in coins:
        sym = c["symbol"].upper()
        th = {}
        th.update(DEFAULTS)
        # coin-spezifische Schwellen (optional in coins.json)
        for k in ("min_rsi","min_change_5m","min_change_15m","min_atr_pct"):
            if k in c: th[k] = c[k]

        res = analyze_symbol(sym, th)
        if "error" in res:
            lines.append(f"🟡 {sym}: Datenfehler — HOLD")
            continue

        price = res["price"]
        ch5, ch15 = res["chg5"], res["chg15"]
        atrp, r = res["atrp"], res["rsi"]
        strong = res["strong"]

        # Cooldown-Check
        last = state.get("last_alerts", {}).get(sym, 0)
        mins_since = (time.time() - last) / 60.0

        if strong and mins_since >= COOLDOWN_MIN:
            msg = (f"🚀 {sym}: {fmt_price(price)} • 5m {ch5:+.2f}% • 15m {ch15:+.2f}% • "
                   f"ATR% {atrp:.2f} • RSI {r:.0f} — ALERT")
            alert_lines.append(msg)
            state["last_alerts"][sym] = time.time()
            lines.append("🟢 " + msg.replace("🚀 ", ""))   # im Snapshot grün
        else:
            cd_txt = ""
            if strong and mins_since < COOLDOWN_MIN:
                rest = int(COOLDOWN_MIN - mins_since)
                cd_txt = f" (Cooldown {rest}m)"
            lines.append(
                f"🟡 {sym}: {fmt_price(price)} • 5m {ch5:+.2f}% • 15m {ch15:+.2f}% • "
                f"ATR% {atrp:.2f} • RSI {r:.0f} — HOLD{cd_txt}"
            )

        # CSV optional loggen
        append_csv_row(now_ts, sym, price, ch5, ch15, atrp, r)

    # Dateien schreiben
    write_text(MSG_PATH, "\n".join(lines) + "\n")
    write_text(ALERTS_PATH, "\n".join(alert_lines) + ("\n" if alert_lines else ""))
    save_json(STATE_PATH, state)

# ----------------------------
# Entry
# ----------------------------
def main():
    try:
        build_messages()
        print("message.txt + alerts.txt erzeugt.")
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        raise

if __name__ == "__main__":
    main()
