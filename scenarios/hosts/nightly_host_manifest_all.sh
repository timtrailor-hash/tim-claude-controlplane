#!/bin/bash
# nightly_host_manifest_all.sh — run host-role manifest check on BOTH hosts.
#
# Runs locally (Mac Mini) and then SSHes to the laptop to run there.
# This avoids the macOS Full Disk Access restriction that blocks remote
# `crontab -e` edits on the laptop — we keep a single schedule on Mac Mini.
#
# Audit 2026-04-11 §4.6. Wired into Mac Mini crontab.

set -u

REPO="$HOME/code/tim-claude-controlplane"
SCRIPT="$REPO/shared/hooks/verify_host_manifest.sh"
LAPTOP=timtrailor@100.112.125.42
LOG=/tmp/nightly_host_manifest_all.log
TS=$(date "+%Y-%m-%d %H:%M:%S")

# Capture each side's output and exit code, then append to log atomically.
TMP=$(mktemp)
{
  echo ""
  echo "===== $TS ====="
  echo "--- Mac Mini ---"
} >> "$TMP"

bash "$SCRIPT" >> "$TMP" 2>&1
MAC_RC=$?

{
  echo ""
  echo "--- MacBook Pro (via SSH) ---"
} >> "$TMP"

ssh -o ConnectTimeout=10 -o BatchMode=yes "$LAPTOP" \
  "bash \$HOME/code/tim-claude-controlplane/shared/hooks/verify_host_manifest.sh" >> "$TMP" 2>&1
LAPTOP_RC=$?

echo "" >> "$TMP"
echo "Mac Mini RC=$MAC_RC   Laptop RC=$LAPTOP_RC" >> "$TMP"
cat "$TMP" | tee -a "$LOG"
rm -f "$TMP"

if [ "$MAC_RC" -ne 0 ] || [ "$LAPTOP_RC" -ne 0 ]; then
  exit 1
fi
exit 0
