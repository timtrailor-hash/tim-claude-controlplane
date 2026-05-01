#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/timtrailor/.local/bin"
export HOME="/Users/timtrailor"

# Kill any existing ttyd on port 7681
pkill -f 'ttyd.*7681' 2>/dev/null
pkill -f ngrok 2>/dev/null
sleep 2

# Fetch ttyd password from Keychain (audit 2026-04-11 §3.5).
TTYD_PASS=$(/usr/bin/security find-generic-password -s ttyd-auth -a timtrailor -w 2>/dev/null)
if [ -z "$TTYD_PASS" ]; then
    echo "FATAL: ttyd-auth not found in keychain. Run: security add-generic-password -a timtrailor -s ttyd-auth -w '<password>' -U" >&2
    exit 1
fi

# Bind to Tailscale IP only — no public tunnel needed (Pattern 22: rogue listener)
# Access via Tailscale: http://100.126.253.40:7681
ttyd --writable --port 7681 --interface 100.126.253.40 --credential "tim:$TTYD_PASS" /Users/timtrailor/.local/bin/claude-shell

# ngrok removed 2026-04-12 — was exposing shell to public internet.
# Tailscale provides authenticated network-level access. See lessons.md Pattern 22.
