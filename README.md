# Alex Signal Bot

Dieser Bot schickt dir automatisch Signale via Telegram.

## Setup
1. Repository klonen oder erstellen.
2. Zwei **GitHub Secrets** anlegen:
   - `TELEGRAM_TOKEN` = BotFather Token
   - `TELEGRAM_CHAT_ID` = deine Telegram Chat-ID
3. Workflow läuft automatisch zu den eingestellten Zeiten.

## Zeiten (Berlin)
- 05:00
- 10:00
- 14:30
- 18:00
- 22:00

## Dateien
- `telegram_send.py` = sendet Nachricht an Telegram
- `generate_message.py` = baut den Text (aktuell Platzhalter)
- `.github/workflows/telegram_signals.yml` = GitHub Action
