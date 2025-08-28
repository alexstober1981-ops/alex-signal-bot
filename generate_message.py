#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Builds a pro-grade crypto signal message.
- Source: Binance klines (15m) + 24h ticker
- Indicators: RSI(14), EMA50/EMA200, MACD
- Alert logic incl. cooldown persisted in signal_state.json
- Output: message.txt (consumed by telegram_send.py)
"""

import os, json, time, math
from datetime import datetime, timezone
from typing import Dict, Any, List

import requests
import pandas as pd
import numpy as np

try:
    # technical indicators
    from ta.momentum import RSIIndicator
    from ta.trend import EMAIndicator, MACD
except Exception:
    # minimal fallback if "ta" would be missing
    RSIIndicator = EMAIndicator = MACD = None

# --------- Config ---------
DEFAULT_COINS = [
    {"symbol": "BTC", "binance": "BTCUSDT"},
    {"symbol": "ETH", "binance": "ETHUSDT"},
    {"symbol": "SOL", "binance": "SOLUSDT"},
]
INTERVAL = "15m"
KLIMIT = 200                         # candles to fetch
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MINUTES", "60"))  # minutes between ALERTS per coin
STATE_PATH = "signal_state.json"
MSG_PATH = "message.txt"
# thresholds
ALERT_MOVE_15M = 2.0                 # % 15m move for Alert
INFO_MOVE_15M = 0.5                  # % 15m move lower bound for Info
RSI_HOT = 70
RSI_COLD = 30
# --------------------------


def load_coins() -> List[Dict[str, str]]:
    # allow overriding via coins.json
    if os.path.exists("coins.json"):
        with open("coins.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    return DEFAULT_COINS


def binance_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    raw = r.json()
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "n_trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ]
    df = pd.DataFrame(raw, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def binance_24h_change(symbol: str) -> float:
    url = "https://api.binance.com/api/v3/ticker/24hr"
    r = requests.get(url, params={"symbol": symbol}, timeout=15)
    r.raise_for_status()
    j = r.json()
    return float(j.get("priceChangePercent", 0.0))


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 50:
        return df.assign(
            rsi=np.nan, ema50=np.nan, ema200=np.nan,
            macd=np.nan, macd_signal=np.nan
        )
    close = df["close"]
    rsi = RSIIndicator(close, window=14).rsi() if RSIIndicator else pd.Series(np.nan, index=df.index)
    ema50 = EMAIndicator(close, window=50).ema_indicator() if EMAIndicator else pd.Series(np.nan, index=df.index)
    ema200 = EMAIndicator(close, window=200).ema_indicator() if EMAIndicator else pd.Series(np.nan, index=df.index)
    macd_obj = MACD(close) if MACD else None
    macd = macd_obj.macd() if macd_obj else pd.Series(np.nan, index=df.index)
    macd_signal = macd_obj.macd_signal() if macd_obj else pd.Series(np.nan, index=df.index)
    out = df.copy()
    out["rsi"] = rsi
    out["ema50"] = ema50
    out["ema200"] = ema200
    out["macd"] = macd
    out["macd_signal"] = macd_signal
    return out


def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"coins": {}}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fmt_usd(x: float) -> str:
    if x >= 1000:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:,.2f}"
    return f"${x:.4f}"


def classify_signal(df: pd.DataFrame) -> Dict[str, Any]:
    """Return dict with computed metrics and label/emoji."""
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    price = float(last["close"])
    prev_price = float(prev["close"])
    change_15m = (price / prev_price - 1.0) * 100.0 if prev_price else 0.0
    rsi = float(last.get("rsi", np.nan))
    ema200 = float(last.get("ema200", np.nan))
    ema200_prev = float(prev.get("ema200", np.nan))
    macd, macdsig = float(last.get("macd", np.nan)), float(last.get("macd_signal", np.nan))
    macd_prev, macdsig_prev = float(prev.get("macd", np.nan)), float(prev.get("macd_signal", np.nan))

    # Cross detection
    macd_cross_up = (macd > macdsig) and (macd_prev <= macdsig_prev)
    macd_cross_dn = (macd < macdsig) and (macd_prev >= macdsig_prev)
    ema_cross_up = (price > ema200) and (prev_price <= ema200_prev)
    ema_cross_dn = (price < ema200) and (prev_price >= ema200_prev)

    # Baseline label
    label = "HOLD"
    emoji = "🟡"

    # Info zone
    if abs(change_15m) >= INFO_MOVE_15M or (not math.isnan(rsi) and (rsi >= RSI_HOT or rsi <= RSI_COLD)):
        label, emoji = "Info", "ℹ️"

    # Alert conditions
    alert = False
    direction = None
    if change_15m >= ALERT_MOVE_15M or macd_cross_up or ema_cross_up or (not math.isnan(rsi) and rsi >= 75):
        alert, direction = True, "UP"
    if change_15m <= -ALERT_MOVE_15M or macd_cross_dn or ema_cross_dn or (not math.isnan(rsi) and rsi <= 25):
        alert, direction = True, "DOWN"

    if alert:
        label = "Alert"
        emoji = "🚀" if direction == "UP" else "🔻"

    trend_arrow = "📈" if change_15m >= 0 else "📉"
    macd_dir = "↑" if macd >= macdsig else "↓"
    ema200_dist = ((price / ema200) - 1.0) * 100.0 if not math.isnan(ema200) and ema200 else float("nan")

    return {
        "price": price,
        "change_15m": change_15m,
        "rsi": rsi,
        "ema200_dist": ema200_dist,
        "macd_dir": macd_dir,
        "label": label,
        "emoji": emoji,
        "trend": trend_arrow,
        "alert": alert,
        "direction": direction,
    }


def allowed_alert(symbol: str, state: Dict[str, Any]) -> bool:
    now = int(time.time())
    last_ts = int(state.get("coins", {}).get(symbol, {}).get("last_alert_ts", 0))
    return (now - last_ts) >= COOLDOWN_MIN * 60


def remember_alert(symbol: str, state: Dict[str, Any]) -> None:
    now = int(time.time())
    state.setdefault("coins", {}).setdefault(symbol, {})["last_alert_ts"] = now


def build_message() -> str:
    coins = load_coins()
    state = load_state()

    header_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 Signal Snapshot — {header_ts}", "Basis: USD • Intervall: 15 Min • Quelle: Binance", ""]

    any_alert = False

    for c in coins:
        sym = c["symbol"].upper()
        pair = c["binance"].upper()

        # Fetch
        df = binance_klines(pair, INTERVAL, KLIMIT)
        df = compute_indicators(df)
        metrics = classify_signal(df)
        change_24h = binance_24h_change(pair)

        price = metrics["price"]
        change15 = metrics["change_15m"]
        rsi = metrics["rsi"]
        ema200d = metrics["ema200_dist"]
        macd_dir = metrics["macd_dir"]
        label = metrics["label"]
        emoji = metrics["emoji"]
        trend = metrics["trend"]

        # Alert cooldown control
        if metrics["alert"]:
            if allowed_alert(sym, state):
                any_alert = True
                remember_alert(sym, state)
            else:
                # downgrade to Info if still on cooldown
                label = "Info"
                emoji = "ℹ️"

        # First line (summary)
        lines.append(
            f"{emoji} {sym}: {fmt_usd(price)} • 15m {change15:+.2f}% • 24h {change_24h:+.2f}% — {label.upper()}"
        )

        # Second line (indicators)
        rsi_txt = f"RSI {rsi:.0f}" if not math.isnan(rsi) else "RSI n/a"
        ema_txt = f"EMA200 {ema200d:+.2f}%" if not math.isnan(ema200d) else "EMA200 n/a"
        lines.append(f"{trend} {rsi_txt} • {ema_txt} • MACD {macd_dir}")
        lines.append("")  # spacer

    if not any_alert:
        lines.append("🟡 Keine nennenswerte Bewegung über den Alert-Schwellen.")

    save_state(state)
    return "\n".join(lines).strip()


def main():
    msg = build_message()
    with open(MSG_PATH, "w", encoding="utf-8") as f:
        f.write(msg)
    print("Message generated and written to message.txt")


if __name__ == "__main__":
    main()
