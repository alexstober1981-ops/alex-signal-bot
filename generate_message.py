#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Expert Signals generator for Telegram:
- Coins: BTC, ETH, SOL, KAS, RNDR, FET, SUI, AVAX (+ optional VET, XRP, ADA)
- Data:
    • Binance klines (15m & 1h) -> RSI & MACD
    • CoinGecko current price + 24h change
- Output:
    • message.txt  (for telegram_send.py)
    • signal_state.json (metrics snapshot)
"""

import json
import math
import os
from datetime import datetime, timezone

import requests

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

COINS = [
    # symbol, CoinGecko id, Binance symbol (USDT pair)
    ("BTC", "bitcoin", "BTCUSDT"),
    ("ETH", "ethereum", "ETHUSDT"),
    ("SOL", "solana", "SOLUSDT"),
    ("KAS", "kaspa", "KASUSDT"),
    ("RNDR", "render-token", "RNDRUSDT"),
    ("FET", "fetch-ai", "FETUSDT"),        # (nach ASI-Merger bleibt FET Symbol auf Binance)
    ("SUI", "sui", "SUIUSDT"),
    ("AVAX", "avalanche-2", "AVAXUSDT"),
    # Optional – einfach einkommentieren
    # ("VET", "vechain", "VETUSDT"),
    # ("XRP", "ripple", "XRPUSDT"),
    # ("ADA", "cardano", "ADAUSDT"),
]

BINANCE_BASE = "https://api.binance.com/api/v3/klines"
COINGECKO_PRICE = "https://api.coingecko.com/api/v3/simple/price"

# Technical settings
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

TIMEOUT = 12  # seconds
HEADERS = {"User-Agent": "alex-signal-bot/1.0"}

# ------------------------------------------------------------
# Helpers: math
# ------------------------------------------------------------

def ema(values, period):
    """Exponential Moving Average without pandas."""
    if not values or period <= 0 or len(values) < period:
        return []
    k = 2 / (period + 1)
    ema_vals = []
    sma = sum(values[:period]) / period
    ema_vals.append(sma)
    for price in values[period:]:
        ema_vals.append(price * k + ema_vals[-1] * (1 - k))
    return ema_vals

def rsi(values, period=14):
    """Relative Strength Index (Wilder)."""
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def macd(values, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram (last values)."""
    if len(values) < slow + signal:
        return None, None, None
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    # align to same tail length
    diff_len = len(ema_slow) - len(ema_fast)
    if diff_len > 0:
        ema_slow = ema_slow
        ema_fast = ema_fast[diff_len:]
    elif diff_len < 0:
        ema_fast = ema_fast
        ema_slow = ema_slow[-len(ema_fast):]
    macd_line_all = [a - b for a, b in zip(ema_fast, ema_slow)]
    signal_all = ema(macd_line_all, signal)
    if not signal_all:
        return None, None, None
    # align tail
    macd_line_all = macd_line_all[-len(signal_all):]
    hist_all = [m - s for m, s in zip(macd_line_all, signal_all)]
    return macd_line_all[-1], signal_all[-1], hist_all[-1]

def fmt_price(x):
    if x is None:
        return "—"
    if x >= 1000:
        return f"{x:,.2f}".replace(",", " ")
    if x >= 1:
        return f"{x:.2f}"
    return f"{x:.4f}"

def fmt_pct(x):
    if x is None:
        return "—"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:.2f}%"

# ------------------------------------------------------------
# Data fetchers
# ------------------------------------------------------------

