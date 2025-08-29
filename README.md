# 🤖 Alex Signal Bot

Automatischer Krypto-Signalbot, der robuste Marktdaten aus mehreren Quellen bezieht und dir die Ergebnisse direkt via **Telegram** schickt.  
Entwickelt für **Krypto-Profis**, die Stabilität, Zuverlässigkeit und klare Signale erwarten.

---

## 🚀 Features

- ⏱️ Automatische Signalsendung zu festen Zeiten (05:00, 10:00, 14:30, 18:00, 22:00 – Berlin)
- 📊 Unterstützt u. a. BTC, ETH, SOL, HBAR, XRP, SEI, KAS, RNDR, FET, SUI, AVAX, ADA, DOT
- 🛡️ Fallback-System für Marktdaten (unkaputtbar):
  1. **Binance.US**
  2. **Bybit (Spot)**
  3. **OKX** (Symbol-Mapping `BTCUSDT → BTC-USDT`)
- 📩 Ergebnisse direkt an Telegram (Text + Alerts)
- 📝 Logging: `message.txt`, `alerts.txt`, `signal_state.json`
- 🔒 API-Keys sicher via **GitHub Secrets** (niemals im Code)
- ⚙️ Schwellenwerte & Coins sauber in `coins.json` konfigurierbar

---

## 🔐 Sicherheit

- API-Keys niemals im Code, nur via **GitHub Secrets**
- Keine Speicherung privater Personendaten
- Stabilität durch **3-fach Datenquelle** (Binance → Bybit → OKX)

---

## ⚡ Quickstart (≤ 5 Minuten)

> Ziel: Secrets setzen → Workflow starten → Signal in Telegram bekommen.

### 1) Telegram-Bot & Chat-ID
1. In Telegram **@BotFather** öffnen → `/newbot` → Namen & Nutzernamen vergeben.  
   → Du erhältst den **Bot-Token** (Format: `123456789:AA...`).
2. Deinen Bot anschreiben (z. B. „Hi“) – so entsteht der Chat.
3. **Chat-ID ermitteln** (nur kurz zum Nachsehen – **Token niemals veröffentlichen**):
   - Browser öffnen:  
     `https://api.telegram.org/bot<DEIN_BOT_TOKEN>/getUpdates`
   - In der Antwort steht `chat":{"id": <DEINE_CHAT_ID>}`.
   - **Wichtig:** URL nie ins Repo kopieren! Nur lokal verwenden.

### 2) GitHub Secrets anlegen
Im Repo: **Settings → Secrets and variables → Actions → New repository secret**
- `TELEGRAM_TOKEN` → dein BotFather-Token
- `TELEGRAM_CHAT_ID` → deine Chat-ID (Zahl; bei Gruppen ggf. negativ)

> **Achtung:** Name muss **exakt** so heißen wie hier!

### 3) Workflows nutzen
- Automatische Zeiten sind bereits eingerichtet (Berlin): **05:00, 10:00, 14:30, 18:00, 22:00**.
- Für einen Sofort-Test: **Actions → „Status now“ → Run workflow**.

### 4) Coins & Schwellen (optional)
Datei `coins.json` anpassen. Beispiel-Eintrag:
```json
[
  { "symbol": "BTC", "binance": "BTCUSDT" },
  { "symbol": "ETH", "binance": "ETHUSDT" }
]
