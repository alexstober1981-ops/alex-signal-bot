#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, json, requests

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")  # fallback
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN:
    print("❌ TELEGRAM_TOKEN fehlt!", file=sys.stderr)
    sys.exit(1)
if not CHAT_ID:
    print("❌ TELEGRAM_CHAT_ID fehlt!", file=sys.stderr)
    sys.exit(1)

if not os.path.exists("message.txt"):
    print("❌ message.txt nicht gefunden (generate_message.py nicht gelaufen?).", file=sys.stderr)
    sys.exit(1)

with open("message.txt", "r", encoding="utf-8") as f:
    text = f.read().strip()

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
payload = {
    "chat_id": CHAT_ID,
    "text": text,
    "disable_web_page_preview": True,
    "parse_mode": "HTML"  # plain text safe; HTML tolerates emojis
}
r = requests.post(url, json=payload, timeout=20)
if r.status_code != 200:
    print(f"❌ Telegram API Error {r.status_code}: {r.text}", file=sys.stderr)
    sys.exit(1)

print("✅ Nachricht an Telegram gesendet.")