def get_binance_closes(symbol: str, interval: str, limit: int = 250):
    """Fetch close prices from Binance klines."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(BINANCE_BASE, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        closes = [float(c[4]) for c in data]  # close price
        return closes
    except Exception:
        return None

def get_cg_prices(ids):
    """Get current price and 24h change from CoinGecko."""
    params = {
        "ids": ",".join(ids),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
    }
    try:
        r = requests.get(COINGECKO_PRICE, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

# ------------------------------------------------------------
# Signal logic
# ------------------------------------------------------------

def classify_signal(rsi_15m, rsi_1h, macd_line, macd_signal, change_24h):
    """
    Return (signal_label, reason_emoji, notes)
    Rules – pragmatisch & trader-tauglich:
    - BUY: 1h RSI < 35  AND MACD trending up (macd_line > macd_signal)  OR 24h change <= -6%
    - SELL: 15m RSI > 70 OR (macd_line < macd_signal AND change_24h >= +6%)
    - INFO/ALERT: starke Bewegung | extreme RSI
    - else: HOLD
    """
    notes = []

    macd_up = (macd_line is not None and macd_signal is not None and macd_line > macd_signal)
    macd_down = (macd_line is not None and macd_signal is not None and macd_line < macd_signal)

    extreme_up = (rsi_15m is not None and rsi_15m >= 75) or (rsi_1h is not None and rsi_1h >= 75)
    extreme_down = (rsi_15m is not None and rsi_15m <= 25) or (rsi_1h is not None and rsi_1h <= 25)
    big_move_up = change_24h is not None and change_24h >= 6.0
    big_move_down = change_24h is not None and change_24h <= -6.0

    if (rsi_1h is not None and rsi_1h < 35 and macd_up) or big_move_down:
        return "🟢 BUY", "🚀", "Dip/Upturn"

    if (rsi_15m is not None and rsi_15m > 70) or (macd_down and big_move_up):
        return "🔴 SELL", "📉", "Überkauft/Abdrehend"

    if extreme_up or extreme_down or big_move_up or big_move_down:
        return "ℹ️ INFO", "⚠️", "Starke Bewegung / Extrem"

    return "🟡 HOLD", "⏸️", ""

# ------------------------------------------------------------
# Build message
# ------------------------------------------------------------

def build_message():
    # 1) Current prices & 24h change (CG)
    cg_ids = [c[1] for c in COINS]
    cg = get_cg_prices(cg_ids)

    # 2) Indicators (Binance klines)
    rows = []
    state = {"generated_at": datetime.now(timezone.utc).isoformat(), "coins": []}

    for symbol, cg_id, binance in COINS:
        # 15m and 1h closes
        closes_15m = get_binance_closes(binance, "15m", limit=300)
        closes_1h = get_binance_closes(binance, "1h", limit=300)

        rsi15 = rsi(closes_15m, RSI_PERIOD) if closes_15m else None
        rsi1h = rsi(closes_1h, RSI_PERIOD) if closes_1h else None
        m_line, m_sig, m_hist = macd(closes_1h, MACD_FAST, MACD_SLOW, MACD_SIGNAL) if closes_1h else (None, None, None)

        # price + 24h change
        price = None
        ch24 = None
        if cg and cg_id in cg and "usd" in cg[cg_id]:
            price = float(cg[cg_id]["usd"])
            ch24 = float(cg[cg_id].get("usd_24h_change", 0.0))

        # signal
        sig, mark, note = classify_signal(rsi15, rsi1h, m_line, m_sig, ch24)

        macd_dir = "🟢 Long" if (m_line is not None and m_sig is not None and m_line >= m_sig) else ("🔴 Short" if (m_line is not None and m_sig is not None) else "—")

        line = (
            f"{mark} {symbol}: ${fmt_price(price)} | 15m RSI: {round(rsi15) if rsi15 is not None else '—'} | "
            f"1h MACD: {macd_dir} | 24h: {fmt_pct(ch24)}\n"
            f"➡️ Empfehlung: {sig}" + (f" ({note})" if note else "")
        )

        rows.append(line)

        state["coins"].append({
            "symbol": symbol,
            "price_usd": price,
            "change_24h": ch24,
            "rsi_15m": rsi15,
            "rsi_1h": rsi1h,
            "macd_1h": {"line": m_line, "signal": m_sig, "hist": m_hist},
            "signal": sig,
            "note": note,
        })

    # Header & Legend
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"📊 Signal Snapshot — {now_utc}\n"
        f"Basis: USD • Intervalle: 15 Min & 1 h • Quellen: Binance (Indikatoren), CoinGecko (Preis/24h)\n\n"
    )
    legend = (
        "\nLegende: 🟡 Hold • ℹ️ Info ⚠️ • 🟢/🔴 Signal\n"
    )

    message = header + "\n\n".join(rows) + legend
    return message, state

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    msg, state = build_message()

    # Write files for the workflow
    with open("message.txt", "w", encoding="utf-8") as f:
        f.write(msg.strip() + "\n")

    with open("signal_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print("Message and state generated ✔")

if __name__ == "__main__":
    main()
