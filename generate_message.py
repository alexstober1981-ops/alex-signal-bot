#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_message.py
Robuste Signalgenerierung mit Daten-Fallback:
binance.us -> bybit (Spot) -> OKX
Unterstützt force_exchange je Coin via coins.json.
Outputs: message.txt, alerts.txt, signal_state.json
"""

import os, json, time, math
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
import requests

# -----------------------------
# Konfiguration & Defaults
# -----------------------------

BASE_QUOTE = "USDT"     # alle Preise in USDT
INTERVALS = ["5m", "15m"]
CANDLE_LIMIT = 300
UA_HEADERS = {"User-Agent": "Mozilla/5.0 (SignalBot/1.0)"}

# Default-Schwellen (werden von coins.json pro Coin übersteuert)
DEFAULT_RULES = {
    "min_change_5m": 0.25,    # % absolute Veränderung
    "min_change_15m": 0.45,   # % absolute Veränderung
    "min_rsi": 50,            # Mindest-RSI für Triggers
    "cooldown_min": 30        # (nur für Alerts-Entdoppelung über state)
}

# Dateien/Paths
COINS_PATH = "coins.json"
MSG_PATH   = "message.txt"
ALERTS_PATH= "alerts.txt"
STATE_PATH = "signal_state.json"

# -----------------------------
# Utils
# -----------------------------

def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_text(path: str, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def pct(a: float, b: float) -> float:
    try:
        return (a - b) / b * 100.0
    except Exception:
        return float("nan")

def safe_float(x) -> Optional[float]:
    try:
        v = float(x)
        if math.isfinite(v):
            return v
        return None
    except Exception:
        return None

def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# -----------------------------
# Datenquellen (Fetch + Fallback)
# -----------------------------

def _fetch_binanceus(symbol: str, interval: str, limit: int) -> List[List]:
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    r = requests.get(url, headers=UA_HEADERS, timeout=20)
    # 451 -> z. B. Geo-Block
    if r.status_code == 451:
        raise RuntimeError("binance.us 451")
    r.raise_for_status()
    # Binance-US: älteste -> neueste
    raw = r.json()
    out = []
    for c in raw:
        # [open_time, open, high, low, close, volume, close_time, ...]
        out.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return out

def _fetch_bybit_spot(symbol: str, interval: str, limit: int) -> List[List]:
    # Bybit interval ist z. B. "5" oder "15"
    iv_map = {"1m":"1","3m":"3","5m":"5","15m":"15","30m":"30","1h":"60"}
    iv = iv_map.get(interval, "5")
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval={iv}&limit={limit}"
    r = requests.get(url, headers=UA_HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"bybit retCode {data.get('retCode')}")
    lst = data["result"]["list"]  # neueste -> älteste
    out = []
    for c in reversed(lst):  # zu älteste -> neueste drehen
        # c: [startTime, open, high, low, close, volume, turnOver]
        out.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return out

def _fetch_okx(symbol: str, interval: str, limit: int) -> List[List]:
    # OKX benötigt BTC-USDT, bar = 5m / 15m
    bar = interval
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
    r = requests.get(url, headers=UA_HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"okx code {data.get('code')}")
    lst = data["data"]  # neueste -> älteste
    out = []
    for c in reversed(lst):
        # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        out.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return out

def fetch_klines(coin: str, interval: str, force: Optional[str]=None) -> Tuple[str, Optional[List[List]]]:
    """
    Liefert (quelle, candles) – candles ist Liste [ts, o, h, l, c] (älteste->neueste).
    Bei Fehlern wird weitergefallen.
    """
    # Symbols
    sym_binance = f"{coin}{BASE_QUOTE}"
    sym_bybit   = f"{coin}{BASE_QUOTE}"
    sym_okx     = f"{coin}-{BASE_QUOTE}"

    last_err = None
    order = []
    if force:
        order = [force]
    else:
        order = ["binance", "bybit", "okx"]

    for ex in order:
        try:
            if ex == "binance":
                data = _fetch_binanceus(sym_binance, interval, CANDLE_LIMIT)
            elif ex == "bybit":
                data = _fetch_bybit_spot(sym_bybit, interval, CANDLE_LIMIT)
            elif ex == "okx":
                data = _fetch_okx(sym_okx, interval, CANDLE_LIMIT)
            else:
                continue
            # Minimalprüfung
            if not data or len(data) < 50:
                raise RuntimeError(f"{ex} zu wenig Daten")
            return ex, data
        except Exception as e:
            last_err = e
            continue
    # nichts geklappt
    return f"error:{last_err}", None

# -----------------------------
# Indikatoren
# -----------------------------

def rsi_wilder(closes: List[float], period: int=14) -> Optional[float]:
    if closes is None or len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # erster Durchschnitt
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain*(period-1) + gains[i]) / period
        avg_loss = (avg_loss*(period-1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    val = 100.0 - (100.0 / (1.0 + rs))
    return float(val)

def atr_percent(ohlc: List[List[float]], period: int=14) -> Optional[float]:
    # ohlc: [ts,o,h,l,c] älteste->neueste
    if ohlc is None or len(ohlc) < period + 1:
        return None
    trs = []
    prev_close = ohlc[0][4]
    for i in range(1, len(ohlc)):
        h, l, c = ohlc[i][2], ohlc[i][3], ohlc[i][4]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    # Wilder ATR
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr*(period-1) + trs[i]) / period
    last_close = ohlc[-1][4]
    if last_close == 0:
        return None
    return atr / last_close * 100.0

def last_change_percent(closes: List[float]) -> Optional[float]:
    if closes is None or len(closes) < 2:
        return None
    return pct(closes[-1], closes[-2])

# -----------------------------
# Regeln/Signale
# -----------------------------

def load_rules_for(symbol: str, coin_cfg: dict) -> dict:
    rules = DEFAULT_RULES.copy()
    # coin-spezifische Overrides
    for k in ("min_change_5m","min_change_15m","min_rsi","cooldown_min"):
        if k in coin_cfg:
            rules[k] = coin_cfg[k]
    return rules

def classify_signal(change5: Optional[float], change15: Optional[float], rsi: Optional[float], rules: dict) -> Tuple[str, str]:
    """
    Rückgabe: (status, icon)
    status z.B. "HOLD" | "Signal +" | "Signal -"
    """
    if any(x is None or math.isnan(x) for x in [change5, change15, rsi]):
        return ("Datenfehler — HOLD", "🟡")
    trig_pos = (abs(change5 or 0) >= rules["min_change_5m"] or abs(change15 or 0) >= rules["min_change_15m"]) and (rsi or 0) >= rules["min_rsi"]
    trig_neg = (abs(change5 or 0) >= rules["min_change_5m"] or abs(change15 or 0) >= rules["min_change_15m"]) and (rsi or 0) <= 100 - rules["min_rsi"]
    if trig_pos and ((change5 or 0) > 0 or (change15 or 0) > 0):
        return ("Signal ▲", "📈")
    if trig_neg and ((change5 or 0) < 0 or (change15 or 0) < 0):
        return ("Signal ▼", "📉")
    return ("HOLD", "🟡")

# -----------------------------
# Hauptlogik
# -----------------------------

def analyze_coin(sym: str, coin_cfg: dict) -> Dict:
    force_ex = coin_cfg.get("force_exchange")  # "bybit" | "okx" | "binance"
    # Daten holen
    src5, k5 = fetch_klines(sym, "5m", force=force_ex)
    src15, k15 = fetch_klines(sym, "15m", force=force_ex)

    if k5 is None or k15 is None:
        return {
            "symbol": sym, "price": None,
            "change5": None, "change15": None,
            "atrp": None, "rsi": None,
            "status": "Datenfehler — HOLD", "icon": "🟡",
            "source": f"{src5} / {src15}"
        }

    closes5  = [c[4] for c in k5]
    closes15 = [c[4] for c in k15]

    price = safe_float(closes5[-1])
    change5 = last_change_percent(closes5)
    change15 = last_change_percent(closes15)
    atrp = atr_percent(k5, 14)
    rsi  = rsi_wilder(closes5, 14)

    # Sanitizing
    if price is None or math.isnan(price):
        return {
            "symbol": sym, "price": None,
            "change5": None, "change15": None,
            "atrp": None, "rsi": None,
            "status": "Datenfehler — HOLD", "icon": "🟡",
            "source": f"{src5} / {src15}"
        }

    rules = load_rules_for(sym, coin_cfg)
    status, icon = classify_signal(change5, change15, rsi, rules)

    return {
        "symbol": sym,
        "price": price,
        "change5": change5,
        "change15": change15,
        "atrp": atrp,
        "rsi": rsi,
        "status": status,
        "icon": icon,
        "source": f"{src5} → {src15}"
    }

def fmt_num(x: Optional[float], money=False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    if money:
        return f"${x:,.2f}"
    return f"{x:.2f}"

def build_messages() -> Tuple[str, str, dict]:
    coins_cfg = read_json(COINS_PATH, [])
    if not coins_cfg:
        # Fallback-Liste falls coins.json fehlt
        coins_cfg = [{"symbol":"BTC"},{"symbol":"ETH"},{"symbol":"SOL"},
                     {"symbol":"HBAR"},{"symbol":"XRP"},{"symbol":"SEI","force_exchange":"bybit"},
                     {"symbol":"KAS","force_exchange":"bybit"},{"symbol":"RNDR"},
                     {"symbol":"FET"},{"symbol":"SUI"},{"symbol":"AVAX"},
                     {"symbol":"ADA"},{"symbol":"DOT"}]

    results = []
    for c in coins_cfg:
        sym = c["symbol"].upper()
        results.append(analyze_coin(sym, c))

    # Header
    header = []
    header.append(f"📊 Signal Snapshot — {utc_now_str()}")
    header.append(f"Basis: USD • Intervalle: 5m/15m •")
    header.append(f"Quellen: BinanceUS → Bybit → OKX")
    lines = ["\n".join(header)]

    alerts = []
    state = read_json(STATE_PATH, {"cooldowns":{}})

    for r in results:
        sym = r["symbol"]
        price = fmt_num(r["price"], money=True)
        ch5 = fmt_num(r["change5"])
        ch15= fmt_num(r["change15"])
        atrp= fmt_num(r["atrp"])
        rsi = fmt_num(r["rsi"])
        status = r["status"]
        icon = r["icon"]

        if "Datenfehler" in status:
            lines.append(f"🟡 {sym}: Datenfehler — HOLD")
            continue

        lines.append(
            f"{icon} {sym}: {price} • 5m {ch5}% • 15m {ch15}% • ATR% {atrp} • RSI {rsi} — {status}"
        )

        # Alerts nur bei echtem Signal
        if status.startswith("Signal"):
            now = int(time.time())
            cool = DEFAULT_RULES["cooldown_min"]
            last_ts = state["cooldowns"].get(sym, 0)
            if now - last_ts >= cool*60:
                arrow = "▲" if "▲" in status else "▼"
                alerts.append(f"{arrow} {sym}: 5m {ch5}% • 15m {ch15}% • RSI {rsi} • ATR% {atrp}")
                state["cooldowns"][sym] = now

    # Falls keine Alerts
    if not alerts:
        alerts_text = "—"
    else:
        alerts_text = "\n".join(alerts)

    return "\n".join(lines), alerts_text, state

def main():
    msg, alerts, state = build_messages()
    write_text(MSG_PATH, msg+"\n")
    write_text(ALERTS_PATH, alerts+"\n")
    save_state(state)
    print("message.txt + alerts.txt erzeugt.")

if __name__ == "__main__":
    main()
