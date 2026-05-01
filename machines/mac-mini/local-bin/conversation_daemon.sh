#!/bin/zsh
# Conversation server daemon — runs via launchd as KeepAlive service.
export HOME="/Users/timtrailor"
export PYTHONPATH="$HOME/code:$HOME/code/claude-mobile:$HOME/code/sv08-print-tools:$PYTHONPATH"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Turn on the URL-push watcher (tappable notifications for URLs Claude prints).
# Disable by setting to 0 or unsetting. Added 2026-04-22.
export CLAUDE_URL_PUSH="1"
cd "$HOME/code/claude-mobile" || { echo "FATAL: cd $HOME/code/claude-mobile failed" >&2; exit 1; }
mkdir -p /tmp/claude_sessions
LOG_FILE="/tmp/claude_sessions/server.log"
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0) -gt 10485760 ]; then
    tail -1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
exec /opt/homebrew/bin/python3.11 conversation_server.py
