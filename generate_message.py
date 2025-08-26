# generate_message.py
# Baut den Text für die nächste Telegram-Nachricht und legt ihn in message.txt ab.

from datetime import datetime

def build_message() -> str:
    """Erzeuge den Signal-Text. Hier kannst du später deine echte Logik einbauen."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "📡 Alex Signal Bot",
        f"⏱️ Zeit: {now}",
        "✅ Systemstatus: OK",
        "💡 Signal: (noch keins – Demo)",
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    msg = build_message()
    with open("message.txt", "w", encoding="utf-8") as f:
        f.write(msg)
    print("message.txt geschrieben.")
