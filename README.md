# 📊 Alex Signal Bot – Pro Crypto Edition 🚀

[![Signals](https://github.com/alexstober1981-ops/alex-signal-bot/actions/workflows/telegram_signals.yml/badge.svg)](https://github.com/alexstober1981-ops/alex-signal-bot/actions/workflows/telegram_signals.yml)
[![Status Now](https://github.com/alexstober1981-ops/alex-signal-bot/actions/workflows/status_now.yml/badge.svg)](https://github.com/alexstober1981-ops/alex-signal-bot/actions/workflows/status_now.yml)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

---

## 🚀 Überblick

Der **Alex Signal Bot** ist ein professioneller Trading-Signal-Bot für **Krypto-Profi-Trader**.  
Er analysiert den Markt **vollautomatisch** und sendet **hochwertige Signale in Echtzeit via Telegram**.  

### ✅ Highlights
- Multi-Exchange Fallback: **Binance → Bybit → OKX** (unkaputtbar, lückenlos)  
- Unterstützte Coins: BTC, ETH, SOL, XRP, KAS, SUI, AVAX, RNDR, FET, ADA, DOT, HBAR, SEI  
- **Volatilitäts-Filter** & individuelle Schwellenwerte (z. B. SOL/KAS strenger)  
- Läuft 24/7 auf **GitHub Actions** – keine extra Hardware nötig  
- Transparente Logs + Telegram Push-Alerts  

---

## ⚙️ Setup

1. Repository klonen oder forken.
2. GitHub Secrets anlegen:
   - `TELEGRAM_TOKEN` → BotFather Token
   - `TELEGRAM_CHAT_ID` → deine Telegram Chat-ID (oder Gruppen-ID)
3. Workflow starten → Signale kommen automatisch nach Zeitplan.

---

## ⏰ Zeitplan (Berlin)

- 05:00  
- 10:00  
- 14:30  
- 18:00  
- 22:00  

Zusätzlich: **alle 15 Minuten Markt-Checks**.  
⚡ **Sofort-Signale** über `status_now.yml` (manuell auslösbar).

---

## 📂 Dateien

- `telegram_send.py` → Sendet Nachrichten an Telegram  
- `generate_message.py` → Baut die Signal-Nachricht (mit Exchange-Fallback)  
- `coins.json` → Liste aller Coins + Schwellenwerte  
- `.github/workflows/telegram_signals.yml` → Automatisierte Runs  
- `.github/workflows/status_now.yml` → Manuelle Sofort-Signale  
- `alerts.txt`, `message.txt` → Logs & Reports  
- `signal_state.json` → verhindert doppelte Signale  

---

## 📊 Beispiel-Signale (Telegram)

```text
📈 BTC/USDT
5m +2.4% | 15m +3.1% | RSI: 72
⚡ Breakout erkannt – starker Trend nach oben

📉 SOL/USDT
5m -1.8% | 15m -2.9% | RSI: 38
⚠️ Vorsicht – Abwärtstrend verstärkt sich
