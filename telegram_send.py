#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, requests

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_MAIN = os.getenv("TELEGRAM_CHAT_ID")
CHAT_ALERT = os.getenv("TELEGRAM_ALERT_CHAT_ID")  # optional; fällt zurück auf CHAT_MAIN

API = "https://api.telegram.org/bot{t}/sendMessage"

def load(path):
    if not os.path.exists(path): return ""
    with open(path,"r",encoding="utf-8") as f:
        return f.read().strip()

def chunks(text, limit=3800):
    if not text: return []
    if len(text) <= limit: return [text]
    parts, rest = [], text
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut == -1: cut = rest.rfind("\n", 0, limit)
        if cut == -1: cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest: parts.append(rest)
    return parts

def send(text, chat_id):
    url = API.format(t=TOKEN)
    for i, c in enumerate(chunks(text), 1):
        r = requests.post(url, json={"chat_id": chat_id, "text": c, "disable_web_page_preview": True}, timeout=20)
        if r.status_code == 429:
            retry = r.json().get("parameters", {}).get("retry_after", 2)
            time.sleep(float(retry)); continue
        r.raise_for_status()

def main():
    if not TOKEN: print("❌ TELEGRAM_TOKEN fehlt", file=sys.stderr); sys.exit(1)
    if not CHAT_MAIN: print("❌ TELEGRAM_CHAT_ID fehlt", file=sys.stderr); sys.exit(1)

    msg = load("message.txt")
    alerts = load("alerts.txt")

    # Hauptnachricht
    if msg:
        send(msg, CHAT_MAIN)
        print("✅ message.txt gesendet.")
    else:
        print("ℹ️ message.txt leer/fehlt.")

    # Alerts: eigener Kanal falls vorhanden, sonst in den Main-Chat
    if alerts:
        target = CHAT_ALERT or CHAT_MAIN
        send(alerts, target)
        print(f"✅ alerts.txt gesendet → {'ALERT_CHAT' if CHAT_ALERT else 'MAIN_CHAT'}")
    else:
        print("ℹ️ alerts.txt leer/fehlt – keine aktuellen Alerts.")

if __name__ == "__main__":
    main()
