#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import requests
from typing import Optional, Tuple

# --- Konfiguration -----------------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
DEFAULT_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()  # optional
LAST_ID_FILE = "last_update_id"  # wird im Repo persistiert

API = f"https://api.telegram.org/bot{TOKEN}"
TIMEOUT = 25           # Long-Poll Timeout
SLEEP_BETWEEN = 1.5    # Pause zwischen Zyklen

# --- Hilfsfunktionen ---------------------------------------------------------

def load_last_update_id() -> Optional[int]:
    try:
        with open(LAST_ID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None

def save_last_update_id(update_id: int) -> None:
    try:
        with open(LAST_ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(update_id))
    except Exception:
        # im Actions-Runner ggf. schreibgeschützt → einfach ignorieren
        pass

def tg_get_updates(offset: Optional[int]) -> list:
    params = {"timeout": TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{API}/getUpdates", params=params, timeout=TIMEOUT + 5)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates not ok: {data}")
    return data.get("result", [])

def tg_send_message(text: str, chat_id: Optional[str] = None) -> None:
    target = (chat_id or DEFAULT_CHAT).strip()
    if not target:
        # Wenn kein Ziel vorhanden ist, nur ins Log schreiben,
        # damit der Poll trotzdem nicht crasht.
        print("[WARN] Kein TELEGRAM_CHAT_ID übergeben/gesetzt – sende nicht.")
        return
    payload = {"chat_id": target, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    r.raise_for_status()

def build_status_message() -> Tuple[str, dict]:
    """
    Versucht – falls vorhanden – deine generate_message.build_message() zu nutzen.
    Wenn nicht vorhanden, gibt es einen simplen Platzhalter zurück.
    """
    try:
        from generate_message import build_message  # type: ignore
        msg, state = build_message()
        return msg, (state or {})
    except Exception as e:
        print(f"[INFO] Fallback build_status_message: {e}")
        # Minimal-Nachricht statt Crash:
        return "📊 Status: Bot ist online. (Fallback-Text)", {}

# --- Command-Handling --------------------------------------------------------

def handle_command(cmd: str, chat_id: str) -> None:
    cmd = cmd.strip().lower()

    if cmd in ("/start", "/help"):
        tg_send_message(
            "👋 <b>Alex Signal Bot</b>\n"
            "Verfügbare Befehle:\n"
            "• /id – zeigt deine Chat-ID\n"
            "• /status – sendet den aktuellen Signal-Snapshot\n"
            "• /ping – einfache Erreichbarkeitsprobe",
            chat_id,
        )
    elif cmd == "/id":
        tg_send_message(f"🆔 Deine Chat-ID: <code>{chat_id}</code>", chat_id)
    elif cmd == "/ping":
        tg_send_message("🏓 Pong!", chat_id)
    elif cmd == "/status":
        msg, _ = build_status_message()
        tg_send_message(msg, chat_id)
    else:
        tg_send_message("❓ Unbekannter Befehl. Nutze /help.", chat_id)

# --- Haupt-Loop --------------------------------------------------------------

def main_once():
    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN fehlt (Env).")

    offset = load_last_update_id()
    print(f"[INFO] Starte Polling – offset={offset}")

    # Ein Durchlauf (für GitHub Actions, kein Endlos-Dienst):
    updates = tg_get_updates(offset)
    max_update_id = offset or 0

    for upd in updates:
        max_update_id = max(max_update_id, upd.get("update_id", 0))

        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat", {}) or {}).get("id", ""))

        if not text or not chat_id:
            continue

        if text.startswith("/"):
            print(f"[INFO] Command von {chat_id}: {text}")
            try:
                handle_command(text.split()[0], chat_id)
            except Exception as e:
                print(f"[ERROR] handle_command: {e}")
                tg_send_message("⚠️ Fehler bei der Verarbeitung des Befehls.", chat_id)

    if max_update_id:
        save_last_update_id(max_update_id + 1)
        print(f"[INFO] last_update_id -> {max_update_id + 1}")

if __name__ == "__main__":
    try:
        main_once()
    except requests.RequestException as e:
        # Netzwerkfehler sollen den Job nicht hässlich stacktracen
        print(f"[ERROR] Netzwerkfehler: {e}")
        raise
