#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pro-Ultra Signal-Generator (mit Filtersupport aus coins.json)
- TFs: 5m, 15m, 1h, 4h, 1d
- Indikatoren: RSI(14), EMA50/EMA200, MACD(12/26/9)
- Per-Coin thresholds + filters (RSI-Band, EMA200-Trend, MACD-Confirm)
- Alerts: MOVE / MACD-Cross / EMA200-Break / RSI-Extrem (mit Cooldown)
- Outputs:
  • message.txt  (Übersicht)
  • alerts.txt   (nur Alerts – optional anderer Chat)
  • signal_state.json (Cooldown/Meta)
  • signals_log.csv (CSV-Log der Haupt-Metriken)
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

STATE_PATH  = "signal_state.json"
MSG_PATH    = "message.txt"
ALERTS_PATH = "alerts.txt"
CSV_LOG     = "signals_log.csv"

# Defaults (werden pro Coin via coins.json überschrieben)
DEFAULT_THRESHOLDS = {
    "move": {"5m": 1.0, "15m": 2.0, "1h": 3.0, "4h": 4.0, "1d": 5.0, "24h": 5.0}
}
DEFAULT_FILTERS = {
    "rsi":   { "min": None, "max": None },   # None = kein Filter
    "ema200":{ "trend": "any" },             # any|above|below
    "macd":  { "confirm": False }            # True = MACD-Cross Pflicht (auf PRIMARY_TF)
}

COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "60"))
UA = {"User-Agent": "alex-pro-crypto/filters/1.0"}

# ------------- HTTP with retry/backoff -------------
def http_get(url: str, params: dict, timeout=20, tries=4, backoff=2.0):
    delay = 1.5
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=UA)
            if r.status_code == 429:
                try:
                    ra = float(r.json().get("retry_after", delay))
                except Exception:
                    ra = delay
                time.sleep(ra); continue
            r.raise_for_status()
            return r
        except Exception:
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
            w.writerow([
                "ts_utc","symbol","tf","close","pct_vs_prev",
                "rsi","ema200_dist","macd_dir","label","alerts_count"
            ])

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

# ------------- Helpers -------------
def pct_last_step(df: pd.DataFrame) -> float:
    if len(df) < 2: return 0.0
    c0 = float(df.iloc[-2]["close"])
    c1 = float(df.iloc[-1]["close"])
    return (c1/c0 - 1.0) * 100.0 if c0 else 0.0

def macd_dir_str(df: pd.DataFrame) -> str:
    if len(df) < 1: return "—"
    last = df.iloc[-1]
    return "↑" if float(last["macd"]) >= float(last["macd_signal"]) else "↓"

def fmt_usd(x: float) -> str:
    if x >= 1: return f"${x:,.2f}"
    return f"${x:.4f}"

def sign_pct(x: float) -> str:
    return f"{x:+.2f}%"

# ------------- Thresholds/Filters merge -------------
def merged_thresholds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    th = json.loads(json.dumps(DEFAULT_THRESHOLDS))
    user = cfg.get("thresholds")
    if isinstance(user, dict):
        # shallow-merge dicts
        for k, v in user.items():
            if isinstance(v, dict) and k in th:
                th[k].update(v)
            else:
                th[k] = v
    # Ensure keys
    th.setdefault("move", {}).setdefault("24h", DEFAULT_THRESHOLDS["move"]["1d"])
    return th

def merged_filters(cfg: Dict[str, Any]) -> Dict[str, Any]:
    flt = json.loads(json.dumps(DEFAULT_FILTERS))
    user = cfg.get("filters")
    if isinstance(user, dict):
        # recursive-ish merge
        for k, v in user.items():
            if isinstance(v, dict) and k in flt:
                flt[k].update(v)
            else:
                flt[k] = v
    # normalize values
    rsi_min = flt["rsi"].get("min"); rsi_max = flt["rsi"].get("max")
    flt["rsi"]["min"] = None if rsi_min in ("", None) else float(rsi_min)
    flt["rsi"]["max"] = None if rsi_max in ("", None) else float(rsi_max)
    tr = str(flt["ema200"].get("trend", "any")).lower()
    flt["ema200"]["trend"] = tr if tr in ("any","above","below") else "any"
    flt["macd"]["confirm"] = bool(flt["macd"].get("confirm", False))
    return flt

# ------------- Cooldown keys -------------
def make_key(tf: str, kind: str, direction: str) -> str:
    return f"{tf}:{kind}:{direction}"

def allowed(state: Dict[str, Any], sym: str, key: str) -> bool:
    last = int(state.get("alerts", {}).get(sym, {}).get(key, 0))
    return (int(time.time()) - last) >= COOLDOWN_MINUTES * 60

