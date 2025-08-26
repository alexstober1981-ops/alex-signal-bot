# telegram_send.py
# Sendet Nachrichten an deinen Telegram-Bot

import os
import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_message(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Telegram BOT_TOKEN oder CHAT_ID fehlt")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    send_message("🚀 Testnachricht von deinem Alex Signal Bot")
