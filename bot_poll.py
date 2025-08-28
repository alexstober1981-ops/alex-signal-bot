#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import requests
from typing import Optional, Tuple

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
DEFAULT_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
LAST_ID_FILE = "last_update_id"

API = f"https://api.telegram.org/bot{TOKEN}"
TIMEOUT = 25
SLEEP_BETWEEN = 1.5

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
        pass

def tg_call(method: str, *, params=None, json_=None, timeout=15) -> dict:
    url = f"{API}/{method}"
    r = requests.post(url, params=params, json=json_, timeout=timeout) if json_ else \
        requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok", False):
        raise RuntimeError(f"Telegram {method} not ok: {data}")
    return data

def get_webhook_info() -> dict:
    try:
        return tg_call("getWebhookInfo").get("result", {})
    except Exception as e:
        print(f"[INFO] getWebhookInfo fail: {e}")
        return {}

def clear_webhook_if_needed() -> None:
    info = get_webhook_info()
    url = (info or {}).get("url", "")
    if url:
        print(f"[INFO] Webhook gesetzt ({url}) -> lösche…")
        tg_call("setWebhook", params={"url": ""}, timeout=20)
        print("[INFO] Webhook entfernt.")

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
        print("[WARN] Kein TELEGRAM_CHAT_ID gesetzt – Nachricht wird nicht gesendet.")
        return
    payload = {"chat_id": target, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    tg_call("sendMessage", json_=payload, timeout=15)

def build_status_message() -> Tuple[str, dict]:
    try:
        from generate_message import build_message  # type: ignore
        msg, state = build_message()
        return msg, (state or {})
    except Exception as e:
        print(f"[INFO] Fallback build_status_message: {e}")
        return "📊 Status: Bot ist online. (Fallback-Text)", {}

def handle_command(cmd: str, chat_id: str) -> None:
    cmd = cmd.strip().lower()
    if cmd in ("/start", "/help"):
        tg_send_message(
            "👋 <b>Alex Signal Bot</b>\n"
            "Befehle:\n"
            "• /id – zeigt deine Chat-ID\n"
            "• /status – Signal-Snapshot\n"
            "• /ping – Test",
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

def main_once():
    if not TOKEN:
        raise SystemExit("TELEGRAM_TOKEN fehlt (Env).")

    # >>> Fix für 409: Webhook entfernen, falls gesetzt
    clear_webhook_if_needed()

    offset = load_last_update_id()
    print(f"[INFO] Starte Polling – offset={offset}")

    # Versuch 1
    try:
        updates = tg_get_updates(offset)
    except requests.HTTPError as e:
        # Falls trotzdem 409 → noch einmal Webhook clearen und retry
        if e.response is not None and e.response.status_code == 409:
            print("[WARN] 409 Conflict bei getUpdates – lösche Webhook & retry…")
            clear_webhook_if_needed()
            updates = tg_get_updates(offset)  # Retry
        else:
            raise

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
        print(f"[ERROR] Netzwerkfehler: {e}")
        raise
