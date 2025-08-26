# generate_message.py
# Holt Preise von CoinGecko, berechnet 15-Minuten-Änderung ggü. letztem Run,
# bewertet mit Profi-Schwellen, schreibt message.txt + signal_state.json.

import os, json, time, datetime as dt
from typing import Dict, List
import requests

STATE_FILE = "signal_state.json"
MSG_FILE = "message.txt"

# -------- Config aus ENV (Fallbacks) ----------
COINGECKO_IDS = os.getenv("COINGECKO_IDS", "bitcoin,ethereum,solana")
SYMBOLS       = os.getenv("SYMBOLS", "BTC,ETH,SOL")

THRESH_INFO   = float(os.getenv("THRESH_INFO", "0.8"))   # „Heads-up“
THRESH_SIGNAL = float(os.getenv("THRESH_SIGNAL", "1.5"))  # „Trade-Signal“
THRESH_ALERT  = float(os.getenv("THRESH_ALERT", "3.0"))   # „Alarm/Spike“

ID_LIST  = [s.strip() for s in COINGECKO_IDS.split(",") if s.strip()]
SYM_LIST = [s.strip().upper() for s in SYMBOLS.split(",") if s.strip()]
if len(ID_LIST) != len(SYM_LIST):
    raise SystemExit("COINGECKO_IDS und SYMBOLS müssen gleiche Länge haben.")

def load_state() -> Dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state: Dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def fetch_prices(ids: List[str]) -> Dict[str, Dict]:
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=" + ",".join(ids) +
        "&vs_currencies=usd&include_24hr_change=true"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def classify(pct_15m: float) -> str:
    # Prozent absolut
    a = abs(pct_15m)
    if a >= THRESH_ALERT:
        return "ALERT"
    if a >= THRESH_SIGNAL:
        return "SIGNAL"
    if a >= THRESH_INFO:
        return "INFO"
    return "HOLD"

def glyph_for(classification: str, sign: float) -> str:
    up = sign >= 0
    if classification == "ALERT":
        return "🚀" if up else "🔻"
    if classification == "SIGNAL":
        return "📈" if up else "📉"
    if classification == "INFO":
        return "ℹ️"
    return "🟡"

def main():
    now = int(time.time())
    ts = dt.datetime.utcfromtimestamp(now).strftime("%Y-%m-%d %H:%M UTC")

    state = load_state()
    last = state.get("last", {})
    last_ts = state.get("last_ts", None)

    data = fetch_prices(ID_LIST)

    lines = []
    header = f"📊 Signal Snapshot — {ts}\n"
    sub = "Basis: USD • Intervall: 15 Min • Quelle: CoinGecko"
    lines.append(header)
    lines.append(sub)
    lines.append("")

    new_last = {}
    had_signal = False

    for i, cid in enumerate(ID_LIST):
        sym = SYM_LIST[i]
        entry = data.get(cid, {})
        price = float(entry.get("usd", 0.0))
        change24 = float(entry.get("usd_24h_change", 0.0))

        # 15-Min Differenz vs. last price
        prev_price = None
        if sym in last:
            prev_price = last[sym].get("price")

        pct_15m = 0.0
        if prev_price and prev_price > 0:
            pct_15m = (price - prev_price) / prev_price * 100.0

        klass = classify(pct_15m)
        had_signal = had_signal or (klass in ("INFO", "SIGNAL", "ALERT"))

        icon = glyph_for(klass, pct_15m)
        ch24 = f"{change24:+.2f}%"
        p15 = f"{pct_15m:+.2f}%"
        line = f"{icon} {sym}: ${price:,.2f} • 15m {p15} • 24h {ch24} — {klass}"
        lines.append(line)

        new_last[sym] = {"price": price}

    if not had_signal:
        lines.append("")
        lines.append("🟡 Keine nennenswerte Bewegung über den Info-Schwellen.")

    # Legende
    lines.append("")
    lines.append("Legende: 🟡 Hold • ℹ️ Info • 📈/📉 Signal • 🚀/🔻 Alert")

    msg = "\n".join(lines)
    with open(MSG_FILE, "w", encoding="utf-8") as f:
        f.write(msg)

    state_out = {"last": new_last, "last_ts": now}
    save_state(state_out)

    print("message.txt & signal_state.json aktualisiert.")

if __name__ == "__main__":
    main()
