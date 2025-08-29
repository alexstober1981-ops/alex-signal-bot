#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_message.py — Pro-Version
- Daten-Fallback: BinanceUS -> Bybit (linear/spot) -> OKX
- Spezial-Handling für Coins wie SEI/KAS (Symbol/Quelle)
- Indikatoren: RSI(14), ATR%, 5m/15m Change
- BUY/SELL-Entscheidungen mit Cooldown & Dedupe
- Ausgaben: message.txt, alerts.txt, signal_state.json, signals_log.csv (append)
"""

import os, json, time, math
from datetime import datetime, timezone
import requests

# ========= Einstellungen =========
PAIR_QUOTE   = "USDT"
HISTORY_MINS = 300            # ca. 5h 1m-Kerzen
COOLDOWN_MIN = 30             # min Abstand pro Richtung/CoIn
MAX_ALERTS   = 6              # Schutz: max Alerts pro Run
HTTP_TIMEOUT = 12
RETRY_MAX    = 2              # einfache Retries bei 429/5xx

# Default-Regeln (pro Coin überschreibbar via coins.json)
DEFAULT_RULES = {
    "min_rsi": 35,
    "buy_rsi_cross_up": 30,
    "sell_rsi_cross_down": 70,
    "min_5m": 0.10,             # BUY braucht >= +0.10% auf 5m
    "min_15m": 0.00,            # 15m darf nicht stark dagegen laufen
    "max_5m_for_sell": -0.10,   # SELL gern <= -0.10% auf 5m
    "atrp_min": 0.05,
    "atrp_max": 3.00
}

# Dateien
BASE_DIR    = "."
MSG_PATH    = os.path.join(BASE_DIR, "message.txt")
ALERTS_PATH = os.path.join(BASE_DIR, "alerts.txt")
STATE_PATH  = os.path.join(BASE_DIR, "signal_state.json")
COINS_PATH  = os.path.join(BASE_DIR, "coins.json")
LOG_CSV     = os.path.join(BASE_DIR, "signals_log.csv")

HEADERS = {"User-Agent": "Mozilla/5.0 (SignalBot/Pro 1.0)"}

# ========= Utils =========
def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def write_text(path, s):
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)

def append_csv_row(ts, sym, side, price, rsi, ch5, ch15, atrp, reason):
    try:
        exists = os.path.exists(LOG_CSV)
        with open(LOG_CSV, "a", encoding="utf-8") as f:
            if not exists:
                f.write("ts,symbol,side,price,rsi,chg5,chg15,atrp,reason\n")
            line = f'{ts},{sym},{side},{price:.8f},{(rsi or 0):.2f},{ch5:.2f},{ch15:.2f},{(atrp or 0):.2f},"{reason}"\n'
            f.write(line)
    except Exception:
        pass

def pct_change(a, b):
    if b == 0: return 0.0
    return (a/b - 1) * 100.0

def fmt_price(x):   return f"${x:,.4f}"
def fmt_pct(x):     return f"{x:+.2f}%"
def fmt_rsi(x):     return f"{x:.0f}" if x is not None else "0"
def fmt_atrp(x):    return f"{x:.2f}" if x is not None else "0.00"

# ========= HTTP mit Retry =========
def http_get(url):
    last = None
    for i in range(RETRY_MAX+1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504):
                last = Exception(f"HTTP {r.status_code}")
                time.sleep(1.2 * (i+1))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(0.8 * (i+1))
    raise last

# ========= Datenquellen =========
def _binanceus_klines(symbol: str, interval: str, limit: int):
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    r = http_get(url)
    if r.status_code == 451:
        raise RuntimeError("451 region blocked")
    data = r.json()
    return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])] for c in data]

def _bybit_klines(symbol: str, interval: str, limit: int, category: str):
    # interval map
    m = {"1m":"1","3m":"3","5m":"5","15m":"15"}
    iv = m.get(interval, "1")
    url = f"https://api.bybit.com/v5/market/kline?category={category}&symbol={symbol}&interval={iv}&limit={limit}"
    r = http_get(url)
    data = r.json().get("result", {}).get("list", [])
    if not data:
        raise RuntimeError("Bybit empty list")
    # Bybit liefert neueste zuerst -> drehen
    kl = []
    for c in reversed(data):
        kl.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return kl

def _okx_symbol(symbol: str):
    # BTCUSDT -> BTC-USDT
    if symbol.endswith("USDT"):
        return symbol[:-4] + "-" + "USDT"
    return symbol

def _okx_klines(symbol: str, interval: str, limit: int):
    m = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m"}
    iv = m.get(interval, "1m")
    inst = _okx_symbol(symbol)
    url = f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar={iv}&limit={limit}"
    r = http_get(url)
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError("OKX empty data")
    kl = []
    for c in reversed(data):
        kl.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    return kl

def fetch_klines_any(symbol: str, interval: str, limit: int, sources: list):
    """
    sources: Liste aus Strings:
      'binanceus', 'bybit_linear', 'bybit_spot', 'okx'
    Wir probieren in dieser Reihenfolge durch.
    """
    last_err = None
    for src in sources:
        try:
            if src == "binanceus":
                return _binanceus_klines(symbol, interval, limit)
            if src == "bybit_linear":
                return _bybit_klines(symbol, interval, limit, category="linear")
            if src == "bybit_spot":
                return _bybit_klines(symbol, interval, limit, category="spot")
            if src == "okx":
                return _okx_klines(symbol, interval, limit)
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Fetch failed for {symbol}: {last_err}")

# ========= Indikatoren =========
def rsi(values, period=14):
    if len(values) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i-1]
        gains.append(max(0, d))
        losses.append(max(0, -d))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def true_range(h, l, c_prev):
    return max(h - l, abs(h - c_prev), abs(l - c_prev))

def atr_percent(kl, period=14):
    if len(kl) <= period: return None
    trs = []
    for i in range(1, len(kl)):
        trs.append(true_range(kl[i][2], kl[i][3], kl[i-1][4]))
    atr = sum(trs[-period:]) / period
    last_close = kl[-1][4]
    if last_close == 0: return None
    return (atr / last_close) * 100.0

# ========= Entscheidungslogik =========
def decide_signal(sym, price, ch5, ch15, rsi14, atrp, prev_rsi, last_side_ts, rules):
    if atrp is None or atrp < rules["atrp_min"] or atrp > rules["atrp_max"]:
        return "HOLD", f"ATR% {fmt_atrp(atrp)} außerhalb Range"

    buy_cross  = prev_rsi is not None and prev_rsi < rules["buy_rsi_cross_up"]  and rsi14 >= rules["buy_rsi_cross_up"]
    sell_cross = prev_rsi is not None and prev_rsi > rules["sell_rsi_cross_down"] and rsi14 <= rules["sell_rsi_cross_down"]

    now = time.time()
    if buy_cross and ch5 >= rules["min_5m"] and ch15 >= rules["min_15m"]:
        if last_side_ts and now - last_side_ts < COOLDOWN_MIN*60:
            return "HOLD", f"Cooldown BUY aktiv ({COOLDOWN_MIN}m)"
        return "BUY",  f"RSI Cross↑ {prev_rsi:.1f}->{rsi14:.1f}, 5m {fmt_pct(ch5)}, 15m {fmt_pct(ch15)}"

    if sell_cross and ch5 <= rules["max_5m_for_sell"]:
        if last_side_ts and now - last_side_ts < COOLDOWN_MIN*60:
            return "HOLD", f"Cooldown SELL aktiv ({COOLDOWN_MIN}m)"
        return "SELL", f"RSI Cross↓ {prev_rsi:.1f}->{rsi14:.1f}, 5m {fmt_pct(ch5)}"

    if rsi14 < DEFAULT_RULES["min_rsi"]:
        return "HOLD", f"RSI {rsi14:.1f} niedrig"
    return "HOLD", "Kein Setup"

# ========= Analyse =========
def load_rules_map_and_sources():
    """
    coins.json kann außer Schwellen auch Quelle bevorzugen:
      { "symbol":"SEI", "source_pref":["bybit_linear","okx","bybit_spot"] }
    Unbekannte Keys werden ignoriert.
    """
    raw = load_json(COINS_PATH, [])
    rules_map = {}
    source_map = {}
    for item in raw:
        sym = item.get("symbol","").upper()
        if not sym: continue
        rules = {k:item[k] for k in item.keys() if k not in ("symbol","source_pref")}
        if rules: rules_map[sym] = rules
        if "source_pref" in item and isinstance(item["source_pref"], list):
            source_map[sym] = item["source_pref"]
    return rules_map, source_map

def default_sources_for(sym):
    # Viele Coins fehlen auf BinanceUS -> direkt Bybit/OKX probieren
    return ["binanceus", "bybit_linear", "bybit_spot", "okx"]

def analyze_symbol(sym: str, rules_map, source_map, state):
    symbol_full = f"{sym}{PAIR_QUOTE}"
    sources = source_map.get(sym, default_sources_for(sym))

    kl1m = fetch_klines_any(symbol_full, "1m", HISTORY_MINS, sources)
    closes = [c[4] for c in kl1m]
    price  = closes[-1]
    chg5   = pct_change(price, closes[-5])  if len(closes) >= 6  else 0.0
    chg15  = pct_change(price, closes[-15]) if len(closes) >= 16 else 0.0
    rsi14  = rsi(closes, 14)
    atrp   = atr_percent(kl1m, 14)

    rules = dict(DEFAULT_RULES)
    if sym in rules_map: rules.update(rules_map[sym])

    st = state.get(sym, {})
    prev_rsi     = st.get("prev_rsi")
    last_side    = st.get("last_side")
    last_side_ts = st.get("last_side_ts")

    side, reason = decide_signal(sym, price, chg5, chg15, rsi14, atrp, prev_rsi, last_side_ts, rules)

    # Dedupe (gleiche Richtung im Cooldown nicht erneut senden)
    if side != "HOLD" and last_side == side and last_side_ts and time.time() - last_side_ts < COOLDOWN_MIN*60:
        side = "HOLD"
        reason += " (dedupe)"

    st["prev_rsi"] = float(rsi14) if rsi14 is not None else None
    if side in ("BUY","SELL"):
        st["last_side"] = side
        st["last_side_ts"] = int(time.time())
    state[sym] = st

    return {
        "price":price, "chg5":chg5, "chg15":chg15, "rsi":rsi14, "atrp":atrp,
        "side":side, "reason":reason
    }

# ========= Main =========
def main():
    coins = load_json(COINS_PATH, [])
    symbols = [c["symbol"].upper() for c in coins] if coins else \
        ["BTC","ETH","SOL","AVAX","RNDR","FET","SUI","ADA","DOT","HBAR","XRP","SEI","KAS"]

    rules_map, source_map = load_rules_map_and_sources()
    state = load_json(STATE_PATH, {})

    lines = []
    alerts = []
    alerts_emitted = 0

    header = f"📊 Signal Snapshot — {utc_now_str()}\n" \
             f"Basis: USD • Intervalle: 5m/15m •\n" \
             f"Quellen: BinanceUS → Bybit → OKX"
    lines.append(header)

    for sym in symbols:
        try:
            m = analyze_symbol(sym, rules_map, source_map, state)
            price, ch5, ch15, rsi14, atrp, side, reason = \
                m["price"], m["chg5"], m["chg15"], m["rsi"], m["atrp"], m["side"], m["reason"]

            bullet = "🟢" if side=="BUY" else ("🔴" if side=="SELL" else "🟡")
            lines.append(
                f"{bullet} {sym}: {fmt_price(price)} • 5m {fmt_pct(ch5)} • 15m {fmt_pct(ch15)} "
                f"• ATR% {fmt_atrp(atrp)} • RSI {fmt_rsi(rsi14)} — {side}"
            )

            if side in ("BUY","SELL") and alerts_emitted < MAX_ALERTS:
                mark = "✅" if side=="BUY" else "⛔"
                alerts.append(f"{mark} {side} {sym} @ {fmt_price(price)} • RSI {rsi14:.1f if rsi14 else 0} "
                              f"• 5m {fmt_pct(ch5)} • ATR% {fmt_atrp(atrp)} — {reason}")
                append_csv_row(utc_now_str(), sym, side, price, rsi14, ch5, ch15, atrp, reason)
                alerts_emitted += 1

        except Exception:
            # Letzter Fallback: sauber im Snapshot ausweisen
            lines.append(f"🟡 {sym}: Datenfehler — HOLD")

    write_text(MSG_PATH, "\n".join(lines))
    write_text(ALERTS_PATH, "—" if not alerts else "\n".join(alerts))
    save_json(STATE_PATH, state)

if __name__ == "__main__":
    main()
