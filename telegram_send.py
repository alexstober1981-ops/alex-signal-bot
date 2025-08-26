# telegram_send.py
# Sendet die in message.txt stehende Nachricht an Telegram.
import os, sys, json, requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    print("❌ TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID fehlen!", file=sys.stderr)
    sys.exit(1)

def send(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    try:
        with open("message.txt", "r", encoding="utf-8") as f:
            msg = f.read()
    except FileNotFoundError:
        print("❌ message.txt nicht gefunden – zuerst generate_message.py ausführen.", file=sys.stderr)
        sys.exit(1)

    resp = send(msg)
    print("✅ Gesendet:", resp.get("ok", True))
