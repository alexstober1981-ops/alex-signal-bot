# generate_message.py
# Pro-Version: Holt Live-Daten (Preis + Historie) von CoinGecko,
# berechnet EMA50/EMA200 + RSI14 und baut eine saubere Telegram-Nachricht.

from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import requests

# -------- Konfiguration -------------------------------------------------

VS_CCY = "eur"
RSI_LEN = 14
EMA_FAST = 50
EMA_SLOW = 200

COINS: Dict[str, Tuple[str, str]] = {
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

# -------- kleine Utils --------------------------------------------------

def fmt_eur(x: float) -> str:
    # €1.234,56 Format (ohne lokales Modul)
    s = f"{x:,.2f}"
    return "€" + s.replace(",", "X").replace(".", ",").replace("X", ".")

def http_get(url: str, params: dict | None = None, retries: int = 3, timeout: int = 25):
    last_err = None
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    raise last_err  # wenn alle Versuche scheitern

def ema(series: List[float], n: int) -> List[float]:
    if not series or n <= 1:
        return series[:]
    k = 2 / (n + 1)
    out = [series[0]]
    for price in series[1:]:
        out.append(price * k + out[-1] * (1 - k))
    return out

def rsi(series: List[float], length: int = 14) -> List[float]:
    # klassischer Wilder-RSI
    if len(series) < length + 1:
        return [50.0] * len(series)
    gains, losses = [], []
    for i in range(1, len(series)):
        chg = series[i] - series[i-1]
        gains.append(max(chg, 0.0))
        losses.append(abs(min(chg, 0.0)))
    # erste Durchschnittswerte
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length
    rsis = [50.0] * (length)  # auffüllen bis erstes echtes RSI
    # rest
    for i in range(length, len(gains)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        if avg_loss == 0:
            rs = float('inf')
        else:
            rs = avg_gain / avg_loss
        rsis.append(100 - (100 / (1 + rs)))
    # Länge angleichen
    while len(rsis) < len(series):
        rsis.insert(0, 50.0)
    return rsis

# -------- Daten holen ---------------------------------------------------

def fetch_snapshot(ids: List[str]) -> dict:
    """aktuelle Preise + 24h-Änderung für alle Coins in einem Call"""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": ",".join(ids),
        "vs_currencies": VS_CCY,
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
    }
    return http_get(url, params)

def fetch_intraday_prices(cg_id: str) -> List[float]:
    """Minütliche Preise (~24h) für EMA/RSI-Berechnung."""
    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
    params = {"vs_currency": VS_CCY, "days": "1", "interval": "minute"}
    data = http_get(url, params)
    # data["prices"] = [[ts_ms, price], ...]
    return [p[1] for p in data.get("prices", [])]

# -------- Entscheidungslogik --------------------------------------------

def decide(action_inputs: dict) -> Tuple[str, str]:
    """
    Inputs: dict mit keys price, chg24, ema50, ema200, rsi, vol_note
    Rückgabe: (emoji, text)
    Logik:
      - Uptrend = EMA50 > EMA200
      - Downtrend = EMA50 < EMA200
      - RSI: <30 überverkauft, >70 überkauft
    """
    ema50 = action_inputs["ema50"]
    ema200 = action_inputs["ema200"]
    rsi_val = action_inputs["rsi"]
    chg24 = action_inputs["chg24"]

    if ema50 is None or ema200 is None or rsi_val is None:
        return "🟡", "Beobachten"

    uptrend = ema50 > ema200
    downtrend = ema50 < ema200

    # Regeln
    if uptrend:
        if rsi_val < 40:
            return "🟢", "Kaufen (Dip im Aufwärtstrend)"
        if 40 <= rsi_val <= 65 and chg24 >= -1.0:
            return "🟢", "Kaufen/Halten"
        if rsi_val > 70:
            return "🔴", "Abwarten (überkauft)"
        return "🟡", "Halten"
    if downtrend:
        if rsi_val < 30:
            return "🟡", "Beobachten (möglicher Rebound)"
        return "🔴", "Abwarten (Abwärtstrend)"

    # Seitwärts
    if abs(chg24) < 1.0:
        return "🟡", "Seitwärts – Halten"
    return ("🟢", "Kaufen") if chg24 > 0 else ("🔴", "Abwarten")

# -------- Nachricht bauen -----------------------------------------------

def build_message() -> str:
    ids = [v[0] for v in COINS.values()]
    snap = fetch_snapshot(ids)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = [f"📊 <b>Krypto-Signale — {ts}</b>"]
    lines.append("Regeln: EMA50/EMA200 + RSI14 + 24h-Change. (Pro-Version)")

    for ticker, (cg_id, nice) in COINS.items():
        s = snap.get(cg_id, {})
        price = s.get(VS_CCY)
        chg24 = s.get(f"{VS_CCY}_24h_change")

        # Defaults, falls später etwas schiefgeht
        ema50_v = ema200_v = rsi_v = None
        note = ""

        try:
            series = fetch_intraday_prices(cg_id)
            if len(series) >= max(EMA_SLOW + 2, RSI_LEN + 2):
                ema50_v = ema(series, EMA_FAST)[-1]
                ema200_v = ema(series, EMA_SLOW)[-1]
                rsi_v = rsi(series, RSI_LEN)[-1]
            else:
                note = " (zu wenig Intraday-Daten)"
        except Exception:
            note = " (Historie derzeit nicht verfügbar)"

        if price is None or chg24 is None:
            lines.append(f"⚠️ {nice}: keine aktuellen Daten{note}")
            continue

        emoji, action = decide({
            "price": price,
            "chg24": chg24,
            "ema50": ema50_v,
            "ema200": ema200_v,
            "rsi": rsi_v,
        })

        # Zeile rendern
        extras = []
        if ema50_v and ema200_v:
            trend = "↑" if ema50_v > ema200_v else "↓" if ema50_v < ema200_v else "→"
            extras.append(f"Trend: {trend}")
        if rsi_v is not None:
            extras.append(f"RSI: {rsi_v:.0f}")
        extras_txt = " | " + " · ".join(extras) if extras else ""

        lines.append(
            f"{emoji} {nice}: <b>{action}</b> "
            f"({fmt_eur(price)}, 24h: {chg24:+.2f}%){extras_txt}{note}"
        )

    lines.append("\nℹ️ Hinweise: Grün = Kauf-Bias, Gelb = neutral, Rot = abwarten. "
                 "Kein Financial Advice.")
    return "\n".join(lines)

if __name__ == "__main__":
    print(build_message())
