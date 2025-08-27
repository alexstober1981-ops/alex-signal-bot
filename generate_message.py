#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pro Signal Generator mit:
- Coins aus coins.json (enabled=true)
- Binance 15m/1h: RSI, MACD, Breakout(1h), ATR(1h)
- CoinGecko: Preis & 24h-Change
- Scoring -> 🟢 BUY / 🔴 SELL / 🟡 HOLD / ℹ️ INFO
- TP/SL per ATR
- COOLDOWN pro Coin (ENV: COOLDOWN_MINUTES, default 60)
- Output: message.txt (HTML) + signal_state.json
"""

import json, os
from datetime import datetime, timezone, timedelta
import requests

HEADERS = {"User-Agent": "alex-signal-bot/3.0"}
TIMEOUT = 12

BINANCE = "https://api.binance.com/api/v3/klines"
CG_PRICE = "https://api.coingecko.com/api/v3/simple/price"

RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
ATR_PERIOD = 14
BRK_LOOKBACK = 20   # 1h-Breakout-Periode

STATE_FILE = "signal_state.json"
MSG_FILE   = "message.txt"
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MINUTES", "60"))

# ---------- Math ----------

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
    if n < period + 1: return None
    gains, losses = [], []
    for i in range(1, period+1):
        d = values[i] - values[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains)/period, sum(losses)/period
    for i in range(period+1, n):
        d = values[i] - values[i-1]
        g, l = max(d,0.0), max(-d,0.0)
        ag = (ag*(period-1) + g)/period
        al = (al*(period-1) + l)/period
    if al == 0: return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))

def macd_series(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal: return None, None, None
    ef, es = ema(values, fast), ema(values, slow)
    if len(ef) > len(es): ef = ef[-len(es):]
    if len(es) > len(ef): es = es[-len(ef):]
    macd_line = [a - b for a,b in zip(ef, es)]
    sig_line  = ema(macd_line, signal)
    macd_line = macd_line[-len(sig_line):]
    hist      = [m - s for m,s in zip(macd_line, sig_line)]
    return macd_line, sig_line, hist

def atr(high, low, close, period=14):
    n = len(close)
    if n < period + 1: return None
    tr = []
    for i in range(n):
        if i == 0: tr.append(high[i]-low[i])
        else:
            tr.append(max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])))
    atr_vals = ema(tr, period)
    return atr_vals[-1] if atr_vals else None

def fmt_price(x):
    if x is None: return "—"
    if x >= 1000: return f"{x:,.2f}".replace(",", " ")
    if x >= 1:    return f"{x:.2f}"
    return f"{x:.4f}"

def fmt_pct(x):
    if x is None: return "—"
    s = "+" if x >= 0 else ""
    return f"{s}{x:.2f}%"

# ---------- IO ----------

def load_coins():
    # coins.json ist führend
    if os.path.exists("coins.json"):
        with open("coins.json", "r", encoding="utf-8") as f:
            items = json.load(f)
        return [c for c in items if c.get("enabled", True)]
    # Fallback (sollte eig. nicht mehr nötig sein)
    return []

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ---------- Fetch ----------

def get_klines(symbol, interval, limit=300):
    p = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(BINANCE, params=p, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        raw = r.json()
        return {
            "open":  [float(k[1]) for k in raw],
            "high":  [float(k[2]) for k in raw],
            "low":   [float(k[3]) for k in raw],
            "close": [float(k[4]) for k in raw],
        }
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

# ---------- Logic ----------

def breakout_flags(h, l, c, lookback=20):
    if not c or len(c) < lookback + 2:
        return False, False
    last = c[-1]
    prev_high = max(h[-(lookback+1):-1])
    prev_low  = min(l[-(lookback+1):-1])
    return last > prev_high * 1.001, last < prev_low * 0.999

def classify_and_score(rsi15, rsi1h, macd_last, macd_sig_last, macd_hist_last, macd_hist_prev, brk_up, brk_dn, ch24):
    score, notes = 0.0, []

    # RSI
    if rsi1h is not None:
        if rsi1h < 30: score += 2; notes.append("RSI1h<30")
        elif rsi1h < 35: score += 1; notes.append("RSI1h<35")
        elif rsi1h > 75: score -= 2; notes.append("RSI1h>75")
        elif rsi1h > 70: score -= 1; notes.append("RSI1h>70")
    if rsi15 is not None:
        if rsi15 < 35: score += 0.5
        elif rsi15 > 70: score -= 0.5

    # MACD
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
    if brk_up: score += 1.5; notes.append("Breakout↑")
    if brk_dn: score -= 1.5; notes.append("Breakout↓")

    # 24h Change
    if ch24 is not None:
        if ch24 <= -6.0: score += 1; notes.append("Dip")
        elif ch24 >= 6.0: score -= 1; notes.append("ExtUp")

    # Label
    if score >= 2.0:   label, emoji = "🟢 BUY", "🚀"
    elif score <= -2.0: label, emoji = "🔴 SELL", "📉"
    elif abs(score) <= 1.0: label, emoji = "🟡 HOLD", "⏸️"
    else:              label, emoji = "ℹ️ INFO", "⚠️"
    return label, emoji, score, ", ".join(notes)

def in_cooldown(coin_state, now_utc):
    until = coin_state.get("cooldown_until")
    if not until: return False
    try:
        dt_until = datetime.fromisoformat(until.replace("Z","")).replace(tzinfo=timezone.utc)
        return now_utc < dt_until
    except Exception:
        return False

def set_cooldown(coin_state, now_utc, minutes):
    dt_until = now_utc + timedelta(minutes=minutes)
    coin_state["cooldown_until"] = dt_until.isoformat()

# ---------- Build Message (HTML) ----------

def build_message():
    # Coins
    coins = load_coins()
    if not coins:
        raise SystemExit("coins.json fehlt oder leer.")

    # Preise
    cg_ids = [c["coingecko_id"] for c in coins]
    prices = get_prices(cg_ids)

    # State
    state = load_state()
    now_utc = datetime.now(timezone.utc)
    out_state = {"generated_at": now_utc.isoformat(), "coins": {}}

    rows = []
    top_candidates = []

    for c in coins:
        sym = c["symbol"]; cg_id = c["coingecko_id"]; bin_sym = c["binance"]

        k15 = get_klines(bin_sym, "15m", 300)
        k1h = get_klines(bin_sym, "1h", 300)

        rsi15 = rsi(k15["close"], RSI_PERIOD) if k15 else None
        rsi1h = rsi(k1h["close"], RSI_PERIOD) if k1h else None

        macd_line = macd_sig = macd_hist = hist_prev = None
        if k1h:
            ml, sl, h = macd_series(k1h["close"], MACD_FAST, MACD_SLOW, MACD_SIG)
            if ml and sl and h:
                macd_line, macd_sig, macd_hist = ml[-1], sl[-1], h[-1]
                if len(h) >= 2: hist_prev = h[-2]

        brk_up = brk_dn = False
        level_atr = None
        if k1h:
            brk_up, brk_dn = breakout_flags(k1h["high"], k1h["low"], k1h["close"], BRK_LOOKBACK)
            level_atr = atr(k1h["high"], k1h["low"], k1h["close"], ATR_PERIOD)

        price = ch24 = None
        if cg_id in prices:
            price = float(prices[cg_id].get("usd", 0.0))
            ch24  = float(prices[cg_id].get("usd_24h_change", 0.0))

        label, emoji, score, notes = classify_and_score(
            rsi15, rsi1h, macd_line, macd_sig, macd_hist, hist_prev, brk_up, brk_dn, ch24
        )

        # Cooldown-Logik
        coin_state = state.get("coins", {}).get(sym, {})
        cooling = in_cooldown(coin_state, now_utc)
        cooldown_flag = False
        if (label.startswith("🟢") or label.startswith("🔴")) and cooling:
            # Degradiere zu INFO, Hinweis Cooldown
            label = "ℹ️ INFO"
            emoji = "⏳"
            notes = (notes + ", " if notes else "") + "Cooldown aktiv"
            cooldown_flag = True

        # TP/SL nur bei BUY/SELL (und wenn ATR da ist)
        tp1 = tp2 = sl = None
        if price and level_atr and (label.startswith("🟢") or label.startswith("🔴")):
            if label.startswith("🟢"):
                tp1 = price + 1.0 * level_atr
                tp2 = price + 2.0 * level_atr
                sl  = price - 1.5 * level_atr
            else:
                tp1 = price - 1.0 * level_atr
                tp2 = price - 2.0 * level_atr
                sl  = price + 1.5 * level_atr

        # MACD Richtung + Breakout-Zeichen
        macd_dir = ("🟢 Long" if (macd_line is not None and macd_sig is not None and macd_line >= macd_sig)
                    else ("🔴 Short" if (macd_line is not None and macd_sig is not None) else "—"))
        brk_txt = "↑" if brk_up else ("↓" if brk_dn else "—")

        # HTML-Zeile
        line = (
            f"<b>{sym}</b> {emoji} — <b>${fmt_price(price)}</b> "
            f"(24h {fmt_pct(ch24)}), RSI15 <code>{'-' if rsi15 is None else round(rsi15)}</code>, "
            f"RSI1h <code>{'-' if rsi1h is None else round(rsi1h)}</code>, MACD {macd_dir}, BRK {brk_txt}<br>"
            f"➡️ <b>{label}</b>" + (f" <i>({notes})</i>" if notes else "")
        )
        if tp1 and tp2 and sl:
            line += f"<br>🎯 TP1 <code>{fmt_price(tp1)}</code> • TP2 <code>{fmt_price(tp2)}</code> • 🛡️ SL <code>{fmt_price(sl)}</code>"

        rows.append(line)

        # Top-Kandidaten (absoluter Score)
        top_candidates.append((sym, label, score))

        # State aktualisieren
        out_state["coins"].setdefault(sym, {})
        out_state["coins"][sym].update({
            "price": price,
            "change_24h": ch24,
            "rsi_15m": rsi15, "rsi_1h": rsi1h,
            "macd": {"line": macd_line, "signal": macd_sig, "hist": macd_hist, "hist_prev": hist_prev},
            "breakout_up": brk_up, "breakout_down": brk_dn,
            "atr_1h": level_atr,
            "last_signal": label,
            "last_updated": now_utc.isoformat()
        })
        # Cooldown neu setzen, wenn echtes BUY/SELL
        if (label.startswith("🟢") or label.startswith("🔴")) and not cooldown_flag:
            set_cooldown(out_state["coins"][sym], now_utc, COOLDOWN_MIN)

    # Top 3
    top3 = sorted(top_candidates, key=lambda x: abs(x[2]), reverse=True)[:3]
    top_block = "🔥 <b>Top 3</b>: " + ", ".join([f"{s} {l.split()[0]} (|Score| {abs(sc):.1f})" for s,l,sc in top3])

    # Header + Legend
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (f"📊 <b>Signal Snapshot — {ts}</b><br>"
              f"Basis: USD • TF: 15m/1h • Quellen: Binance (Indikatoren), CoinGecko (Preis/24h)<br><br>")
    legend = ("<br>Legende: 🟡 Hold • ℹ️ Info ⚠️ • 🟢/🔴 Signal • BRK=Breakout 1h • "
              f"Cooldown: {COOLDOWN_MIN} Min<br>"
              "<i>Hinweis: Kein Finanzrat.</i>")

    html = header + top_block + "<br><br>" + "<br><br>".join(rows) + legend
    return html, out_state

# ---------- Main ----------

def main():
    msg, state = build_message()
    with open(MSG_FILE, "w", encoding="utf-8") as f:
        f.write(msg)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print("message.txt + signal_state.json geschrieben ✔")

if __name__ == "__main__":
    main()
