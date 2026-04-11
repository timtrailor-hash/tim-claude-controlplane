#!/bin/bash
# verify_laptop_agents.sh — nightly drift check on MacBook Pro LaunchAgents.
#
# Per audit 2026-04-11 §3.1: the MacBook Pro is a thin client. It must NOT run
# any com.timtrailor.* LaunchAgent except the explicit allowlist below. If any
# unknown agent reappears (e.g. backup-to-drive restores it from Drive), this
# script exits 1 and fires a ntfy alert.
#
# Wired into nightly cron on Tims-Mac-mini.local — see crontab.
# Audit reference: ~/Documents/Claude code/audit_2026-04-11/meta_report.md

set -u

LAPTOP=timtrailor@100.112.125.42
# Agents that ARE permitted to live on the laptop. Everything else is drift.
ALLOWED=(com.timtrailor.health-check)

# Pull the list of timtrailor plists on the laptop via SSH.
REMOTE_PLISTS=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$LAPTOP" \
  '(cd ~/Library && ls LaunchAgents/ 2>/dev/null) | grep "^com\.timtrailor\." | sed "s/\.plist$//"' 2>/dev/null || true)

# Also check live loaded state.
REMOTE_LOADED=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$LAPTOP" \
  'launchctl list 2>/dev/null | awk "/com\.timtrailor\./ {print \$3}"' 2>/dev/null || true)

DRIFT=""

check_unknown() {
  local label="$1"
  local kind="$2"
  [ -z "$label" ] && return 0
  for allowed in "${ALLOWED[@]}"; do
    if [ "$label" = "$allowed" ]; then
      return 0
    fi
  done
  DRIFT="${DRIFT}${kind}: ${label}\n"
}

while IFS= read -r label; do
  [ -z "$label" ] && continue
  check_unknown "$label" "plist"
done <<<"$REMOTE_PLISTS"

while IFS= read -r label; do
  [ -z "$label" ] && continue
  check_unknown "$label" "loaded"
done <<<"$REMOTE_LOADED"

if [ -n "$DRIFT" ]; then
  printf "FAIL: unauthorised LaunchAgent drift on MacBook Pro\n"
  printf "%b" "$DRIFT"
  # Fire ntfy alert if available (non-fatal if curl missing).
  if command -v curl >/dev/null 2>&1 && [ -f "$HOME/code/credentials.py" ]; then
    NTFY_TOPIC=$(/opt/homebrew/bin/python3.11 -c \
      "import sys; sys.path.insert(0, '$HOME/code'); from credentials import NTFY_TOPIC; print(NTFY_TOPIC)" 2>/dev/null || true)
    if [ -n "$NTFY_TOPIC" ]; then
      curl -s -H "Priority: 4" -H "Title: Laptop LaunchAgent drift" \
        -d "$(printf '%b' "$DRIFT")" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null || true
    fi
  fi
  exit 1
fi

echo "OK: no unauthorised LaunchAgents on MacBook Pro (allowlist: ${ALLOWED[*]})"
exit 0
