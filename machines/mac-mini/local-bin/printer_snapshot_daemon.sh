#!/bin/zsh
# Printer monitoring daemon — runs via launchd as KeepAlive service.
export HOME="/Users/timtrailor"
export PYTHONPATH="$HOME/code:$HOME/code/sv08-print-tools:$PYTHONPATH"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$HOME/code/sv08-print-tools"
mkdir -p /tmp/printer_status
LOG_FILE="/tmp/printer_status/snapshot_daemon.log"
if [ -f "$LOG_FILE" ] && [ $(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0) -gt 10485760 ]; then
    tail -1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi
exec /opt/homebrew/bin/python3.11 printer_daemon.py
