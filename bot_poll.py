#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Robustes Telegram-Polling für GitHub Actions:
- Löscht Webhook (verhindert 409 Conflict)
- Nutzt long polling (timeout 50s)
- Persistiert last_update_id in Datei
- Sauberes Retry/Logging
Nur stdlib + requests.
"""

import os
import json
import time
from typing import Optional, List, Dict, Any

import requests

API_BASE = "https://api.telegram.org"

# --- Konfiguration per ENV (GitHub Secrets) ---
TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")  # Fallback auf alten Namen
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHATID")

# Pfad für Offset-Persistenz (Repo-Root in Actions)
STATE_PATH = os.getenv("LAST_UPDATE_FILE", "last_update_id.txt")

# Netzwerk/Retry
HTTP_TIMEOUT = 20
POLLING_TIMEOUT = 50      # long polling
MAX_RETRIES = 2
SLEEP_BETWEEN_RETRIES = 2


# ---------- Hilfen ----------
def _bot_url(method: str) -> str:
    if not TOKEN:
        raise RuntimeError("Fehlendes TOKEN (TELEGRAM_TOKEN).")
    return f"{API_BASE}/bot{TOKEN}/{method}"


def _load_last_update_id() -> Optional[int]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            return int(raw) if raw else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _save_last_update_id(value: int) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            f.write(str(value))
    except Exception as e:
        print(f"[WARN] Konnte last_update_id nicht speichern: {e}")


# ---------- Telegram Low-Level ----------
def tg_delete_webhook() -> None:
    """Webhook löschen, um 409-Conflicts zu vermeiden (idempotent)."""
    url = _bot_url("deleteWebhook")
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        # 200 mit ok:true erwartet – aber wir ignorieren bewusst Fehler,
        # weil der Webhook evtl. nie gesetzt war.
        print(f"[INFO] deleteWebhook status={r.status_code} body={r.text[:200]}")
    except Exception as e:
        print(f"[WARN] deleteWebhook: {e}")


def tg_get_updates(offset: Optional[int]) -> List[Dict[str, Any]]:
    """Long-Poll getUpdates mit Retry und 409-Heilung."""
    params = {
        "timeout": POLLING_TIMEOUT,
        "allowed_updates": json.dumps(["message"])  # wir brauchen nur Nachrichten
    }
    if offset is not None:
        params["offset"] = offset

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(_bot_url("getUpdates"), params=params, timeout=HTTP_TIMEOUT + POLLING_TIMEOUT)
            if r.status_code == 409:
                # Sicher ist sicher – Webhook löschen und 1x retryen
                print("[WARN] 409 Conflict bei getUpdates – lösche Webhook & retry…")
                tg_delete_webhook()
                time.sleep(1)
                continue
            r.raise_for_status()
            data = r.json()
            if not data.get("ok", False):
                raise RuntimeError(f"Telegram not ok: {data}")
            return data.get("result", [])
        except Exception as e:
            last_err = e
            print(f"[WARN] getUpdates attempt {attempt}/{MAX_RETRIES} failed: {e}")
            time.sleep(SLEEP_BETWEEN_RETRIES)
    # alle Versuche scheiterten
    raise RuntimeError(f"getUpdates failed: {last_err}")


def tg_send_message(chat_id: str, text: str, disable_web_page_preview: bool = True) -> None:
    if not TOKEN:
        raise RuntimeError("Fehlendes TOKEN (TELEGRAM_TOKEN).")
    if not chat_id:
        raise RuntimeError("Fehlende TELEGRAM_CHAT_ID.")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_web_page_preview,
    }
    r = requests.post(_bot_url("sendMessage"), data=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()


# ---------- Business-Logik ----------
def handle_message(msg: Dict[str, Any]) -> None:
    """Ganz schlanke Command-Handler – erweiterbar nach Bedarf."""
    if "text" not in msg:
        return
    text: str = msg["text"].strip()
    user = msg.get("from", {})
    uname = user.get("username") or user.get("first_name") or "User"

    if text.lower() in ("/start", "start"):
        tg_send_message(CHAT_ID, f"👋 Hi {uname}! Ich bin online und lausche auf deine Kommandos.")
    elif text.lower() in ("/ping", "ping"):
        tg_send_message(CHAT_ID, "🏓 pong")
    elif text.lower() in ("/help", "help"):
        tg_send_message(CHAT_ID, "ℹ️ Verfügbar: /start, /ping, /help")
    else:
        # hier könntest du weitere Kommandos anbinden
        print(f"[INFO] Unbekanntes Text-Event: {text!r}")


def process_updates(updates: List[Dict[str, Any]], last_update_id: Optional[int]) -> Optional[int]:
    """Events der Reihe nach verarbeiten und den neuesten update_id zurückgeben."""
    newest = last_update_id
    for upd in updates:
        uid = upd.get("update_id")
        if uid is None:
            continue
        # Telegram-Events: uns interessieren nur Messages
        msg = upd.get("message")
        if msg:
            handle_message(msg)
        newest = uid
    return newest


def main_once() -> None:
    print(f"[INFO] Starte Polling – offset={_load_last_update_id()}")
    # Idempotent: erst Webhook entfernen
    tg_delete_webhook()

    offset = _load_last_update_id()
    updates = tg_get_updates(offset)
    if not updates:
        print("[INFO] Keine neuen Updates.")
        return

    newest = process_updates(updates, offset)
    # Beim nächsten Poll wollen wir NACH dem letzten Event starten
    if newest is not None:
        _save_last_update_id(newest + 1)
        print(f"[INFO] last_update_id gespeichert: {newest + 1}")


if __name__ == "__main__":
    main_once()
