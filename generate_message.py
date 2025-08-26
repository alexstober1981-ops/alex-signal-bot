# generate_message.py
# Holt Live-Preise von CoinGecko, entscheidet Signal (Kaufen/Halten/Abwarten)
# Sendet nur bei Signal-Änderung oder wenn Preis um bestimmte % verändert.

import json, os, requests
from datetime import datetime, timezone

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

# Sende-Schwellen: BTC 1%, ETH 1.5%, große Coins 2%, kleinere 3%
THRESHOLDS = {
    "BTC": 0.010,
    "ETH": 0.015,
    "SOL": 0.020, "ADA": 0.020, "AVAX": 0.020, "XRP": 0.020,
    "DOT": 0.030, "RNDR": 0.030, "KAS": 0.030,
    "SUI": 0.030, "FET": 0.030, "HBAR": 0.030, "SEI": 0.030,
}

STATE_FILE = "signal_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, "r", encoding="utf-8"))
        except: return {}
    return {}

def save_state(state):
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), indent=2)

def get_action(change):
    if change >= 3.0: return "🟢", "Kaufen"
    if change <= -3.0: return "🔴", "Abwarten"
    return "🟡", "Halten"

def fetch(ids):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(ids)}&vs_currencies=eur&include_24hr_change=true"
    return requests.get(url, timeout=25).json()

def build_message_and_state():
    prev = load_state()
    data = fetch([v[0] for v in COINS.values()])
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines, should_send, next_state = [f"📊 <b>Signals {ts}</b>"], False, {}

    for t, (cg, name) in COINS.items():
        if cg not in data: continue
        price, chg = data[cg]["eur"], data[cg]["eur_24h_change"]
        emo, act = get_action(chg)

        last, moved = prev.get(t, {}), False
        if "price" in last:
            rel = abs(price - last["price"]) / last["price"]
            moved = rel >= THRESHOLDS.get(t, 0.03)
        else: moved = True

        if act != last.get("action") or moved: should_send = True
        lines.append(f"{emo} {name}: <b>{act}</b> (€{price:,.2f}, 24h {chg:+.2f}%)".replace(",", "X").replace(".", ",").replace("X", "."))

        next_state[t] = {"price": price, "action": act}

    lines.append("\nℹ️ Sendet nur bei Signal-Wechsel oder wenn Preis BTC 1%, ETH 1.5%, große 2%, kleine 3% bewegt.")
    return "\n".join(lines), should_send, next_state
