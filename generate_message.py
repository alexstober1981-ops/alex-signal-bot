#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pro Signal Generator for Telegram
- Datenquellen:
  • Binance: 15m & 1h Klines (RSI, MACD, Breakouts, ATR)
  • CoinGecko: Spot-Preis & 24h-%Change
- Output:
  • message.txt (für telegram_send.py)
  • signal_state.json (kompletter Snapshot)
"""

import json
from datetime import datetime, timezone
import requests

# ------------------------------- Konfiguration -------------------------------

COINS = [
    ("BTC", "bitcoin", "BTCUSDT"),
    ("ETH", "ethereum", "ETHUSDT"),
    ("SOL", "solana", "SOLUSDT"),
    ("KAS", "kaspa", "KASUSDT"),
    ("RNDR", "render-token", "RNDRUSDT"),
    ("FET", "fetch-ai", "FETUSDT"),     # (ASI-Umbenennung – Ticker auf Binance bleibt FET)
    ("SUI", "sui", "SUIUSDT"),
    ("AVAX", "avalanche-2", "AVAXUSDT"),
    # weitere Coins optional:
    # ("VET", "vechain", "VETUSDT"),
    # ("XRP", "ripple", "XRPUSDT"),
    # ("ADA", "cardano", "ADAUSDT"),
]

BINANCE_BASE = "https://api.binance.com/api/v3/klines"
CG_PRICE = "https://api.coingecko.com/api/v3/simple/price"

TIMEOUT = 12
HEADERS = {"User-Agent": "alex-signal-bot/2.0"}

# Technische Parameter
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
ATR_PERIOD = 14
BRK_LOOKBACK = 20   # 1h-Breakout-Periode

# ------------------------------- Utils / Math --------------------------------

def ema(values, period):
    if not values or len(values) < period:
        return []
    k = 2 / (period + 1)
    out = []
    sma = sum(values[:period]) / period
    out.append(sma)
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def rsi(values, period=14):
    n = len(values)
    if n < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        d = values[i] - values[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, n):
        d = values[i] - values[i-1]
        g = max(d, 0.0)
        l = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def macd_series(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return None, None, None
    ef = ema(values, fast)
    es = ema(values, slow)
    # align
    if len(ef) > len(es):
        ef = ef[-len(es):]
    elif len(es) > len(ef):
        es = es[-len(ef):]
    macd_line = [a - b for a, b in zip(ef, es)]
    sig_line = ema(macd_line, signal)
    macd_line = macd_line[-len(sig_line):]
    hist = [m - s for m, s in zip(macd_line, sig_line)]
    return macd_line, sig_line, hist

def atr(high, low, close, period=14):
    n = len(close)
    if n < period + 1:
        return None
    tr = []
    for i in range(n):
        if i == 0:
            tr.append(high[i] - low[i])
        else:
            tr.append(max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            ))
    atr_vals = ema(tr, period)
    return atr_vals[-1] if atr_vals else None

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
    s = "+" if x >= 0 else ""
    return f"{s}{x:.2f}%"

# ------------------------------- Datenfetcher ---------------------------------

def get_klines(symbol, interval, limit=300):
    p = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(BINANCE_BASE, params=p, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        raw = r.json()
        out = {
            "open": [float(k[1]) for k in raw],
            "high": [float(k[2]) for k in raw],
            "low":  [float(k[3]) for k in raw],
            "close":[float(k[4]) for k in raw],
        }
        return out
    except Exception:
        return None

def get_prices(ids):
    p = {"ids": ",".join(ids), "vs_currencies": "usd", "include_24hr_change": "true"}
    try:
        r = requests.get(CG_PRICE, params=p, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

# ------------------------------- Scoring/Logic --------------------------------

def breakout_flags(h, l, c, lookback=20):
    if not c or len(c) < lookback + 2:
        return False, False
    last = c[-1]
    prev_high = max(h[-(lookback+1):-1])
    prev_low  = min(l[-(lookback+1):-1])
    up = last > prev_high * 1.001
    dn = last < prev_low  * 0.999
    return up, dn

def classify_and_score(rsi15, rsi1h, macd_last, macd_sig_last, macd_hist_last, macd_hist_prev,
                        brk_up, brk_dn, ch24):
    score = 0.0
    notes = []

    # RSI
    if rsi1h is not None:
        if rsi1h < 30: score += 2; notes.append("RSI1h<30")
        elif rsi1h < 35: score += 1; notes.append("RSI1h<35")
        elif rsi1h > 75: score -= 2; notes.append("RSI1h>75")
        elif rsi1h > 70: score -= 1; notes.append("RSI1h>70")
    if rsi15 is not None:
        if rsi15 < 35: score += 0.5
        elif rsi15 > 70: score -= 0.5

    # MACD Richtung + Momentum
    if macd_last is not None and macd_sig_last is not None:
        if macd_last > macd_sig_last:
            score += 1; notes.append("MACD↑")
        else:
            score -= 1; notes.append("MACD↓")
    if macd_hist_last is not None and macd_hist_prev is not None:
        if macd_hist_last > macd_hist_prev:
            score += 0.5; notes.append("Hist↑")
        else:
            score -= 0.5; notes.append("Hist↓")

    # Breakouts
    if brk_up:
        score += 1.5; notes.append("Breakout↑")
    if brk_dn:
        score -= 1.5; notes.append("Breakout↓")

    # 24h Change (starke Moves)
    if ch24 is not None:
        if ch24 <= -6.0:
            score += 1; notes.append("Dip")
        elif ch24 >= 6.0:
            score -= 1; notes.append("ExtUp")

    # Label
    if score >= 2.0:
        label, emoji = "🟢 BUY", "🚀"
    elif score <= -2.0:
        label, emoji = "🔴 SELL", "📉"
    elif abs(score) <= 1.0:
        label, emoji = "🟡 HOLD", "⏸️"
    else:
        label, emoji = "ℹ️ INFO", "⚠️"

    return label, emoji, score, ", ".join(notes)

# ------------------------------- Message Builder ------------------------------

def build_message():
    # Preise holen
    cg_ids = [c[1] for c in COINS]
    prices = get_prices(cg_ids)

    results = []
    state = {"generated_at": datetime.now(timezone.utc).isoformat(), "coins": []}

    for sym, cg_id, bin_sym in COINS:
        k15 = get_klines(bin_sym, "15m", 300)
        k1h = get_klines(bin_sym, "1h", 300)

        rsi15 = rsi(k15["close"], RSI_PERIOD) if k15 else None
        rsi1h = rsi(k1h["close"], RSI_PERIOD) if k1h else None

        macd_line, macd_sig, macd_hist = (None, None, None)
        hist_prev = None
        if k1h:
            ml, sl, h = macd_series(k1h["close"], MACD_FAST, MACD_SLOW, MACD_SIG)
            if ml and sl and h:
                macd_line, macd_sig, macd_hist = ml[-1], sl[-1], h[-1]
                if len(h) >= 2:
                    hist_prev = h[-2]

        brk_up, brk_dn = (False, False)
        atr_1h = None
        if k1h:
            brk_up, brk_dn = breakout_flags(k1h["high"], k1h["low"], k1h["close"], BRK_LOOKBACK)
            atr_1h = atr(k1h["high"], k1h["low"], k1h["close"], ATR_PERIOD)

        price = None
        ch24 = None
        if cg_id in prices:
            price = float(prices[cg_id].get("usd", None))
            ch24 = float(prices[cg_id].get("usd_24h_change", 0.0))

        label, emoji, score, notes = classify_and_score(
            rsi15, rsi1h, macd_line, macd_sig, macd_hist, hist_prev, brk_up, brk_dn, ch24
        )

        macd_dir = "🟢 Long" if (macd_line is not None and macd_sig is not None and macd_line >= macd_sig) else ("🔴 Short" if (macd_line is not None and macd_sig is not None) else "—")
        brk_txt = "↑" if brk_up else ("↓" if brk_dn else "—")

        # TP/SL (nur bei BUY/SELL)
        tp1 = tp2 = sl = None
        if price and atr_1h:
            if label.startswith("🟢"):
                tp1 = price + 1.0 * atr_1h
                tp2 = price + 2.0 * atr_1h
                sl  = price - 1.5 * atr_1h
            elif label.startswith("🔴"):
                tp1 = price - 1.0 * atr_1h
                tp2 = price - 2.0 * atr_1h
                sl  = price + 1.5 * atr_1h

        line = (
            f"{emoji} {sym}: ${fmt_price(price)} • 24h {fmt_pct(ch24)} • "
            f"RSI15 {round(rsi15) if rsi15 is not None else '—'}/RSI1h {round(rsi1h) if rsi1h is not None else '—'} • "
            f"MACD {macd_dir} • BRK {brk_txt}\n"
            f"➡️ {label}  (Score {score:.1f}" + (f" | {notes}" if notes else "") + ")"
        )
        if tp1 and tp2 and sl:
            line += f"\n   🎯 TP1 {fmt_price(tp1)} • TP2 {fmt_price(tp2)} • 🛡️ SL {fmt_price(sl)}"

        results.append({
            "symbol": sym,
            "line": line,
            "score": score,
            "label": label,
            "price": price,
            "tp1": tp1, "tp2": tp2, "sl": sl,
            "metrics": {
                "change_24h": ch24,
                "rsi_15m": rsi15, "rsi_1h": rsi1h,
                "macd": {"line": macd_line, "signal": macd_sig, "hist": macd_hist, "hist_prev": hist_prev},
                "breakout_up": brk_up, "breakout_down": brk_dn,
                "atr_1h": atr_1h,
            }
        })

        state["coins"].append({
            "symbol": sym,
            **results[-1]["metrics"],
            "price": price,
            "signal": label,
            "score": score,
            "targets": {"tp1": tp1, "tp2": tp2, "sl": sl}
        })

    # Top 3 nach absolutem Score
    top3 = sorted(results, key=lambda x: abs(x["score"]), reverse=True)[:3]

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"📊 Signal Snapshot — {now_utc}\n"
        f"Basis: USD • TF: 15m/1h • Quellen: Binance (Indikatoren), CoinGecko (Preis/24h)\n"
    )

    top_block = "🔥 Top 3 Signale:\n" + "\n".join([f"  {i+1}) {r['symbol']} — {r['label']} (Score {r['score']:.1f})" for i, r in enumerate(top3)]) + "\n"

    body = "\n\n".join([r["line"] for r in results])

    legend = (
        "\nLegende: 🟡 Hold • ℹ️ Info ⚠️ • 🟢/🔴 Signal • BRK=Breakout 1h\n"
        "Levels auf Basis 1h-ATR (orientativ, kein Finanzrat)🙏"
    )

    message = f"{header}\n{top_block}\n{body}{legend}"
    return message, state

# ----------------------------------- Main -------------------------------------

def main():
    msg, state = build_message()
    with open("message.txt", "w", encoding="utf-8") as f:
        f.write(msg.strip() + "\n")
    with open("signal_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print("message.txt + signal_state.json geschrieben ✔")

if __name__ == "__main__":
    main()
