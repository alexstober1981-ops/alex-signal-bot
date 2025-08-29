#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_message.py — Profi-Variante
- Daten-Fallback: BinanceUS -> Bybit (Spot) -> OKX
- SEI/KAS Symbol-Fix + force_exchange aus coins.json
- Kennzahlen: 5m/15m %Change, ATR%(14), RSI(14)
- Entscheidungen: HOLD, BUY, SELL mit Cooldown
- Outputs: message.txt (Snapshot), alerts.txt (nur Signale), signal_state.json (Cooldown)

Abhängigkeiten: requests
"""

import os, json, time, math, csv
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests

# ---------- Pfade ----------
COINS_PATH  = "coins.json"
STATE_PATH  = "signal_state.json"
MSG_PATH    = "message.txt"
ALERTS_PATH = "alerts.txt"
LOG_CSV     = "signals_log.csv"   # optional, wird automatisch angelegt

# ---------- Anzeige ----------
BASE_QUOTE = "USD"
HEADER = "📊 Signal Snapshot"
INTERVALS_INFO = "5m/15m"
SOURCES = "BinanceUS → Bybit → OKX"

HEADERS = {"User-Agent": "Mozilla/5.0 (ProSignalBot)"}
TIMEOUT = 18

# ---------- Default-Regeln (werden pro Coin überschrieben) ----------
DEFAULTS = {
    "buy_rsi":       65.0,
    "sell_rsi":      30.0,
    "min_15m_up":     0.45,    # +0.45% für Long
    "min_15m_down":  -0.45,    # -0.45% für Short
    "min_atr_pct":    0.20,    # Mindest-Volatilität
    "cooldown_min":  30
}

# ---------- SEI/KAS Mapping ----------
PAIR_MAP: Dict[str, Dict[str, Optional[str]]] = {
    "SEI": {"binanceus": None, "bybit": "SEIUSDT", "okx": "SEI-USDT"},
    "KAS": {"binanceus": None, "bybit": "KASUSDT", "okx": "KAS-USDT"},
}

def _default_symbol(coin: str, ex: str) -> Optional[str]:
    c = coin.upper()
    if ex in ("binanceus", "bybit"):
        return f"{c}USDT"
    if ex == "okx":
        return f"{c}-USDT"
    return None

def _resolve_symbols(coin: str, force: Optional[str]) -> List[tuple[str, str]]:
    """liefert priorisierte Liste (exchange, symbol) je Coin"""
    c = coin.upper()
    if force:
        # erzwungene Börse zuerst, dann Rest als Fallback
        order = [force, "binanceus", "bybit", "okx"]
    else:
        order = ["binanceus", "bybit", "okx"]
    custom = PAIR_MAP.get(c, {})
    out = []
    for ex in order:
        if ex in custom:
            sym = custom[ex]
            if sym:
                out.append((ex, sym))
        else:
            sym = _default_symbol(c, ex)
            if sym:
                out.append((ex, sym))
    # duplicates raus, Reihenfolge bewahren
    seen = set()
    uniq = []
    for ex, sym in out:
        key = (ex, sym)
        if key not in seen:
            seen.add(key)
            uniq.append((ex, sym))
    # bei SEI/KAS BinanceUS sicher hinten halten
    if c in ("SEI","KAS"):
        uniq = [p for p in uniq if p[0] != "binanceus"] + [p for p in uniq if p[0] == "binanceus"]
    return uniq

# ---------- Fetcher ----------
def _fetch_binanceus(symbol: str, interval: str, limit: int = 300) -> List[List[float]]:
    url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if r.status_code != 451:
        r.raise_for_status()
    if r.status_code == 451:
        raise requests.HTTPError("451 region-block", response=r)
    rows = r.json()
    # [open_time, open, high, low, close, vol,...]  älteste->neueste
    return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in rows]

def _fetch_bybit(symbol: str, interval: str, limit: int = 300) -> List[List[float]]:
    iv = {"1m":"1", "3m":"3", "5m":"5", "15m":"15", "1h":"60"}.get(interval, "5")
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval={iv}&limit={min(limit,200)}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    js = r.json()
    if js.get("retCode") != 0:
        raise RuntimeError(f"Bybit retCode {js.get('retCode')}: {js.get('retMsg')}")
    rows = list(reversed(js["result"]["list"]))  # neueste->älteste -> drehen
    return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in rows]

def _fetch_okx(symbol: str, interval: str, limit: int = 300) -> List[List[float]]:
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit={limit}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    rows = list(reversed(r.json()["data"]))  # neueste->älteste -> drehen
    return [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in rows]

def fetch_klines(coin: str, interval: str, force: Optional[str]) -> List[List[float]]:
    last_err = None
    for ex, sym in _resolve_symbols(coin, force):
        try:
            if ex == "binanceus": return _fetch_binanceus(sym, interval)
            if ex == "bybit":     return _fetch_bybit(sym, interval)
            if ex == "okx":       return _fetch_okx(sym, interval)
        except Exception as e:
            last_err = e
            time.sleep(0.3)
            continue
    raise RuntimeError(f"Keine Datenquellen für {coin} ({interval}): {last_err}")

# ---------- Indikatoren ----------
def pct(a: float, b: float) -> float:
    if b == 0: return 0.0
    return (a - b) / b * 100.0

def rsi14(closes: List[float]) -> float:
    if len(closes) < 15: return 50.0
    gains = []
    losses = []
    for i in range(1, 15):
        d = closes[-i] - closes[-i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / 14.0
    avg_loss = sum(losses) / 14.0
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr_pct(ohlc: List[List[float]], period: int = 14) -> float:
    if len(ohlc) < period + 1: return 0.0
    trs = []
    prev_close = ohlc[-period-1][4]
    for i in range(-period, 0):
        _, o, h, l, c, _ = ohlc[i]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    atr = sum(trs) / period
    last_close = ohlc[-1][4]
    if last_close <= 0: return 0.0
    return (atr / last_close) * 100.0

# ---------- Regeln ----------
def merge_rules(base: dict, override: dict) -> dict:
    r = base.copy()
    for k in ("buy_rsi","sell_rsi","min_15m_up","min_15m_down","min_atr_pct","cooldown_min","force_exchange"):
        if k in override:
            r[k] = override[k]
    return r

def decide(m: dict, rules: dict) -> str:
    """
    Gibt 'BUY' | 'SELL' | 'HOLD' zurück.
    Bedingungen (Pro-Setup):
      - ATR% >= min_atr_pct
      - 15m >= min_15m_up  + RSI >= buy_rsi  -> BUY
      - 15m <= min_15m_down + RSI <= sell_rsi -> SELL
    """
    if m["atrp"] < rules["min_atr_pct"]:
        return "HOLD"
    if m["chg15"] >= rules["min_15m_up"] and m["rsi"] >= rules["buy_rsi"]:
        return "BUY"
    if m["chg15"] <= rules["min_15m_down"] and m["rsi"] <= rules["sell_rsi"]:
        return "SELL"
    return "HOLD"

# ---------- Analyse ----------
def analyze_symbol(sym: str, cfg: dict, state: dict) -> dict:
    force = cfg.get("force_exchange")
    k5  = fetch_klines(sym, "5m",  force)
    k15 = fetch_klines(sym, "15m", force)

    c5  = [row[4] for row in k5]
    c15 = [row[4] for row in k15]
    price = c5[-1]
    chg5  = pct(c5[-1],  c5[-2])  if len(c5)  >= 2 else 0.0
    chg15 = pct(c15[-1], c15[-2]) if len(c15) >= 2 else 0.0
    rsi   = rsi14(c5)
    atrp  = atr_pct(k5, 14)

    rules = merge_rules(DEFAULTS, cfg)
    action = decide({"chg5": chg5, "chg15": chg15, "rsi": rsi, "atrp": atrp}, rules)

    # Cooldown
    now = int(time.time())
    cd_min = int(rules.get("cooldown_min", DEFAULTS["cooldown_min"]))
    last_ts = state.get(sym, {}).get("last", 0)
    on_cd = (now - last_ts) < cd_min * 60

    alert_line = ""
    if action in ("BUY","SELL") and not on_cd:
        state.setdefault(sym, {})["last"] = now
        arrow = "▲" if action == "BUY" else "▼"
        alert_line = (f"{arrow} {sym}: {action} • "
                      f"15m {chg15:+.2f}% • RSI {int(round(rsi))} • ATR% {atrp:.2f}")

    return {
        "symbol": sym,
        "price": price,
        "chg5": chg5,
        "chg15": chg15,
        "rsi": rsi,
        "atrp": atrp,
        "action": action if not (action in ("BUY","SELL") and on_cd) else f"HOLD (Cooldown)",
        "alert": alert_line
    }

# ---------- IO ----------
def fmt_price(p: float) -> str:
    return f"${p:,.2f}" if p >= 1 else f"${p:.4f}"

def line_snapshot(r: dict) -> str:
    return (f"🟡 {r['symbol']}: {fmt_price(r['price'])} • "
            f"5m {r['chg5']:+.2f}% • 15m {r['chg15']:+.2f}% • "
            f"ATR% {r['atrp']:.2f} • RSI {int(round(r['rsi']))} — {r['action']}")

def append_csv(ts: str, r: dict, tag: str):
    try:
        exists = os.path.exists(LOG_CSV)
        with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["ts","symbol","price","chg5_pct","chg15_pct","rsi","atr_pct","tag"])
            w.writerow([ts, r["symbol"], f"{r['price']:.8f}", f"{r['chg5']:.2f}",
                        f"{r['chg15']:.2f}", f"{r['rsi']:.2f}", f"{r['atrp']:.2f}", tag])
    except Exception:
        pass

# ---------- Main ----------
def main():
    # Coins laden
    raw = []
    try:
        with open(COINS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        pass
    if not raw:
        raw = [{"symbol": "BTC"}, {"symbol":"ETH"}]

    coins: List[dict] = []
    for item in raw:
        if isinstance(item, dict) and "symbol" in item:
            d = item.copy()
            d["symbol"] = d["symbol"].upper().strip()
            coins.append(d)

    # State laden
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}

    # Header
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"{HEADER} — {ts}",
             f"Basis: {BASE_QUOTE} • Intervalle: {INTERVALS_INFO} •",
             f"Quellen: {SOURCES}"]
    alerts = []

    # Analyse
    for c in coins:
        sym = c["symbol"]
        try:
            res = analyze_symbol(sym, c, state)
            lines.append(line_snapshot(res))
            tag = res["action"]
            append_csv(ts, res, tag)
            if res["alert"]:
                alerts.append(res["alert"])
        except Exception as e:
            lines.append(f"🟡 {sym}: Datenfehler — HOLD")
            append_csv(ts, {"symbol":sym,"price":0,"chg5":0,"chg15":0,"rsi":0,"atrp":0}, f"ERR {e}")

    # Schreiben
    with open(MSG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")

    with open(ALERTS_PATH, "w", encoding="utf-8") as f:
        if alerts:
            f.write("📣 Alerts\n" + "\n".join(alerts) + "\n")
        else:
            f.write("📣 Alerts\n—\n")

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print("message.txt + alerts.txt erzeugt.")

if __name__ == "__main__":
    main()
