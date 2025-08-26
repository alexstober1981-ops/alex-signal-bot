# generate_message.py
# Holt Daten von CoinGecko, berechnet einfache Indikatoren (EMA50/EMA200, RSI14),
# baut die Nachricht und schreibt sie nur dann in out_message.txt,
# wenn sich mindestens EIN Signal gegenüber signal_state.json geändert hat.

import json, math, sys, time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# === Coins: Ticker -> (coingecko-id, Anzeigename) ===
COINS = {
    "BTC": ("bitcoin", "Bitcoin (BTC)"),
    "ETH": ("ethereum", "Ethereum (ETH)"),
    "SOL": ("solana", "Solana (SOL)"),
    "ADA": ("cardano", "Cardano (ADA)"),
    "DOT": ("polkadot", "Polkadot (DOT)"),
    "KAS": ("kaspa", "Kaspa (KAS)"),
    "RNDR": ("render-token", "Render (RNDR)"),
    "SUI": ("sui", "Sui (SUI)"),
    "FET": ("fetch-ai", "Fetch.ai (FET)"),
    "AVAX": ("avalanche-2", "Avalanche (AVAX)"),
    "HBAR": ("hedera-hashgraph", "Hedera (HBAR)"),
    "XRP": ("ripple", "XRP"),
    "SEI": ("sei-network", "Sei (SEI)"),
}

STATE_FILE = "signal_state.json"
OUT_FILE = "out_message.txt"

# ---------- kleine Helfer ----------

def http_get_json(url: str):
    req = Request(url, headers={"User-Agent": "signal-bot/1.0"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def ema(values, period):
    k = 2 / (period + 1.0)
    ema_val = None
    for v in values:
        ema_val = v if ema_val is None else (v - ema_val) * k + ema_val
    return ema_val if ema_val is not None else float("nan")

def rsi(values, period=14):
    # klassisches Wilder-RSI auf Schlusskursen (daily)
    if len(values) < period + 1:
        return float("nan")
    gains, losses = [], []
    for i in range(1, len(values)):
        ch = values[i] - values[i-1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    # Start mit Simple Moving Average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder Glättung
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def euro(n):
    s = f"€{n:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

# ---------- Daten holen ----------

def fetch_spot(ids):
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={','.join(ids)}&vs_currencies=eur&include_24hr_change=true"
    )
    return http_get_json(url)

def fetch_history(cg_id):
    # tägliche Daten (Schlusskurse) – 400 Tage reichen für EMA200
    url = (
        "https://api.coingecko.com/api/v3/coins/"
        f"{cg_id}/market_chart?vs_currency=eur&days=400&interval=daily"
    )
    js = http_get_json(url)
    closes = [p[1] for p in js.get("prices", [])]
    return closes

# ---------- Regel-Engine ----------

def make_signal(price, pct24h, closes):
    # Robustheit: wenn keine Historie ➜ nur Change-Regel
    trend_up = False
    mom_rsi = float("nan")
    if closes and len(closes) >= 50:
        ema50 = ema(closes[-250:], 50)   # begrenze Länge, beschleunigt
        ema200 = ema(closes[-400:], 200)
        trend_up = (ema50 is not None and ema200 is not None and ema50 > ema200)
        mom_rsi = rsi(closes[-260:], 14)

    # Heuristik:
    # - Uptrend + RSI>60 + 24h≥+1%  -> Kaufen
    # - RSI<40 + 24h≤-1%            -> Abwarten
    # - sonst                        -> Halten
    if trend_up and (not math.isnan(mom_rsi)) and mom_rsi >= 60 and pct24h >= 1.0:
        return "🟢", "Kaufen"
    if (not math.isnan(mom_rsi)) and mom_rsi <= 40 and pct24h <= -1.0:
        return "🔴", "Abwarten"
    return "🟡", "Halten"

# ---------- Hauptlogik ----------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(d):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def build_now():
    ids = [v[0] for v in COINS.values()]
    spot = fetch_spot(ids)

    # Historien pro Coin (einmalig je Coin)
    history_cache = {}
    signals = {}
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"📊 <b>Signal Snapshot — {ts}</b>")

    for ticker, (cg_id, nice) in COINS.items():
        if cg_id not in spot:
            lines.append(f"⚠️ {nice}: keine Daten")
            continue
        price = float(spot[cg_id].get("eur") or 0.0)
        chg = float(spot[cg_id].get("eur_24h_change") or 0.0)

        # Historie (einmal pro Lauf pro Coin)
        if cg_id not in history_cache:
            try:
                history_cache[cg_id] = fetch_history(cg_id)
            except Exception:
                history_cache[cg_id] = []

        emoji, action = make_signal(price, chg, history_cache[cg_id])
        signals[ticker] = action

        # hübsche Zeile
        price_s = euro(price)
        chg_s = f"{chg:+.2f}%".replace(".", ",")
        lines.append(f"{emoji} {nice}: <b>{action}</b> ({price_s}, 24h: {chg_s})")

    lines.append("\nℹ️ Regeln: Uptrend(EMA50>EMA200)+RSI≥60 & 24h≥+1% → Kaufen · RSI≤40 & 24h≤−1% → Abwarten · sonst Halten.")
    return "\n".join(lines), signals

def main():
    try:
        message, new_state = build_now()
    except (HTTPError, URLError) as e:
        # Bei API-Problemen nicht abbrechen: schreibe Hinweis
        hint = f"⚠️ CoinGecko nicht erreichbar ({e})."
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(hint)
        print("WARN: api error -> send hint")
        return

    old_state = load_state()
    changed = (old_state != new_state)

    if changed:
        # Nachricht zum Versand schreiben
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(message)
        # neuen Zustand speichern (wird später vom Workflow committed)
        save_state(new_state)
        print("changed: yes")
    else:
        # keine Versanddatei erzeugen -> Workflow sendet dann nichts
        print("changed: no")

if __name__ == "__main__":
    main()