def remember(state: Dict[str, Any], sym: str, key: str) -> None:
    state.setdefault("alerts", {}).setdefault(sym, {})[key] = int(time.time())

# ------------- Filterprüfung -------------
def passes_filters_primary(df_primary: pd.DataFrame, flt: Dict[str, Any]) -> bool:
    """Prüft RSI-Band, EMA200-Trend und optional MACD-Confirm auf PRIMARY_TF."""
    if len(df_primary) < 2: 
        return True  # nichts zu filtern
    last = df_primary.iloc[-1]
    prev = df_primary.iloc[-2]
    price = float(last["close"])
    ema200_now  = float(last["ema200"])
    ema200_prev = float(prev["ema200"])
    macd_now    = float(last["macd"])
    macds_now   = float(last["macd_signal"])
    macd_prev   = float(prev["macd"])
    macds_prev  = float(prev["macd_signal"])
    rsi_now     = float(last["rsi"])

    # RSI-Band
    rmin = flt["rsi"]["min"]; rmax = flt["rsi"]["max"]
    if rmin is not None and rsi_now < rmin: 
        return False
    if rmax is not None and rsi_now > rmax: 
        return False

    # EMA200-Trend
    trend = flt["ema200"]["trend"]
    if trend == "above":
        if not (ema200_now and price > ema200_now): 
            return False
    elif trend == "below":
        if not (ema200_now and price < ema200_now):
            return False

    # MACD-Confirm (Cross)
    if flt["macd"]["confirm"]:
        cross_up = (macd_now > macds_now) and (macd_prev <= macds_prev)
        cross_dn = (macd_now < macds_now) and (macd_prev >= macds_prev)
        if not (cross_up or cross_dn):
            return False

    return True

# ------------- Analyse pro Coin -------------
def analyze_coin(sym: str, pair: str, th: Dict[str, Any], flt: Dict[str, Any]) -> Tuple[List[str], List[str], Dict[str, Any]]:
    # alle TFs laden
    tfs: Dict[str, pd.DataFrame] = {}
    for tf in TIMEFRAMES:
        d = add_indicators(binance_klines(pair, tf, KLIMIT))
        tfs[tf] = d

    # 24h change
    ch24 = binance_24h_change(pair)

    # PRIMARY_TF snapshot
    dfp = tfs[PRIMARY_TF]
    last = dfp.iloc[-1]
    prev = dfp.iloc[-2] if len(dfp) > 1 else last
    price = float(last["close"])
    change_p = pct_last_step(dfp)
    rsi_p = float(last["rsi"])
    ema200 = float(last["ema200"])
    macd_now = float(last["macd"])
    macds_now = float(last["macd_signal"])
    ema_dist = ((price/ema200) - 1.0) * 100.0 if ema200 else float("nan")

    # Summary-Zeilen
    trend = "📈" if change_p >= 0 else "📉"
    header = f"{sym}: {fmt_usd(price)} • {PRIMARY_TF} {sign_pct(change_p)} • 24h {sign_pct(ch24)}"
    indi   = f"{trend} RSI {rsi_p:.0f} • EMA200 {sign_pct(ema_dist)} • MACD {'↑' if macd_now>=macds_now else '↓'}"
    mtf    = "MTF Δ: " + " • ".join([f"{tf} {sign_pct(pct_last_step(tfs[tf]))}" for tf in TIMEFRAMES])

    # Alerts
    alerts: List[str] = []
    info_hit = False

    move_t = th["move"]
    th_24h = float(move_t.get("24h", DEFAULT_THRESHOLDS["move"]["1d"]))

    # 24h starker Move => globaler Alert (unabhängig von Filtern)
    if abs(ch24) >= th_24h:
        alerts.append(f"🚨 24h MOVE {sign_pct(ch24)}")

    # Pro-TF Bewegungen + Indikator-Events – ABER: Filters greifen (PRIMARY-TF) für MOVE/MACD/EMA/RSI
    # (Filter sind "Bestätiger"; 24h-Alert bleibt immer durchlässig)
    primary_pass = passes_filters_primary(dfp, flt)

    for tf in TIMEFRAMES:
        d = tfs[tf]
        if len(d) < 3: 
            continue
        pct = pct_last_step(d)
        rsi = float(d.iloc[-1]["rsi"])
        ema_now = float(d.iloc[-1]["ema200"])
        ema_prev= float(d.iloc[-2]["ema200"])
        macd_now_t = float(d.iloc[-1]["macd"])
        macds_now_t= float(d.iloc[-1]["macd_signal"])
        macd_prev_t= float(d.iloc[-2]["macd"])
        macds_prev_t=float(d.iloc[-2]["macd_signal"])
        price_now = float(d.iloc[-1]["close"])
        price_prev= float(d.iloc[-2]["close"])

        # MOVE -> nur wenn Filter auf PRIMARY_TF bestehen (primary_pass)
        if primary_pass and tf in move_t and abs(pct) >= float(move_t[tf]):
            alerts.append(f"🚨 MOVE {tf} {sign_pct(pct)}")

        # MACD Crossover (als Ereignis) – erfordert primary_pass
        if primary_pass and (macd_now_t > macds_now_t and macd_prev_t <= macds_prev_t):
            alerts.append(f"⚡ MACD Cross ↑ ({tf})")
        if primary_pass and (macd_now_t < macds_now_t and macd_prev_t >= macds_prev_t):
            alerts.append(f"⚡ MACD Cross ↓ ({tf})")

        # EMA200 Break – erfordert primary_pass
        if primary_pass and (price_now > ema_now and price_prev <= ema_prev):
            alerts.append(f"🧭 EMA200 Break ↑ ({tf})")
        if primary_pass and (price_now < ema_now and price_prev >= ema_prev):
            alerts.append(f"🧭 EMA200 Break ↓ ({tf})")

        # RSI Extrem – erfordert primary_pass
        rmin = flt["rsi"]["min"]; rmax = flt["rsi"]["max"]
        if primary_pass:
            if rmax is not None and rsi >= rmax:
                alerts.append(f"🔥 RSI {rsi:.0f} ({tf})")
            if rmin is not None and rsi <= rmin:
                alerts.append(f"❄️ RSI {rsi:.0f} ({tf})")

        # Info-Hinweis (falls kein Alert), wenn MOVE nahe an Schwelle (50%) – nur PRIMARY_TF
        if tf == PRIMARY_TF and not alerts:
            thr = float(move_t.get(PRIMARY_TF, 999))
            if abs(pct) >= 0.5 * thr:
                info_hit = True

    # Label
    label = "HOLD"; emoji = "🟡"
    if alerts:
        label = "Alert"; emoji = "🚀" if change_p >= 0 else "🔻"
    elif info_hit:
        label = "Info"; emoji = "ℹ️"

    summary = [f"{emoji} {header} — {label}", mtf, indi]

    # Metriken (für CSV)
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
        f.write((content.strip() + "\n") if content else "")

