# Minimaler Generator für deine Signale.
# Ersetze die Logik in build_message() später durch echte Auswertung (z. B. aus Google Sheets / API).
from datetime import datetime

COINS = ["Bitcoin (BTC)", "Ethereum (ETH)", "Solana (SOL)", "Cardano (ADA)",
         "Polkadot (DOT)", "Kaspa (KAS)", "Render (RNDR)", "Sui (SUI)",
         "Fetch.ai (FET)", "Avalanche (AVAX)", "Hedera (HBAR)", "XRP", "Sei (SEI)"]

EXAMPLE_SIGNALS = {
    "Bitcoin (BTC)": "Halten",
    "Ethereum (ETH)": "Kaufen",
    "Solana (SOL)": "Beobachten",
    "Cardano (ADA)": "Abwarten",
    "Polkadot (DOT)": "Halten",
    "Kaspa (KAS)": "Kaufen",
    "Render (RNDR)": "Halten",
    "Sui (SUI)": "Beobachten",
    "Fetch.ai (FET)": "Halten",
    "Avalanche (AVAX)": "Abwarten",
    "Hedera (HBAR)": "Beobachten",
    "XRP": "Halten",
    "Sei (SEI)": "Beobachten",
}

def build_message():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📈 <b>Signal Snapshot</b> — {now}", ""]

    for c in COINS:
        sig = EXAMPLE_SIGNALS.get(c, "Beobachten")
        icon = {"Kaufen":"🟢","Halten":"🟡","Beobachten":"🟡","Abwarten":"🔴"}.get(sig, "🟡")
        lines.append(f"{icon} <b>{c}</b>: {sig}")
    lines.append("")
    lines.append("ℹ️ Hinweis: Platzhalter-Logik. Passe build_message() an, um echte Daten zu nutzen.")
    return "\n".join(lines)

if __name__ == "__main__":
    print(build_message())
