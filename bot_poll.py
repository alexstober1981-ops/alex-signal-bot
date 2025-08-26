# bot_poll.py
# Hilfs-Skript: generiert Message und sendet sie sofort (für manuelle Läufe).

import generate_message
import telegram_send

def main():
    generate_message.main()
    telegram_send.main()

if __name__ == "__main__":
    main()
