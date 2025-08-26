# telegram_send.py
# Sendet Text/Nachricht an Telegram (Bot API).

import os
import sys
import requests

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage" if TOKEN else None

def send_message(text: str) -> dict:
    if not TOKEN or not CHAT_ID:
        raise SystemExit("Fehlende Umgebungsvariablen: TELEGRAM_TOKEN/TELEGRAM_CHAT_ID")
    resp = requests.post(
        API_URL,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()

if __name__ == "__main__":
    # Nutzung:
    # 1) python telegram_send.py "Hallo"
    # 2) python telegram_send.py path/zur/datei.txt  (liest Inhalt)
    if len(sys.argv) >= 2:
        arg = sys.argv[1]
        if os.path.isfile(arg):
            with open(arg, "r", encoding="utf-8") as f:
                msg = f.read().strip()
        else:
            msg = " ".join(sys.argv[1:])
    else:
        msg = "Test vom Telegram-Bot ✅"

    r = send_message(msg)
    print("Sent:", r.get("ok", False))
