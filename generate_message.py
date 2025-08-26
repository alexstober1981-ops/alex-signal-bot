# generate_message.py
# Holt Live-Daten von CoinGecko und baut die Signal-Nachricht.

import requests
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

def get_signal(pct_change_24h: float):
    if pct_change_24h >= 3.0:  return "🟢", "Kaufen"
    if pct_change_24h <= -3.0: return "🔴", "Abwarten"
    return "🟡", "Halten"

def fetch(ids):
    url = ("https://api.coingecko.com/api/v3/simple/price"
           f"?ids={','.join(ids)}&vs_currencies=eur&include_24hr_change=true")
    r = requests.get(url, timeout=20); r.raise_for_status()
    return r.json()

def build_message():
    ids = [v[0] for v in COINS.values()]
    data = fetch(ids)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📈 <b>Signal Snapshot — {ts}</b>"]

    for _, (cg_id, name) in COINS.items():
        entry = data.get(cg_id, {})
        price = entry.get("eur"); chg = entry.get("eur_24h_change")
        if price is None or chg is None:
            lines.append(f"⚠️ {name}: keine Daten"); continue
        emoji, action = get_signal(chg)
        price_str = f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        chg_str   = f"{chg:+.2f}%".replace(".", ",")
        lines.append(f"{emoji} {name}: <b>{action}</b> (€{price_str}, 24h: {chg_str})")

    lines.append("\nℹ️ Regeln: ≥ +3% = Kaufen, ≤ −3% = Abwarten, sonst Halten.")
    return "\n".join(lines)

if __name__ == "__main__":
    print(build_message())
