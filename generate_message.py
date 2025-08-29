#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_message.py
Pro-Signalgenerierung mit Daten-Fallback
(binance.us -> bybit (spot) -> OKX), adaptiven Schwellen (ATR%),
Trend-Filtern (EMA 20/50), RSI, Cooldown & sauberem Output.

Erwartete Dateien:
- coins.json  (Liste: {symbol, binance, optional thresholds ...})
Schreibt:
- message.txt
- alerts.txt
- signal_state.json
"""

import os, json, time, math, datetime as dt
from typing import List, Dict, Tuple, Optional
import requests

# ---------- Konfiguration ----------
USER_AGENT = "Mozilla/5.0 (compatible; AlexSignalBot/1.0)"
HEADERS = {"User-Agent": USER_AGENT}
TZ = dt.timezone.utc

# Cooldown (Minuten) via Env überschreibbar
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "30"))

# Kline-Limits
LIM_5M = 120           # ~10h
LIM_15M = 200          # ~2d
EMA_LEN_FAST = 20
EMA_LEN_SLOW = 50
RSI_LEN = 14
ATR_LEN = 20

MSG_PATH = "message.txt"
ALERTS_PATH = "alerts.txt"
STATE_PATH = "signal_state.json"

# ---------- Utils ----------
def read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def write_text(path: str, s: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)

def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def now_utc_ts() -> int:
    return int(dt.datetime.now(tz=TZ).timestamp())

def ema(values: List[float], length: int) -> List[float]:
    k = 2 / (length + 1)
    out = []
    v_prev = None
    for v in values:
        if v_prev is None:
            v_prev = v
        else:
            v_prev = (v * k) + (v_prev * (1 - k))
        out.append(v_prev)
    return out

def rsi(closes: List[float], length: int) -> List[float]:
    if len(closes) < length + 1:
        return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i-1]
        gains.append(max(ch, 0.0))
        losses.append(abs(min(ch, 0.0)))
    # Anfangsdurchschnitt
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length
    rsis = [50.0] * (length)  # padding
    for i in range(length, len(gains)):
        avg_gain = (avg_gain*(length-1) + gains[i]) / length
        avg_loss = (avg_loss*(length-1) + losses[i]) / length
        if avg_loss == 0:
            rs = float('inf')
        else:
            rs = avg_gain / avg_loss
        r = 100 - (100 / (1 + rs))
        rsis.append(r)
    return [50.0] + rsis  # Länge = len(closes)

def true_range(h: float, l: float, prev_close: float) -> float:
    return max(h - l, abs(h - prev_close), abs(l - prev_close))

def atr(highs: List[float], lows: List[float], closes: List[float], length: int) -> List[float]:
    if len(closes) < length + 1:
        return [0.0] * len(closes)
    trs = []
    for i in range(1, len(closes)):
        trs.append(true_range(highs[i], lows[i], closes[i-1]))
    # Wilder ATR
    atrs = [0.0] * 1  # pad
    a = sum(trs[:length]) / length
    atrs.extend([a])
    for i in range(length, len(trs)):
        a = (a*(length-1) + trs[i]) / length
        atrs.append(a)
    # Länge angleichen
    while len(atrs) < len(closes):
        atrs.append(atrs[-1] if atrs else 0.0)
    return atrs[:len(closes)]

def pct(a: float, b: float) -> float:
    if b == 0: return 0.0
    return (a - b) / b * 100.0

# ---------- Fetch Klines (Fallback) ----------
def _binance_us(pair: str, interval: str, limit: int):
    url = f"https://api.binance.us/api/v3/klines?symbol={pair}&interval={interval}&limit={limit}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code == 451:
        # rechtliche Blockade – Fallback
        raise RuntimeError("binance.us 451")
    r.raise_for_status()
    data = r.json()
    # [open_time, open, high, low, close, volume, close_time, ...]
    out = []
    for c in data:
        out.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return out

def _bybit_spot(pair: str, interval: str, limit: int):
    # Bybit: interval z.B. "5", "15"
    inter = interval.replace("m", "")
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={pair}&interval={inter}&limit={limit}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    js = r.json()
    if js.get("retCode") != 0:
        raise RuntimeError(f"bybit retCode {js.get('retCode')}")
    out = []
    for c in js["result"]["list"][::-1]:
        # [start, open, high, low, close, ...] Strings
        out.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return out

def _okx(pair: str, interval: str, limit: int):
    # OKX symbol: "BTC-USDT", bar: "5m"
    okx_symbol = pair.replace("USDT", "-USDT")
    url = f"https://www.okx.com/api/v5/market/candles?instId={okx_symbol}&bar={interval}&limit={limit}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    js = r.json()
    if js.get("code") != "0":
        raise RuntimeError(f"okx code {js.get('code')}")
    out = []
    for c in js["data"][::-1]:
        # [ts, o, h, l, c, ...] Strings
        out.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return out

def fetch_klines(pair: str, interval: str, limit: int) -> List[List[float]]:
    # 1) Binance US
    try:
        return _binance_us(pair, interval, limit)
    except Exception as e1:
        # 2) Bybit
        try:
            return _bybit_spot(pair, interval, limit)
        except Exception as e2:
            # 3) OKX
            return _okx(pair, interval, limit)

# ---------- Analyse ----------
def analyze_coin(sym: str, pair: str, th_cfg: Dict, state: Dict, filters: Dict) -> Tuple[str, Optional[str], Dict]:
    """
    returns: summary_line, alert_line(optional), metrics
    """
    # 5m & 15m klines
    k5 = fetch_klines(pair, "5m", LIM_5M)
    k15 = fetch_klines(pair, "15m", LIM_15M)
    if len(k5) < 30 or len(k15) < 30:
        return f"🟡 {sym}: Daten dünn — HOLD", None, {}

    c5 = [c[4] for c in k5]
    c15 = [c[4] for c in k15]
    h15 = [c[2] for c in k15]
    l15 = [c[3] for c in k15]

    last = c15[-1]
    # Änderungen
    chg_5m = pct(c5[-1], c5[-2])
    chg_15m = pct(c15[-1], c15[-2])

    # EMA, RSI, ATR%
    ema20 = ema(c15, EMA_LEN_FAST)[-1]
    ema50 = ema(c15, EMA_LEN_SLOW)[-1]
    rsi14 = rsi(c15, RSI_LEN)[-1]
    atr_vals = atr(h15, l15, c15, ATR_LEN)
    atrp = (atr_vals[-1] / last * 100.0) if last > 0 else 0.0

    trend_up = ema20 > ema50
    trend_down = ema20 < ema50

    # Basis-Schwellen (Default)
    base_move = {"5m": 0.5, "15m": 1.0}
    if "thresholds" in th_cfg and "move" in th_cfg["thresholds"]:
        mv = th_cfg["thresholds"]["move"]
        base_move["5m"] = float(mv.get("5m", base_move["5m"]))
        base_move["15m"] = float(mv.get("15m", base_move["15m"]))

    # Adaptive Schwelle: +0.5x ATR% auf beide
    mov5_req = base_move["5m"] + 0.5 * atrp
    mov15_req = base_move["15m"] + 0.5 * atrp

    # Filter: z.B. Trendfilter, RSI-Extreme dämpfen
    filter_ok = True
    if filters.get("trend") == "up" and not trend_up:
        filter_ok = False
    if filters.get("trend") == "down" and not trend_down:
        filter_ok = False

    # Statuslogik
    status = "HOLD"
    reason = []
    if abs(chg_5m) >= mov5_req or abs(chg_15m) >= mov15_req:
        status = "INFO"
        reason.append("Move")
    if status == "INFO" and filter_ok:
        # „Signal“ wenn 15m-Move + Trend in dieselbe Richtung
        if (chg_15m > 0 and trend_up) or (chg_15m < 0 and trend_down):
            status = "SIGNAL"
            reason.append("Trend")
    # „ALERT“ wenn starke Bewegung und RSI in Extremzone
    if status in ("INFO","SIGNAL") and (abs(chg_15m) >= mov15_req*1.5) and (rsi14 >= 70 or rsi14 <= 30):
        status = "ALERT"
        reason.append("RSI")

    # Cooldown
    st = state.setdefault("coins", {}).setdefault(sym, {})
    last_ts = st.get("last_ts", 0)
    now_ts = now_utc_ts()
    cooldown_ok = (now_ts - last_ts) >= COOLDOWN_MINUTES*60
    if status in ("SIGNAL","ALERT") and not cooldown_ok:
        status = "INFO"  # de-eskalieren, aber nicht ganz verwerfen
        reason.append("cooldown")

    # Update State
    st["last_price"] = last
    st["last_ts"] = now_ts if status in ("SIGNAL","ALERT") else last_ts
    st["last_status"] = status

    # Texte
    tdir = "▲" if chg_15m >= 0 else "▼"
    line = f"🟡 {sym}: ${last:,.2f} • 5m {chg_5m:+.2f}% • 15m {chg_15m:+.2f}% • ATR% {atrp:.2f} • RSI {rsi14:.0f} — {status}"
    alert_line = None
    if status == "ALERT":
        alert_line = f"🚨 {sym} {tdir} {chg_15m:+.2f}% (15m) • Trend:{'UP' if trend_up else 'DOWN'} • RSI {rsi14:.0f} • ATR% {atrp:.2f}"

    metrics = {
        "price": last, "chg_5m": chg_5m, "chg_15m": chg_15m,
        "ema20": ema20, "ema50": ema50, "rsi14": rsi14, "atrp": atrp,
        "status": status, "reasons": reason
    }
    return line, alert_line, metrics

# ---------- Build ----------
def build_messages():
    coins = read_json("coins.json", [])
    state = read_json(STATE_PATH, {})
    filters = {"trend": "any"}  # "up" | "down" | "any" – bei Bedarf steuern

    header = dt.datetime.now(tz=TZ).strftime("📊 Signal Snapshot — %Y-%m-%d %H:%M UTC\n\nBasis: USD • Intervalle: 5m/15m • Quellen: BinanceUS → Bybit → OKX\n")
    lines: List[str] = []
    alerts: List[str] = []
    all_alerts_raw: List[str] = []

    for c in coins:
        sym = c["symbol"]
        pair = c.get("binance", f"{sym}USDT")
        th = c.get("thresholds", {})
        try:
            line, alert_line, metrics = analyze_coin(sym, pair, {"thresholds": th}, state, filters)
            lines.append(line)
            if alert_line:
                alerts.append(alert_line)
                all_alerts_raw.append(json.dumps({"symbol": sym, **metrics}, ensure_ascii=False))
        except Exception as e:
            lines.append(f"🟡 {sym}: Datenfehler — HOLD")

    if not alerts:
        lines.append("\n🟡 Keine nennenswerte Bewegung über den Info-Schwellen.")
    legend = "\nLegende: 🟡 Hold • ℹ️ Info • 📈/📉 Signal • 🚀/🔻 Alert"
    msg = header + "\n".join(lines) + "\n" + legend

    write_text(MSG_PATH, msg)
    write_text(ALERTS_PATH, "\n".join(alerts))
    save_state(state)

def main():
    try:
        build_messages()
        print("message.txt + alerts.txt + signal_state.json geschrieben.")
    except Exception as e:
        print(f"ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
