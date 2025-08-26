# bot_poll.py
# Kurzer Poll-Handler für Telegram-Kommandos (/status, /next, /help)
# Läuft einmal, beantwortet neue Nachrichten und beendet sich.
# Nutzt deinen bestehenden Code aus generate_message.py

import os
import json
import time
import requests
from datetime import datetime, timezone

from generate_message import build_message  # nutzt deine Signallogik

API_BASE = "https://api.telegram.org/bot{token}"
LAST_ID_FILE = "last_update_id.txt"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_CHAT_ID_ENV = os.environ.get("TELEGRAM_CHAT_ID")  # optionaler Filter (nur deinem Chat antworten)

if not BOT_TOKEN:
    raise SystemExit("Fehlt: TELEGRAM_BOT_TOKEN (Secret in GitHub anlegen).")

def tg_get(path, **params):
    url = API_BASE.format(token=BOT_TOKEN) + "/" + path
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def tg_post(path, **data):
    url = API_BASE.format(token=BOT_TOKEN) + "/" + path
    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()
    return r.json()

def load_last_id():
    try:
        with open(LAST_ID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None

def save_last_id(update_id: int):
    try:
        with open(LAST_ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(update_id))
    except Exception:
        pass

def minutes_to_next_quarter(now: datetime) -> int:
    # nächste :00, :15, :30, :45
    m = now.minute
    return (15 - (m % 15)) % 15 or 15

def handle_command(chat_id: int, text: str):
    t = (text or "").strip().lower()
    if t.startswith("/status"):
        msg = build_message()
        tg_post("sendMessage", chat_id=chat_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)
        return

    if t.startswith("/next"):
        now = datetime.now(timezone.utc)
        mins = minutes_to_next_quarter(now)
        tg_post("sendMessage", chat_id=chat_id,
                text=f"⏱ Nächster automatischer Lauf in ~{mins} Min.",
                disable_web_page_preview=True)
        return

    if t.startswith("/help") or t.startswith("/start"):
        help_text = (
            "🤖 Befehle:\n"
            "/status – sofort aktuelles Signal-Snapshot\n"
            "/next – wann der nächste Lauf kommt\n"
            "/help – diese Hilfe\n"
        )
        tg_post("sendMessage", chat_id=chat_id, text=help_text, disable_web_page_preview=True)
        return

def main():
    last_id = load_last_id()
    params = {"timeout": 1}
    if last_id is not None:
        params["offset"] = last_id + 1

    data = tg_get("getUpdates", **params)
    if not data.get("ok"):
        return

    max_update_id = last_id or 0
    owner_id = int(OWNER_CHAT_ID_ENV) if OWNER_CHAT_ID_ENV else None

    for upd in data.get("result", []):
        upd_id = upd.get("update_id", 0)
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            max_update_id = max(max_update_id, upd_id)
            continue

        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = msg.get("text", "")

        # Falls OWNER_CHAT_ID gesetzt ist, nur auf deinen Chat reagieren:
        if owner_id and chat_id != owner_id:
            max_update_id = max(max_update_id, upd_id)
            continue

        # Kommando bearbeiten
        handle_command(chat_id, text)
        max_update_id = max(max_update_id, upd_id)

    if max_update_id:
        save_last_id(max_update_id)

if __name__ == "__main__":
    main()