def append_csv_row(ts: str, sym: str, tf: str, metrics: Dict[str, Any]):
    with open(CSV_LOG,"a",newline="",encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([ts, sym, tf, metrics["price"], metrics["pct_primary"],
                    metrics["rsi"], metrics["ema200_dist"], metrics["macd_dir"],
                    metrics["label"], metrics["alerts_count"]])

def build_messages():
    coins = load_coins()
    state = load_state()
    ensure_csv_header()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = [f"📊 Signal Snapshot — {ts}", "Basis: USD • TFs: 5m/15m/1h/4h/1d • Quelle: Binance", ""]
    alert_lines_all: List[str] = []

    for cfg in coins:
        sym  = cfg["symbol"].upper()
        pair = cfg.get("binance", f"{sym}USDT").upper()
        th   = merged_thresholds(cfg)
        flt  = merged_filters(cfg)

        summary, raw_alerts, metrics = analyze_coin(sym, pair, th, flt)

        # Cooldown pro Alert-Event
        filtered = []
        for a in raw_alerts:
            # Richtung + TF + Art extrahieren
            direction = "UP" if "↑" in a or ("MOVE" in a and "-" not in a) else "DOWN"
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

            key = f"{tf}:{kind}:{direction}"
            if allowed(state, sym, key):
                remember(state, sym, key)
                filtered.append(f"{sym} • {a}")

        # Summary block
        lines += summary
        if filtered:
            lines.append("🚨 Alerts:")
            lines += [f"• {x}" for x in filtered]
        lines.append("")

        alert_lines_all.extend(filtered)

        # CSV Log
        append_csv_row(ts, sym, PRIMARY_TF, metrics)

    # Übersicht
    if not alert_lines_all:
        lines.append("🟡 Keine nennenswerte Bewegung über den Alert-Schwellen.")

    write_text(MSG_PATH, "\n".join(lines))

    # Alerts
    alerts_text = ""
    if alert_lines_all:
        alerts_text = "🚨 Alerts — " + ts + "\n" + "\n".join([f"• {x}" for x in alert_lines_all])
    write_text(ALERTS_PATH, alerts_text)

    save_state(state)

def main():
    try:
        build_messages()
        print("message.txt + alerts.txt + state + csv geschrieben ✔")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
