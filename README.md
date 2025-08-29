# 🤖 Alex Signal Bot  

Automatischer Krypto-Signalbot, der robuste Marktdaten aus mehreren Quellen bezieht und dir die Ergebnisse direkt via **Telegram** schickt.  
Entwickelt für **Krypto-Profis**, die Stabilität, Zuverlässigkeit und klare Signale erwarten.  

---

## 🚀 Features

- ⏱️ Automatische Signalsendung zu festen Zeiten (05:00, 10:00, 14:30, 18:00, 22:00 Berlin-Zeit)  
- 📊 Unterstützung für BTC, ETH, SOL, HBAR, XRP, SEI, KAS, RNDR, FET, SUI, AVAX, ADA, DOT  
- 🛡️ Fallback-System:  
  1. Binance.US →  
  2. Bybit (Spot) →  
  3. OKX (Symbol-Mapping BTCUSDT → BTC-USDT)  
- 📩 Ergebnisse direkt an Telegram (Text + Alerts)  
- 📝 Logging: `message.txt`, `alerts.txt`, `signal_state.json`  
- 🔒 API Keys sicher via **GitHub Secrets** (niemals im Code)  
- 📈 Saubere Schwellenwerte via `coins.json` konfigurierbar  

---

## 🔐 Sicherheit

- API Keys niemals im Code, nur via **GitHub Secrets**  
- Keine Speicherung privater Daten  
- Stabilität durch **3-fach Datenquelle**  

---

## ⚙️ Setup

1. Repository klonen oder erstellen.  
2. Zwei GitHub Secrets anlegen:  
   - `TELEGRAM_TOKEN` = dein BotFather-Token  
   - `TELEGRAM_CHAT_ID` = deine Telegram Chat-ID  
3. Workflow läuft automatisch zu den eingestellten Zeiten.  

---

## 🕒 Zeiten (Berlin)

- 05:00  
- 10:00  
- 14:30  
- 18:00  
- 22:00  

---

## 📂 Dateien

- `telegram_send.py` → sendet Nachricht an Telegram  
- `generate_message.py` → baut die Signals + Fallback (Binance → Bybit → OKX)  
- `.github/workflows/telegram_signals.yml` → GitHub Action für Zeitsteuerung  
- `.github/workflows/status_now.yml` → Sofort-Signal auf Knopfdruck  
- `coins.json` → deine Coin-Liste + Schwellenwerte  

---

## 📌 Beispiel-Signal
