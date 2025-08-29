# 🤖 Alex Signal Bot

Automatischer Krypto-Signalbot mit **Fallback-Marktdaten (BinanceUS → Bybit → OKX)**, adaptiven Schwellen (ATR%), Trend-Filtern (EMA 20/50), RSI, Cooldown und Telegram-Benachrichtigung.  
Entwickelt für **Krypto-Profis**, die Stabilität, Zuverlässigkeit und klare Signale erwarten.

---

## 🚀 Features

- 📊 **Multi-Exchange Daten**: BinanceUS → Bybit → OKX (3-fach Fallback, unkaputtbar)  
- 🧮 **Indikatoren**: 5m/15m Change, EMA(20/50), RSI(14), ATR%  
- 🎚 **Adaptive Schwellen**: passen sich Volatilität an (weniger Spam in High-Vol-Phasen)  
- ⏱ **Cooldown pro Coin**: Standard 30min (überschreibbar per `COOLDOWN_MINUTES`)  
- 📝 **Outputs**:  
  - `message.txt` → kompakter Markt-Snapshot  
  - `alerts.txt` → nur starke Signale  
  - `signal_state.json` → interner Zustand (Cooldown, letzter Preis, Status)  
- 🔔 **Telegram Integration**: alle Signale direkt in deinen Chat  
- 🛡 **Stabilität & Sicherheit**: GitHub Secrets für API Keys, kein Klartext im Repo  

---

## 🛡 Sicherheit

- 🔑 **API Keys niemals im Code** – nur via GitHub **Secrets**  
- 🗂 **Keine Speicherung privater Daten**  
- 🧩 **3-fach Datenquelle** = hohe Ausfallsicherheit  

---

## ⚙️ Setup

1. **Repository klonen oder forken**

2. **GitHub Secrets anlegen** (Settings → Secrets and variables → Actions):
   - `TELEGRAM_TOKEN` = BotFather-Token  
   - `TELEGRAM_CHAT_ID` = deine Telegram-ChatID  

3. **Automatische Workflows** (GitHub Actions):
   - `telegram_signals.yml` → schickt Signals 5× täglich  
   - `status_now.yml` → manuell starten für Sofort-Snapshot  
   - `bot_poll.yml` → Polling für Commands in Telegram  

---

## ⏰ Zeitplan (Berlin)

- 05:00  
- 10:00  
- 14:30  
- 18:00  
- 22:00  

---

## 📂 Dateien

- `generate_message.py` → baut Markt-Signaltexte (Fallback + Indikatoren)  
- `telegram_send.py` → sendet Textnachrichten an Telegram  
- `bot_poll.py` → verarbeitet Telegram-Befehle (409-safe)  
- `.github/workflows/*.yml` → Actions für Auto-Runs  

---

## 📈 Beispiel-Output
