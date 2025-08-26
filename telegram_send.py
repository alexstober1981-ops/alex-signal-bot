# telegram_send.py
# Sendet message.txt via Telegram Bot API.

import os, sys, json, pathlib, urllib.parse
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT  = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MSG_PATH = pathlib.Path("message.txt")

def fail(msg: str):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)

def main():
    if not TOKEN:
        fail("TELEGRAM_BOT_TOKEN fehlt!")
    if not CHAT:
        fail("TELEGRAM_CHAT_ID fehlt!")
    if not MSG_PATH.exists():
        fail("message.txt wurde nicht gefunden – erst generate_message.py ausführen.")

    text = MSG_PATH.read_text(encoding="utf-8")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT,
        "text": text,
        "parse_mode": "HTML",   # (wir senden plain text, HTML ist robust)
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    resp = r.json()
    if not resp.get("ok"):
        fail(f"Telegram Fehler: {json.dumps(resp)}")

    print("✅ Nachricht an Telegram gesendet.")

if __name__ == "__main__":
    main()
