#!/bin/bash
cd /home/walli/octodamus

SESSION_FILE="/home/walli/.bw_session"

if [ ! -f "$SESSION_FILE" ]; then
    echo "[$(date)] ERROR: No BW session file. Run: bash /home/walli/octodamus/bw_unlock.sh" >> /home/walli/octodamus/logs/telegram.log
    exit 1
fi

export BW_SESSION=$(cat "$SESSION_FILE")

if [ -z "$BW_SESSION" ]; then
    echo "[$(date)] ERROR: BW session file is empty" >> /home/walli/octodamus/logs/telegram.log
    exit 1
fi

echo "[$(date)] Session loaded, starting Telegram bot..." >> /home/walli/octodamus/logs/telegram.log
exec python3 telegram_bot.py
