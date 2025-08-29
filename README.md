# 🤖 Alex Signal Bot

Automatischer **Krypto-Signalbot** mit **Multi-Exchange-Daten, Fallback-Strategie** und robuster **Telegram-Integration**.  
Entwickelt für **Krypto-Trader**, die Stabilität, Transparenz und professionelle Signalqualität erwarten.

---

## 🏆 Features (State of the Art)
- 📊 **Multi-Exchange Fallback**: BinanceUS → Bybit → OKX  
- 📉 **Indikatoren**: 5m/15m Change, EMA, RSI, ATR (erweiterbar)  
- 🧠 **Adaptive Schwellen**: dynamische Anpassung an Marktvolatilität  
- ⏱ **Cooldown pro Coin**: Anti-Spam, Standard 30min  
- 📂 **Outputs**:
  - `message.txt` → Markt-Snapshot  
  - `alerts.txt` → starke Kauf-/Verkaufssignale  
  - `signal_state.json` → interner Zustand / letzte Alerts  
- 🔔 **Telegram-Integration**: Push-Nachrichten in Echtzeit  
- 🛡 **Stabilität & Sicherheit**: GitHub Secrets + redundante Datenquellen  
- 📈 **Optimiert für Trading-Profis** → skalierbar & erweiterbar

---

## 🔐 Sicherheit
- 🔑 **API Keys niemals im Code** – ausschließlich über GitHub Secrets  
- 🛡 **Keine Speicherung sensibler Daten**  
- ♻️ **3-fach Datenquelle** → hohe Ausfallsicherheit (Binance/Bybit/OKX)  
- 📝 **Logging & State** → reproduzierbare Signale & Debugging

---

## ⚙️ Setup
1. Repository klonen oder forken  
2. **GitHub Secrets** einrichten:
   - `TELEGRAM_TOKEN` = BotFather Token  
   - `TELEGRAM_CHAT_ID` = deine Telegram Chat-ID  
3. Workflows:
   - `.github/workflows/telegram_signals.yml` → geplanter Lauf (05:00, 10:00, 14:30, 18:00, 22:00 Berlin-Zeit)  
   - `.github/workflows/status_now.yml` → sofortiger Run (manuell triggerbar)  

---

## 📂 Projektstruktur
