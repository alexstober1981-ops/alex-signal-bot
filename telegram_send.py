# telegram_send.py
# Liest out_message.txt und sendet an deinen Telegram-Chat.

import os, json, urllib.parse, urllib.request, sys

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

API = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

def send(text: str):
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(API, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()

def main():
    path = "out_message.txt"
    if not os.path.exists(path):
        print("no message file -> nothing to send")
        return
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    # Telegram max ~4096 Zeichen pro Nachricht -> ggfs. stückeln
    chunk = 3500
    if len(text) <= chunk:
        send(text)
    else:
        parts = [text[i:i+chunk] for i in range(0, len(text), chunk)]
        for idx, p in enumerate(parts, 1):
            send((f"Teil {idx}/{len(parts)}\n" + p) if len(parts) > 1 else p)

if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        sys.exit("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID fehlen (GitHub Secrets)!")
    main()
