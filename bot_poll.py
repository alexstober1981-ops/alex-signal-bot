#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
bot_poll.py
Einfaches Telegram-Polling für Commands, robust gegen 409/Webhook-Konflikte.
Speichert den Offset in last_update_id.txt, sodass Runs idempotent sind.

Erwartete Umgebungsvariablen (GitHub Actions -> Secrets):
  - TELEGRAM_TOKEN
  - TELEGRAM_CHAT_ID
"""

import os
import json
import time
import traceback
from pathlib import Path

import requests

# ----------- Konfiguration -----------
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID_ENV = os.getenv("TELEGRAM_CHAT_ID", "").strip()  # optional – wir filtern nur, wenn gesetzt
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
LAST_ID_PATH = Path("last_update_id.txt")

# Polling-Parameter
LONG_POLL_TIMEOUT = 25  # Sekunden für getUpdates Timeout
STARTUP_DELETE_WEBHOOK = True  # beim Start vorsorglich Webhook löschen
REQUEST_HEADERS = {
    "User-Agent": "AlexSignalBot/1.0 (+https://github.com/)"
}
# -------------------------------------


def require_env():
    missing = []
    if not TOKEN:
        missing.append("TELEGRAM_TOKEN")
    # CHAT_ID ist optional (wenn leer, verarbeiten wir alle Chats/DMs)
    if missing:
        print("❌ Fehlende Umgebungsvariablen:", ", ".join(missing))
        raise SystemExit(1)


def tg_delete_webhook():
    """Löscht vorhandenen Webhook, ignoriert Fehler vollständig."""
    try:
        r = requests.get(f"{BASE_URL}/deleteWebhook", headers=REQUEST_HEADERS, timeout=15)
        # kein raise_for_status -> wir wollen niemals crashen
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        print(f"[INFO] deleteWebhook status={r.status_code} body={j!r}")
    except Exception as e:
        print(f"[WARN] deleteWebhook Exception: {e}")


def tg_send(chat_id, text):
    """Sendet eine Textnachricht, Fehler werden geloggt aber nicht geworfen."""
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(f"{BASE_URL}/sendMessage", headers=REQUEST_HEADERS, data=payload, timeout=20)
        if r.status_code >= 400:
            print(f"[WARN] sendMessage {r.status_code}: {r.text[:500]}")
    except Exception as e:
        print(f"[WARN] sendMessage Exception: {e}")


def load_last_update_id():
    try:
        if LAST_ID_PATH.exists():
            return int(LAST_ID_PATH.read_text().strip())
    except Exception as e:
        print(f"[WARN] Konnte last_update_id nicht lesen: {e}")
    return None


def save_last_update_id(update_id):
    try:
        LAST_ID_PATH.write_text(str(update_id))
        print(f"[INFO] last_update_id gespeichert: {update_id}")
    except Exception as e:
        print(f"[WARN] Konnte last_update_id nicht schreiben: {e}")


def tg_get_updates(offset=None, timeout=LONG_POLL_TIMEOUT):
    """
    Holt Updates via Long Polling.
    - Bei 409 (Webhook aktiv) -> Webhook löschen und leere Liste zurückgeben (kein Crash).
    - Bei anderen Fehlern -> loggen und leere Liste zurückgeben.
    """
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{BASE_URL}/getUpdates", headers=REQUEST_HEADERS, params=params, timeout=timeout + 5)
        if r.status_code == 409:
            print("[WARN] 409 Conflict bei getUpdates – lösche Webhook & retry später…")
            tg_delete_webhook()
            return []
        r.raise_for_status()
        data = r.json()
        if not data.get("ok", False):
            print(f"[WARN] getUpdates ok=false body={data}")
            return []
        return data.get("result", [])
    except Exception as e:
        print(f"[ERROR] getUpdates Exception: {e}")
        return []


def format_id_info(msg):
    chat = msg.get("chat", {})
    parts = [
        f"<b>Chat-ID:</b> {chat.get('id')}",
        f"<b>Typ:</b> {chat.get('type')}",
    ]
    title = chat.get("title")
    if title:
        parts.append(f"<b>Title:</b> {title}")
    username = chat.get("username")
    if username:
        parts.append(f"<b>Username:</b> @{username}")
    return "\n".join(parts)


def handle_command(msg):
    """
    Einfache Command-Handler:
      /start  /help  /ping  /id
    Weitere kannst du leicht ergänzen.
    """
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not text or not chat_id:
        return

    # Wenn CHAT_ID_ENV gesetzt ist, nur diesen Chat erlauben
    if CHAT_ID_ENV:
        try:
            wanted = int(CHAT_ID_ENV)
            if int(chat_id) != wanted:
                print(f"[INFO] Ignoriere fremden Chat {chat_id} (erlaubt: {wanted})")
                return
        except Exception:
            pass

    lower = text.lower()

    if lower.startswith("/start"):
        tg_send(chat_id, "👋 <b>Willkommen!</b>\nDieser Bot verarbeitet deine Commands im Polling-Modus.")
    elif lower.startswith("/help"):
        tg_send(chat_id, "ℹ️ <b>Kommandos</b>\n/start – Begrüßung\n/ping – Liveness\n/id – Chat/Benutzer Info")
    elif lower.startswith("/ping"):
        tg_send(chat_id, "🏓 pong")
    elif lower.startswith("/id"):
        tg_send(chat_id, format_id_info(msg))
    else:
        # schweigend ignorieren oder kurz antworten:
        print(f"[INFO] Unbekanntes Kommando/Text ignoriert: {text!r} von {chat_id}")


def main_once():
    print("[INFO] Starte Polling (ein Durchlauf)…")
    if STARTUP_DELETE_WEBHOOK:
        tg_delete_webhook()

    offset = load_last_update_id()
    if offset is not None:
        # Telegram erwartet "nächster" Offset, nicht der zuletzt verarbeitete.
        # Viele speichern already+1; hier stellen wir sicher, dass wir nicht doppelt senden.
        print(f"[INFO] Verwende gespeicherten offset={offset}")

    updates = tg_get_updates(offset=offset)

    max_update_id = None
    for upd in updates:
        try:
            upd_id = upd.get("update_id")
            if max_update_id is None or (upd_id is not None and upd_id > max_update_id):
                max_update_id = upd_id

            msg = upd.get("message") or upd.get("edited_message") or {}
            if msg:
                handle_command(msg)
        except Exception:
            print("[WARN] Fehler beim Verarbeiten eines Updates:\n" + traceback.format_exc())

    # Offset fortschreiben
    if max_update_id is not None:
        # next offset: letzter + 1
        save_last_update_id(max_update_id + 1)

    print("[INFO] Polling-Durchlauf beendet.")


if __name__ == "__main__":
    require_env()
    main_once()
