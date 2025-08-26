# telegram_send.py – sendet Signal an Telegram (mit --force Option)

import os, sys, requests
from generate_message import build_message_and_state, save_state

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

def send(msg: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
        timeout=25
    )
    r.raise_for_status()

def main(argv):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID fehlen!", file=sys.stderr)
        return 1

    force = "--force" in argv  # bei „Status Now“-Workflow
    msg, should_send, next_state = build_message_and_state()

    if force or should_send:
        send(msg)
        save_state(next_state)
        print("✅ Nachricht gesendet" + (" (force)" if force else ""))
    else:
        print("ℹ️ Keine Änderung – nichts gesendet")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
