#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Alex Signal Bot – generate_message.py (Profi-Version, 2025-08)
- Fallback-Provider: binance.us -> binance.com -> bybit
- Robust gegen 451 / Rate-Limits
- State/Cooldown, CSV-Logging, getrennte Alerts/Message
- Kompatibel zu bestehendem Repo-Layout
"""

import os
import json
import math
import time
import csv
import sys
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Any, Optional

import requests

# ----------------------------
# Pfade / Defaults
# ----------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
MSG_PATH = os.path.join(ROOT, "message.txt")
ALERTS_PATH = os.path.join(ROOT, "alerts.txt")
STATE_PATH = os.path.join(ROOT, "signal_state.json")
COINS_PATH = os.path.join(ROOT, "coins.json")
CSV_PATH = os.path.join(ROOT, "signal_log.csv")

# Cooldown aus Workflow (Minuten)
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "0"))

# HTTP
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AlexSignalBot/1.0; +https://github.com)"
}
HTTP_TIMEOUT = 15
RETRY_DELAY = (0.6, 1.2, 2.0)  # progressive kurze Delays

# Benötigte Kerzen pro Intervall (für Metriken)
NEEDED_KLINES = {
    "1m": 120,
    "3m": 160,
    "5m": 300,
    "15m": 300,
}

# ----------------------------
# Utilities
# ----------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def ts_to_dt(ms: int) -> datetime:
    # Binance/Bybit liefern ms
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

def dt_to_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_state() -> Dict[str, Any]:
    return read_json(STATE_PATH, default={})

def append_csv_row(ts: datetime, symbol: str, prim_sig: str, extra: Dict[str, Any]) -> None:
    """
    Leichtgewichtiges CSV-Log (optional). Erzeugt Datei bei Bedarf.
    """
    row = {
        "timestamp": dt_to_str(ts),
        "symbol": symbol,
        "primary_signal": prim_sig,
        **extra
    }
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if not exists:
            w.writeheader()
        w.writerow(row)

# ----------------------------
# Markt-API – Fallback-Fetch
# ----------------------------

# Bybit Intervalmap (für 1m/3m/5m/15m ausreichend)
_BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15"}

def _http_get(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    last_exc: Optional[Exception] = None
    for i, delay in enumerate(([0.0] + list(RETRY_DELAY))):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=HTTP_TIMEOUT)
            return r
        except Exception as e:
            last_exc = e
    if last_exc:
        raise last_exc
    raise RuntimeError("HTTP GET failed without exception")

def _binance_klines(base: str, pair: str, interval: str, limit: int) -> List[List[Any]]:
    # base in {"us","com"}
    host = "api.binance.us" if base == "us" else "api.binance.com"
    url = f"https://{host}/api/v3/klines"
    params = {"symbol": pair, "interval": interval, "limit": limit}
    r = _http_get(url, params)
    # 451 -> jurist. Blockade / Geo
    if r.status_code == 451:
        raise requests.HTTPError("451 Unavailable For Legal Reasons", response=r)
    r.raise_for_status()
    return r.json()  # Liste von [open_time, open, high, low, close, volume, ... ]

def _bybit_klines(pair: str, interval: str, limit: int) -> List[List[Any]]:
    # Bybit v5 public kline
    # pair z.B. BTCUSDT -> symbol: BTCUSDT, category: spot
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "spot",
        "symbol": pair,
        "interval": _BYBIT_INTERVAL.get(interval, "1"),
        "limit": str(limit),
    }
    r = _http_get(url, params)
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit error: {data.get('retMsg')}")
    # Bybit liefert: list of [start, open, high, low, close, volume, turnover]
    out = []
    for c in data.get("result", {}).get("list", []):
        # c[0] = start (ms)
        out.append([int(c[0]), c[1], c[2], c[3], c[4], c[5]])
    out.reverse()  # bybit returns newest-first
    return out

def fetch_klines(pair: str, interval: str, limit: int) -> List[Tuple[datetime, float, float, float, float]]:
    """
    Holt OHLC-Kerzen. Reihenfolge: binance.us -> binance.com -> bybit (Fallback).
    Gibt Liste [(open_time_dt, open, high, low, close)] zurück.
    """
    last_error = None
    providers = (("us", True), ("com", True), ("bybit", False))
    for base, is_binance in providers:
        try:
            if is_binance:
                raw = _binance_klines(base, pair, interval, limit)
                out = []
                for c in raw:
                    # Validierung: 6 Felder vorhanden
                    if len(c) < 6:
                        continue
                    ot = ts_to_dt(int(c[0]))
                    o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                    if any(math.isnan(x) for x in (o, h, l, cl)):
                        continue
                    out.append((ot, o, h, l, cl))
                if len(out) >= max(NEEDED_KLINES.get(interval, 120), 50):
                    return out
                else:
                    raise RuntimeError(f"Too few klines from binance.{base}: {len(out)}")
            else:
                raw = _bybit_klines(pair, interval, limit)
                out = []
                for c in raw:
                    if len(c) < 6:
                        continue
                    ot = ts_to_dt(int(c[0]))
                    o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                    if any(math.isnan(x) for x in (o, h, l, cl)):
                        continue
                    out.append((ot, o, h, l, cl))
                if len(out) >= max(NEEDED_KLINES.get(interval, 120), 50):
                    return out
                else:
                    raise RuntimeError(f"Too few klines from bybit: {len(out)}")
        except Exception as e:
            last_error = e
            # bei 451 (nur binance) einfach nächsten Provider versuchen
            # andere HTTP-Fehler ebenfalls weiter zum Fallback
            continue
    raise RuntimeError(f"All providers failed for {pair} {interval}: {last_error}")

# ----------------------------
# Analyse / Metriken
# ----------------------------

def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0

def ema(values: List[float], length: int) -> List[float]:
    if not values or length <= 1:
        return values or []
    k = 2.0 / (length + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def atr(hlc: List[Tuple[float, float, float]], length: int = 14) -> float:
    # hlc: [(high, low, close_prev), ...] -> TR gemittelt
    if len(hlc) < length + 1:
        return 0.0
    trs = []
    for i in range(1, len(hlc)):
        h, l, cp = hlc[i][0], hlc[i][1], hlc[i - 1][2]
        tr = max(h - l, abs(h - cp), abs(l - cp))
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs[-length:]) / float(length)

def analyze_coin(
    symbol: str,
    pair: str,
    thresholds: Dict[str, Any],
    intervals: Tuple[str, str] = ("5m", "15m"),
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    """
    Liefert:
      - summary_line (Text für message.txt)
      - raw_alerts (dict) – Flags/Details für Alerts
      - metrics (dict) – berechnete Metriken
    """
    # 1) Klines laden
    k1 = fetch_klines(pair, intervals[0], NEEDED_KLINES[intervals[0]])
    k2 = fetch_klines(pair, intervals[1], NEEDED_KLINES[intervals[1]])

    def closes(kl: List[Tuple[datetime, float, float, float, float]]) -> List[float]:
        return [c[4] for c in kl]

    c1 = closes(k1)
    c2 = closes(k2)

    # 2) Prozentveränderungen
    ch_5m = pct(c1[-1], c1[-6]) if len(c1) >= 6 else 0.0
    ch_15m = pct(c2[-1], c2[-2]) if len(c2) >= 2 else 0.0
    ch_24h = 0.0  # optional: könnte via 24h-ticker ergänzt werden

    # 3) Volatilität / ATR grob (auf 5m)
    hlc = [(h, l, c1[i - 1] if i > 0 else c1[0]) for i, (_, _, h, l, _) in enumerate(k1)]
    atr_val = atr(hlc, 14)
    ema_fast = ema(c1, 9)
    ema_slow = ema(c1, 21)
    trend_up = len(ema_fast) > 0 and len(ema_slow) > 0 and ema_fast[-1] > ema_slow[-1]

    # 4) Schwellen anwenden
    th = thresholds or {}
    move_th = th.get("move", {})
    info_th = th.get("info", {})
    alert_th = th.get("alert", {})

    # Defaults (konservativ)
    def_th_move_5 = 0.8
    def_th_move_15 = 1.2
    def_th_info_5 = 1.2
    def_th_info_15 = 2.0
    def_th_alert_5 = 2.0
    def_th_alert_15 = 3.0

    sig = "HOLD"
    emoji = "🟡"
    level = "hold"

    # Alerts zuerst prüfen (höchste Priorität)
    if abs(ch_5m) >= float(alert_th.get("5m", def_th_alert_5)) or abs(ch_15m) >= float(alert_th.get("15m", def_th_alert_15)):
        sig = "ALERT"
        emoji = "🚀" if (ch_5m > 0 or ch_15m > 0) else "🔻"
        level = "alert"
    # Dann Info
    elif abs(ch_5m) >= float(info_th.get("5m", def_th_info_5)) or abs(ch_15m) >= float(info_th.get("15m", def_th_info_15)):
        sig = "INFO"
        emoji = "ℹ️"
        level = "info"
    # Dann Signal (leichter)
    elif abs(ch_5m) >= float(move_th.get("5m", def_th_move_5)) or abs(ch_15m) >= float(move_th.get("15m", def_th_move_15)):
        sig = "SIGNAL"
        emoji = "📈" if (ch_5m > 0 or ch_15m > 0) else "📉"
        level = "signal"

    # 5) Formatierung einer Zeile
    price = c1[-1]
    summary = f"{emoji} {symbol}: ${price:,.2f} • 15m {ch_15m:+.2f}% • 24h {ch_24h:+.2f}% — {sig}"

    raw_alerts = {
        "symbol": symbol,
        "level": level,
        "sig": sig,
        "ch_5m": round(ch_5m, 2),
        "ch_15m": round(ch_15m, 2),
        "trend_up": trend_up,
    }
    metrics = {
        "price": price,
        "atr": atr_val,
        "ema9": ema_fast[-1] if ema_fast else None,
        "ema21": ema_slow[-1] if ema_slow else None,
    }
    return summary, raw_alerts, metrics

# ----------------------------
# Building messages
# ----------------------------

def load_coins() -> List[Dict[str, Any]]:
    data = read_json(COINS_PATH, [])
    if not isinstance(data, list):
        raise ValueError("coins.json muss eine Liste sein")
    out = []
    for c in data:
        sym = c.get("symbol")
        pair = c.get("binance") or c.get("pair")  # backward-compat
        th = c.get("thresholds", {})
        if not sym or not pair:
            continue
        out.append({"symbol": sym, "pair": pair, "thresholds": th})
    if not out:
        raise ValueError("coins.json ist leer oder ungültig")
    return out

def cooldown_ok(state: Dict[str, Any], symbol: str, now: datetime) -> bool:
    if COOLDOWN_MINUTES <= 0:
        return True
    sym_state = state.get("coins", {}).get(symbol, {})
    last_ts = sym_state.get("last_ts")
    if not last_ts:
        return True
    try:
        last_dt = datetime.fromisoformat(last_ts)
    except Exception:
        return True
    return (now - last_dt) >= timedelta(minutes=COOLDOWN_MINUTES)

def mark_sent(state: Dict[str, Any], symbol: str, now: datetime, level: str) -> None:
    state.setdefault("coins", {}).setdefault(symbol, {})
    state["coins"][symbol]["last_ts"] = now.isoformat()
    state["coins"][symbol]["last_level"] = level

def build_messages() -> None:
    now = utc_now()
    coins = load_coins()
    state = load_state()

    lines: List[str] = []
    alert_lines_all: List[str] = []
    legend = "Legende: 🟡 Hold • ℹ️ Info • 📈/📉 Signal • 🚀/🔻 Alert"

    header = f"📊 Signal Snapshot — {now.strftime('%Y-%m-%d %H:%M')} UTC\nBasis: USD • Intervall: 15 Min • Quelle: Binance/Bybit"
    lines.append(header)
    lines.append("")

    for c in coins:
        sym, pair, th = c["symbol"], c["pair"], c.get("thresholds", {})
        try:
            if not cooldown_ok(state, sym, now):
                # Im Cooldown: nur HOLD-Zeile (optional markierbar)
                lines.append(f"🟡 {sym}: — (Cooldown aktiv) — HOLD")
                continue

            summary, raw_alerts, metrics = analyze_coin(sym, pair, th, intervals=("5m", "15m"))

            # Alerts sammeln
            lvl = raw_alerts["level"]
            if lvl in ("signal", "info", "alert"):
                alert_lines_all.append(summary)

            # CSV (minimal)
            try:
                append_csv_row(
                    ts=now,
                    symbol=sym,
                    prim_sig=raw_alerts["sig"],
                    extra={"ch5m": raw_alerts["ch_5m"], "ch15m": raw_alerts["ch_15m"], "trend_up": raw_alerts["trend_up"]},
                )
            except Exception:
                pass

            # State aktualisieren
            if lvl in ("signal", "info", "alert"):
                mark_sent(state, sym, now, lvl)

            # In Liste aufnehmen
            lines.append(summary)

        except Exception as e:
            # Fehler je Coin nicht alles abbrechen lassen
            lines.append(f"🟡 {sym}: (Datenfehler) — HOLD")
            # optional: print ins Protokoll
            print(f"[{sym}] ERROR: {e}", file=sys.stderr)

    # Übersicht/Legende
    if not alert_lines_all:
        lines.append("")
        lines.append("🟡 Keine nennenswerte Bewegung über den Info-Schwellen.")
        lines.append("")
        lines.append(legend)

    # Schreiben
    write_text(MSG_PATH, "\n".join(lines))

    alerts_text = ""
    if alert_lines_all:
        alerts_text = "🚨 Alerts\n" + "\n".join(alert_lines_all)
    write_text(ALERTS_PATH, alerts_text)

    save_state(state)

# ----------------------------
# Main
# ----------------------------

def main():
    try:
        build_messages()
        print("message.txt + alerts.txt erstellt.")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
