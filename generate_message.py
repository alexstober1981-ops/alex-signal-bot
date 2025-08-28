#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pro-Ultra Signal-Generator
- TFs: 5m, 15m, 1h, 4h, 1d
- Indikatoren: RSI(14), EMA50/EMA200, MACD
- Alerts: MOVE / MACD-Cross / EMA200-Break / RSI-Extrem (mit Cooldown)
- Per-Coin Thresholds via coins.json
- Outputs:
  • message.txt  (Übersicht)
  • alerts.txt   (nur Alerts – separater Chat möglich)
  • signal_state.json (Cooldown/Meta)
  • signals_log.csv (CSV-Log)
"""

import os, sys, json, time, math, csv
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

import requests
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD

# ---------------- Config ----------------
TIMEFRAMES: List[str] = ["5m", "15m", "1h", "4h", "1d"]
PRIMARY_TF = "15m"
KLIMIT = 300

STATE_PATH = "signal_state.json"
MSG_PATH = "message.txt"
ALERTS_PATH = "alerts.txt"
CSV_LOG = "signals_log.csv"

DEFAULT_THRESHOLDS = {
  "move": {"5m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 4.0, "1d": 5.0},
  "info": {"5m": 0.3, "15m": 0.5, "1h": 1.0, "4h": 1.5, "1d": 2.0},
  "rsi_hot": 70,
  "rsi_cold": 30
}

COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "60"))
UA = {"User-Agent": "alex-pro-crypto/1.0"}

# ------------- HTTP with retry/backoff -------------
def http_get(url: str, params: dict, timeout=20, tries=4, backoff=2.0):
    delay = 1.5
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=UA)
            if r.status_code == 429:
                ra = r.json().get("retry_after", delay)
                time.sleep(float(ra))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay *= backoff

# ------------- IO helpers -------------
def load_coins() -> List[Dict[str, Any]]:
    if os.path.exists("coins.json"):
        with open("coins.json","r",encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    # Fallback, falls coins.json fehlt
    return [{"symbol":"BTC","binance":"BTCUSDT"},
            {"symbol":"ETH","binance":"ETHUSDT"},
            {"symbol":"SOL","binance":"SOLUSDT"}]

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH,"r",encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"alerts": {}}  # alerts[SYM][key]=ts

def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_PATH,"w",encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def ensure_csv_header():
    if not os.path.exists(CSV_LOG):
        with open(CSV_LOG,"w",newline="",encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts_utc","symbol","tf","close","pct_vs_prev","rsi","ema200_dist","macd_dir","label","alerts_count"])

# ------------- Data fetch -------------
def binance_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    r = http_get(url, {"symbol":symbol, "interval":interval, "limit":limit})
    cols = [
        "open_time","open","high","low","close","volume",
        "close_time","quote_asset_volume","n_trades",
        "taker_buy_base","taker_buy_quote","ignore"
    ]
    df = pd.DataFrame(r.json(), columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def binance_24h_change(symbol: str) -> float:
    url = "https://api.binance.com/api/v3/ticker/24hr"
    r = http_get(url, {"symbol": symbol}, timeout=15)
    j = r.json()
    try:
        return float(j.get("priceChangePercent", 0.0))
    except Exception:
        return 0.0

# ------------- Indicators -------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    close = df["close"]
    out = df.copy()
    out["rsi"] = RSIIndicator(close, window=14).rsi()
    out["ema50"] = EMAIndicator(close, window=50).ema_indicator()
    out["ema200"] = EMAIndicator(close, window=200).ema_indicator()
    macd_obj = MACD(close)
    out["macd"] = macd_obj.macd()
    out["macd_signal"] = macd_obj.macd_signal()
    return out

# ------------- Thresholds merge -------------
def merge_thresholds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    th = json.loads(json.dumps(DEFAULT_THRESHOLDS))
    if isinstance(cfg.get("thresholds"), dict):
        for k, v in cfg["thresholds"].items():
            if isinstance(v, dict) and k in th:
                th[k].update(v)
            else:
                th[k] = v
    return th

# ------------- Cooldown keys -------------
def make_key(tf: str, kind: str, direction: str) -> str:
    return f"{tf}:{kind}:{direction}"

def allowed(state: Dict[str, Any], sym: str, key: str) -> bool:
    last = int(state.get("alerts", {}).get(sym, {}).get(key, 0))
    return (int(time.time()) - last) >= COOLDOWN_MINUTES * 60

def remember(state: Dict[str, Any], sym: str, key: str) -> None:
    state.setdefault("alerts", {}).setdefault(sym, {})[key] = int(time.time())

# ------------- Formatting -------------
def fmt_usd(x: float) -> str:
    if x >= 1: return f"${x:,.2f}"
    return f"${x:.4f}"

def sign_pct(x: float) -> str:
    return f"{x:+.2f}%"

# ------------- Analysis -------------
def pct_last_step(df: pd.DataFrame) -> float:
    if len(df) < 2: return 0.0
    c0 = float(df.iloc[-2]["close"])
    c1 = float(df.iloc[-1]["close"])
    return (c1/c0 - 1.0) * 100.0 if c0 else 0.0

def macd_dir_str(df: pd.DataFrame) -> str:
    if len(df) < 1: return "—"
    last = df.iloc[-1]
    return "↑" if float(last["macd"]) >= float(last["macd_signal"]) else "↓"

def analyze_coin(sym: str, pair: str, th: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    # Fetch TFs
    tfs: Dict[str, pd.DataFrame] = {}
    for tf in TIMEFRAMES:
        d = add_indicators(binance_klines(pair, tf, KLIMIT))
        tfs[tf] = d

    # 24h change
    ch24 = binance_24h_change(pair)

    # Primary TF snapshot
    dfp = tfs[PRIMARY_TF]
    last = dfp.iloc[-1]
    prev = dfp.iloc[-2] if len(dfp) > 1 else last
    price = float(last["close"])
    change_p = pct_last_step(dfp)
    rsi_p = float(last["rsi"])
    ema200 = float(last["ema200"])
    ema200_prev = float(prev["ema200"])
    macd_now = float(last["macd"])
    macds_now = float(last["macd_signal"])
    macd_prev = float(prev["macd"])
    macds_prev = float(prev["macd_signal"])
    ema_dist = ((price/ema200) - 1.0) * 100.0 if ema200 else float("nan")

    # Summary block
    trend = "📈" if change_p >= 0 else "📉"
    header = f"{sym}: {fmt_usd(price)} • {PRIMARY_TF} {sign_pct(change_p)} • 24h {sign_pct(ch24)}"
    indi = f"{trend} RSI {rsi_p:.0f} • EMA200 {sign_pct(ema_dist)} • MACD {'↑' if macd_now>=macds_now else '↓'}"
    mtf = "MTF Δ: " + " • ".join([f"{tf} {sign_pct(pct_last_step(tfs[tf]))}" for tf in TIMEFRAMES])

    # Alerts durch TFs
    alerts: List[str] = []
    info_hit = False

    rsi_hot = th.get("rsi_hot", DEFAULT_THRESHOLDS["rsi_hot"])
    rsi_cold = th.get("rsi_cold", DEFAULT_THRESHOLDS["rsi_cold"])
    move_t = th["move"]; info_t = th["info"]

    for tf in TIMEFRAMES:
        d = tfs[tf]
        if len(d) < 3: 
            continue
        pct = pct_last_step(d)
        rsi = float(d.iloc[-1]["rsi"])
        ema_now = float(d.iloc[-1]["ema200"])
        ema_prev = float(d.iloc[-2]["ema200"])
        macd_now = float(d.iloc[-1]["macd"])
        macds_now = float(d.iloc[-1]["macd_signal"])
        macd_prev = float(d.iloc[-2]["macd"])
        macds_prev = float(d.iloc[-2]["macd_signal"])
        price_now = float(d.iloc[-1]["close"])
        price_prev = float(d.iloc[-2]["close"])

        # MOVE
        if abs(pct) >= move_t.get(tf, 999):
            alerts.append(f"🚨 MOVE {tf} {sign_pct(pct)}")
        else:
            if abs(pct) >= info_t.get(tf, 999):
                info_hit = True

        # MACD crosses
        if (macd_now > macds_now and macd_prev <= macds_prev):
            alerts.append(f"⚡ MACD Cross ↑ ({tf})")
        if (macd_now < macds_now and macd_prev >= macds_prev):
            alerts.append(f"⚡ MACD Cross ↓ ({tf})")

        # EMA200 breaks
        if (price_now > ema_now and price_prev <= ema_prev):
            alerts.append(f"🧭 EMA200 Break ↑ ({tf})")
        if (price_now < ema_now and price_prev >= ema_prev):
            alerts.append(f"🧭 EMA200 Break ↓ ({tf})")

        # RSI extremes
        if rsi >= rsi_hot:
            alerts.append(f"🔥 RSI {rsi:.0f} ({tf})")
        if rsi <= rsi_cold:
            alerts.append(f"❄️ RSI {rsi:.0f} ({tf})")

    # Label
    label = "HOLD"
    emoji = "🟡"
    if alerts:
        label = "Alert"; emoji = "🚀" if change_p >= 0 else "🔻"
    elif info_hit or rsi_p >= rsi_hot or rsi_p <= rsi_cold:
        label = "Info"; emoji = "ℹ️"

    summary = [f"{emoji} {header} — {label}", mtf, indi]

    # Für CSV-Log basismetriken zurückgeben
    metrics = {
        "price": price,
        "pct_primary": change_p,
        "rsi": rsi_p,
        "ema200_dist": ema_dist,
        "macd_dir": "UP" if macd_now>=macds_now else "DOWN",
        "alerts_count": len(alerts),
        "label": label
    }
    return summary, alerts, metrics

# ------------- Build & Write -------------
def write_text(path: str, content: str):
    with open(path,"w",encoding="utf-8") as f:
        f.write(content.strip() + "\n")

def append_csv_row(ts: str, sym: str, tf: str, metrics: Dict[str, Any]):
    with open(CSV_LOG,"a",newline="",encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([ts, sym, tf, metrics["price"], metrics["pct_primary"], metrics["rsi"],
                    metrics["ema200_dist"], metrics["macd_dir"], metrics["label"], metrics["alerts_count"]])

def build_messages():
    coins = load_coins()
    state = load_state()
    ensure_csv_header()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 Signal Snapshot — {ts}", "Basis: USD • TFs: 5m/15m/1h/4h/1d • Quelle: Binance", ""]
    alert_lines_all: List[str] = []

    for cfg in coins:
        sym = cfg["symbol"].upper()
        pair = cfg.get("binance", f"{sym}USDT").upper()
        th = merge_thresholds(cfg)

        summary, raw_alerts, metrics = analyze_coin(sym, pair, th)

        # Cooldown-Filter für Alerts
        filtered = []
        for a in raw_alerts:
            # Richtung/Kind/TF extrahieren
            if "↑" in a: direction = "UP"
            elif "↓" in a: direction = "DOWN"
            else: direction = "UP" if ("MOVE" in a and "-" not in a) else "DOWN"

            if "MOVE" in a:
                kind="MOVE"; tf=a.split("MOVE ")[1].split(" ")[0]
            elif "MACD" in a:
                kind="MACD"; tf=a.split("(")[1].split(")")[0]
            elif "EMA200" in a:
                kind="EMA200"; tf=a.split("(")[1].split(")")[0]
            elif "RSI" in a:
                kind="RSI"; tf=a.split("(")[1].split(")")[0]
            else:
                kind="GEN"; tf=PRIMARY_TF

            key = make_key(tf, kind, direction)
            if allowed(state, sym, key):
                remember(state, sym, key)
                filtered.append(f"{sym} • {a}")

        # Summary-Block
        lines += summary
        if filtered:
            lines.append("🚨 Alerts:")
            for a in filtered:
                lines.append(f"• {a}")
        lines.append("")

        # Alerts gesammelt (eigene Nachricht)
        alert_lines_all.extend(filtered)

        # CSV-Log
        append_csv_row(ts, sym, PRIMARY_TF, metrics)

    # Übersicht schreiben
    if not alert_lines_all:
        lines.append("🟡 Keine nennenswerte Bewegung über den Alert-Schwellen.")
    write_text(MSG_PATH, "\n".join(lines))

    # Alerts separat schreiben (oder leeren)
    alerts_text = ""
    if alert_lines_all:
        alerts_text = "🚨 Alerts — " + ts + "\n" + "\n".join([f"• {x}" for x in alert_lines_all])
    write_text(ALERTS_PATH, alerts_text if alerts_text else "")

    save_state(state)

def main():
    build_messages()
    print("message.txt + alerts.txt + state + csv geschrieben ✔")

if __name__ == "__main__":
    main()
