#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_message.py
Robuste Signal-Generierung mit Daten-Fallback:
binance.us  →  Bybit (Spot)  →  OKX

Outputs:
- message.txt     : Markt-Snapshot (alle Coins)
- alerts.txt      : nur starke Signale
- signal_state.json : interner Zustand (Cooldown usw.)

Konfiguration:
- coins.json  : Liste der Coins; optionale per-Coin Schwellen (min_rsi, min_change)
- ENV:
  * COOLDOWN_MINUTES (Standard 30)
  * BASE_QUOTE (Standard "USDT") – nur kosmetisch im Text
"""

from __future__ import annotations
import json, os, time, math
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

import requests

# ========= Konfig & Defaults =========

DEFAULTS = {
    "min_rsi":    50,     # Mindestrsi für ein Signal
    "min_change": 0.003,  # 0.3% (als Dezimalzahl)
}

COOLDOWN_MIN = int(os.getenv("COOLDOWN_MINUTES", "30"))
BASE_QUOTE = os.getenv("BASE_QUOTE", "USDT")

MSG_PATH     = "message.txt"
ALERTS_PATH  = "alerts.txt"
STATE_PATH   = "signal_state.json"
COINS_PATH   = "coins.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (SignalBot; +https://github.com)",
    "Accept": "application/json",
}

# ========= Utils =========

def load_state() -> Dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"coins": {}, "last_run": 0}

def save_state(state: Dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_coins() -> List[Dict]:
    # Fallback-Liste, falls keine coins.json vorhanden ist
    default_coins = [
        {"symbol": "BTC"},
        {"symbol": "ETH"},
        {"symbol": "SOL"},  # in coins.json ggf. schärfer
        {"symbol": "HBAR"},
        {"symbol": "XRP"},
        {"symbol": "SEI"},
        {"symbol": "KAS"},  # in coins.json ggf. schärfer
        {"symbol": "RNDR"},
        {"symbol": "FET"},
        {"symbol": "SUI"},
        {"symbol": "AVAX"},
        {"symbol": "ADA"},
        {"symbol": "DOT"},
    ]
    if not os.path.exists(COINS_PATH):
        return default_coins
    try:
        with open(COINS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # sanity: nur Dicts mit "symbol"
            out = []
            for c in data:
                if isinstance(c, dict) and "symbol" in c:
                    out.append(c)
            return out or default_coins
    except Exception:
        return default_coins

def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.2f}"
    return f"${p:.4f}"

def pct(a: float) -> str:
    s = f"{a*100:+.2f}%"
    return s

def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# ========= Daten-Fetch (Fallback: BinanceUS -> Bybit -> OKX) =========

def _okx_symbol(pair: str) -> str:
    # BTCUSDT -> BTC-USDT
    if pair.endswith("USDT"):
        return pair[:-4] + "-USDT"
    return pair.replace("USDT", "-USDT")

def fetch_klines(pair: str, interval: str) -> Optional[List[Tuple[int,float,float,float,float]]]:
    """
    Liefert Liste von Kerzen: [(t_ms, open, high, low, close), ...]
    oder None bei Fehler.
    interval: '5m' oder '15m'
    """

    # --- 1) Binance.US ---
    try:
        url = f"https://api.binance.us/api/v3/klines?symbol={pair}&interval={interval}&limit=300"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 451:
            raise RuntimeError("451 from BinanceUS")
        r.raise_for_status()
        data = r.json()
        res = []
        for c in data:
            res.append((int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])))
        if res:
            return res
    except Exception:
        pass

    # --- 2) Bybit (Spot) ---
    try:
        iv = "5" if interval == "5m" else "15"
        url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={pair}&interval={iv}&limit=300"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        j = r.json()
        rows = j.get("result", {}).get("list", [])
        # Bybit liefert jüngste zuletzt oder zuerst je nach API; wir sortieren sicherheitshalber
        rows = sorted(rows, key=lambda x: int(x[0]))
        res = []
        for c in rows:
            # [start, open, high, low, close, volume, turnover]
            res.append((int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])))
        if res:
            return res
    except Exception:
        pass

    # --- 3) OKX ---
    try:
        inst = _okx_symbol(pair)
        bar = "5m" if interval == "5m" else "15m"
        url = f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar={bar}&limit=300"
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        j = r.json()
        rows = j.get("data", [])
        # OKX liefert neueste zuerst -> umdrehen
        rows = list(reversed(rows))
        res = []
        for c in rows:
            # [ts, o, h, l, c, ...]
            res.append((int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])))
        if res:
            return res
    except Exception:
        pass

    return None

# ========= Indikatoren =========

def rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period+1):
        diff = closes[i] - closes[i-1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period

    for i in range(period+1, len(closes)):
        diff = closes[i] - closes[i-1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain*(period-1) + gain) / period
        avg_loss = (avg_loss*(period-1) + loss) / period

    if avg_loss == 0:
        return 70.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def atr_percent(ohlc: List[Tuple[int,float,float,float,float]], period: int = 14) -> float:
    # True Range basiert auf High, Low, Close(prev)
    if len(ohlc) <= period + 1:
        return 0.0
    trs = []
    prev_close = ohlc[0][4]
    for i in range(1, len(ohlc)):
        _, o, h, l, c = ohlc[i]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    # Wilder-Smoothing
    trn = trs[:period]
    if len(trn) < period:
        return 0.0
    atr = sum(trn) / period
    for x in trs[period:]:
        atr = (atr*(period-1) + x) / period
    last_close = ohlc[-1][4]
    if last_close == 0:
        return 0.0
    return (atr / last_close) * 100.0

# ========= Analyse einer Coin =========

def analyze_coin(sym: str, per_coin: Dict, state: Dict) -> Tuple[str, Optional[str]]:
    pair = f"{sym}USDT"
    linestr = ""
    alert_line: Optional[str] = None

    # 5m/15m Daten
    data_5m = fetch_klines(pair, "5m")
    data_15m = fetch_klines(pair, "15m")

    if not data_5m or not data_15m:
        linestr = f"🟡 {sym}: Datenfehler — HOLD"
        return linestr, None

    # Preise/Indikatoren
    close_5 = [c[4] for c in data_5m]
    close_15 = [c[4] for c in data_15m]
    price = close_5[-1]
    ch5  = (close_5[-1] / close_5[-2]) - 1.0 if len(close_5) >= 2 else 0.0
    # 15m ≈ 3×5m -> Diff von -4 nach -1
    ch15 = (close_5[-1] / close_5[-4]) - 1.0 if len(close_5) >= 4 else 0.0

    r = rsi(close_5, 14)
    atrp = atr_percent(data_5m, 14)

    # Schwellen
    min_rsi    = per_coin.get("min_rsi", DEFAULTS["min_rsi"])
    min_change = per_coin.get("min_change", DEFAULTS["min_change"])

    # Textzeile
    linestr = (
        f"🟡 {sym}: {fmt_price(price)}"
        f" • 5m {pct(ch5)}"
        f" • 15m {pct(ch15)}"
        f" • ATR% {atrp:.2f}"
        f" • RSI {int(round(r))} — "
    )

    # Signal-Logik (simpel & robust)
    direction = 0
    if r >= min_rsi and ch5 >= min_change:
        direction = +1
    elif r <= (100 - min_rsi) and ch5 <= -min_change:
        direction = -1

    if direction == 0:
        linestr += "HOLD"
        return linestr, None

    # Cooldown
    now_ts = int(time.time())
    last_alert_ts = state.get("coins", {}).get(sym, {}).get("last_alert", 0)
    cooled = (now_ts - last_alert_ts) >= COOLDOWN_MIN * 60

    emoji = "📈" if direction > 0 else "📉"
    dirword = "BUY" if direction > 0 else "SELL"

    if cooled:
        linestr += f"{emoji} {dirword}"
        alert_line = f"{emoji} {sym} — {dirword} • Preis {fmt_price(price)} • 5m {pct(ch5)} • RSI {int(round(r))}"
        # state updaten
        state.setdefault("coins", {}).setdefault(sym, {})["last_alert"] = now_ts
    else:
        mins = max(0, COOLDOWN_MIN - (now_ts - last_alert_ts)//60)
        linestr += f"HOLD (Cooldown {mins}m)"

    return linestr, alert_line

# ========= Hauptlogik =========

def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")

def build_messages():
    state = load_state()
    coins = load_coins()

    header = (
        f"📊 Signal Snapshot — {now_utc_str()}\n"
        f"Basis: USD • Intervalle: 5m/15m •\n"
        f"Quellen: BinanceUS → Bybit → OKX\n"
    )

    lines: List[str] = []
    alert_lines: List[str] = []

    for c in coins:
        sym = c["symbol"].upper().strip()
        l, a = analyze_coin(sym, c, state)
        lines.append(l)
        if a:
            alert_lines.append(a)

    # Dateien schreiben
    msg_text = header + "\n" + "\n".join(lines)
    write_text(MSG_PATH, msg_text)

    alerts_text = "\n".join(alert_lines) if alert_lines else ""
    write_text(ALERTS_PATH, alerts_text)

    state["last_run"] = int(time.time())
    save_state(state)

def main():
    try:
        build_messages()
        print("message.txt + alerts.txt erzeugt.")
    except Exception as e:
        print(f"ERROR: {e}")
        raise

if __name__ == "__main__":
    main()
