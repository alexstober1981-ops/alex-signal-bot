#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Alex Signal Bot – generate_message.py
-------------------------------------
Baut die Telegram-Nachricht und pflegt signal_state.json.

Robust:
- akzeptiert state["coins"] als Dict ODER Liste und normalisiert.
- liest coins.json als ["BTC","ETH",...] ODER mit Objekten.
- COOLDOWN_MINUTES via Env steuerbar (Standard: 15).
"""

import os
import json
import time
import math
from datetime import datetime, timezone
from pathlib import Path

import requests


# --------------------------- Konfiguration ---------------------------

BASE = "USD"
INTERVAL_MIN = int(os.getenv("COOLDOWN_MINUTES", "15"))

# Mapping Symbol -> CoinGecko-ID (ggf. erweitern)
SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "TON": "the-open-network",
    "TRX": "tron",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LTC": "litecoin",
}

STATE_FILE = Path("signal_state.json")
COINS_FILE = Path("coins.json")
MESSAGE_FILE = Path("message.txt")

COINGECKO_SIMPLE = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids={ids}&vs_currencies={vs}&include_24hr_change=true"
)

# Schwellen (liberal anpassbar)
ALERT_M15 = 1.0       # % in ~15 Minuten
ALERT_24H = 5.0       # % in 24h
SIGNAL_M15 = 0.3
SIGNAL_24H = 2.0
INFO_M15 = 0.1
INFO_24H = 0.5


# --------------------------- Hilfsfunktionen ---------------------------

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fmt_money(val):
    # $110,915.00 mit Komma-Tausendern
    return "${:,.2f}".format(val)


def fmt_pct(val):
    # +0.74%
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def minutes_since(ts):
    return (time.time() - ts) / 60.0


def normalize_state_coins(state):
    """
    state["coins"] kann Dict ODER Liste sein -> in Dict mit Symbol-Key transformieren.
    """
    coins = state.get("coins", {})
    if isinstance(coins, list):
        new_dict = {}
        for item in coins:
            if isinstance(item, dict):
                sym = item.get("symbol")
                if sym:
                    new_dict[sym] = item
        state["coins"] = new_dict
    elif not isinstance(coins, dict):
        state["coins"] = {}
    return state


def read_coins():
    """
    coins.json kann ["BTC","ETH"] ODER [{"symbol":"BTC","id":"bitcoin"}, ...] enthalten.
    Liefert Liste von (SYMBOL, COINGECKO_ID).
    """
    raw = load_json(COINS_FILE, default=[])
    symbols = []

    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, str):
                sym = it.upper()
                coingecko_id = SYMBOL_TO_ID.get(sym)
                if coingecko_id:
                    symbols.append((sym, coingecko_id))
            elif isinstance(it, dict):
                sym = str(it.get("symbol", "")).upper()
                cid = it.get("id") or SYMBOL_TO_ID.get(sym)
                if sym and cid:
                    symbols.append((sym, cid))
    else:
        # falls jemand ein Dict liefert, versuch's schlau zu raten
        for sym, cid in raw.items():
            if sym and cid:
                symbols.append((sym.upper(), str(cid)))

    # Duplikate raus
    seen = set()
    uniq = []
    for sym, cid in symbols:
        if sym not in seen:
            uniq.append((sym, cid))
            seen.add(sym)

    return uniq


def fetch_prices(ids):
    url = COINGECKO_SIMPLE.format(ids=",".join(ids), vs=BASE.lower())
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    # Beispiel: {"bitcoin":{"usd":110915.0,"usd_24h_change":0.7421}, ...}
    return data


def classify(m15, d24):
    """
    Liefert (emoji, label)
    """
    abs15 = abs(m15)
    abs24 = abs(d24)

    if abs15 >= ALERT_M15 or abs24 >= ALERT_24H:
        return ("🚀" if (m15 > 0 or d24 > 0) else "🔻", "Alert")
    if abs15 >= SIGNAL_M15 or abs24 >= SIGNAL_24H:
        return ("📈" if (m15 > 0 or d24 > 0) else "📉", "Signal")
    if abs15 >= INFO_M15 or abs24 >= INFO_24H:
        return ("ℹ️", "Info")
    return ("🟡", "HOLD")


# --------------------------- Hauptlogik ---------------------------

def build_message():
    state = load_json(STATE_FILE, default={"coins": {}, "last_run": None})
    state = normalize_state_coins(state)
    state.setdefault("last_run", None)

    pairs = read_coins()
    if not pairs:
        raise RuntimeError("coins.json enthält keine gültigen Einträge.")

    # Preise holen
    ids = [cid for _, cid in pairs]
    prices = fetch_prices(ids)

    lines = []
    now_iso = utc_now_iso()

    header = [
        "📊 Signal Snapshot — " + now_iso.split("+")[0] + " UTC",
        f"Basis: {BASE} • Intervall: {INTERVAL_MIN} Min • Quelle: CoinGecko",
        ""
    ]
    lines.extend(header)

    had_movement = False

    for sym, cid in pairs:
        pdata = prices.get(cid, {})
        price = float(pdata.get(BASE.lower(), 0.0))
        ch24 = float(pdata.get(f"{BASE.lower()}_24h_change", 0.0))  # %

        coin_state = state["coins"].get(sym, {})
        last_price = float(coin_state.get("last_price", 0.0))
        last_ts = float(coin_state.get("last_ts", 0.0))

        # 15m Δ: aus letztem gespeicherten Preis
        if last_price > 0:
            m15 = (price - last_price) / last_price * 100.0
        else:
            m15 = 0.0

        # "Frische" des 15m-Werts grob einordnen
        age_min = minutes_since(last_ts) if last_ts > 0 else None
        # wenn älter als 60 min, markiere 15m als ~0.00
        if age_min is not None and age_min > 60:
            m15 = 0.0

        emoji, label = classify(m15, ch24)
        if label in ("Alert", "Signal", "Info"):
            had_movement = True

        line = f"{emoji} {sym}: {fmt_money(price)} • 15m {fmt_pct(m15)} • 24h {fmt_pct(ch24)} — {label}"
        lines.append(line)

        # state aktualisieren
        state["coins"][sym] = {
            "symbol": sym,
            "last_price": price,
            "last_ts": time.time(),
        }

    lines.append("")
    if not had_movement:
        lines.append("🟡 Keine nennenswerte Bewegung über den Info-Schwellen.")

    lines.append("")
    lines.append("Legende: 🟡 Hold • ℹ️ Info • 📈/📉 Signal • 🚀/🔻 Alert")

    message = "\n".join(lines)

    # speichern
    with MESSAGE_FILE.open("w", encoding="utf-8") as f:
        f.write(message)

    state["last_run"] = now_iso
    save_json(STATE_FILE, state)

    return message, state


def main():
    try:
        msg, _ = build_message()
        print("✅ Nachricht erzeugt:")
        print(msg)
    except Exception as e:
        # Für GitHub Actions gut lesbar ausgeben und Exit 1
        print(f"ERROR: {e}")
        raise


if __name__ == "__main__":
    main()
