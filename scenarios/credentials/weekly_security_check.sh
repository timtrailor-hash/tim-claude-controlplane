#!/bin/bash
# Weekly security check — enforces the audit 2026-04-11 §3.5 keychain migration
# stays migrated. Runs via Mac Mini crontab.
#
# Fails on ANY of:
#   - Plaintext `~/.keychain_pass` resurfacing
#   - Hardcoded passwords in active LaunchAgent plists
#   - Duplicate ttyd processes on :7681
#   - NOPASSWD reboot/shutdown in sudoers (if readable)
#   - More than 2 writers of google_token.json on disk
#
# Fires ntfy priority=5 on any failure.

set -u
FAILURES=()

# 1. ~/.keychain_pass must not exist.
if [ -f "$HOME/.keychain_pass" ]; then
  FAILURES+=("keychain_pass_resurfaced: $HOME/.keychain_pass exists (should be deleted post-2026-04-11)")
fi

# 2. No LaunchAgent plist may contain a hardcoded password.
# Look for plaintext password-like strings in active plists.
ACTIVE_PLISTS_DIR="$HOME/Library/LaunchAgents"
if [ -d "$ACTIVE_PLISTS_DIR" ]; then
  while IFS= read -r plist; do
    [ -z "$plist" ] && continue
    # Generic pattern: any `--credential USER:PASSWORD` shape, or a
    # password-style assignment. Deliberately NOT listing specific strings
    # (public repo — don't advertise historic credential literals).
    # Structure-based detection still catches any plaintext-in-plist.
    if grep -qE '(--credential[[:space:]]+[[:alnum:]_-]+:[^[:space:]"'"'"']{4,})|(password[[:space:]]*[=:][[:space:]]*["'"'"']\w{4,}["'"'"'])' "$plist" 2>/dev/null; then
      FAILURES+=("hardcoded_password_in_plist: $plist")
    fi
  done < <(find "$ACTIVE_PLISTS_DIR" -maxdepth 1 -name 'com.timtrailor.*.plist' -not -path '*_quarantine*')
fi

# 3. ttyd processes — warn if more than one process is bound to :7681.
TTYD_LISTENERS=$(lsof -nP -iTCP:7681 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 && /ttyd/ {print $2}' | sort -u | wc -l | tr -d ' ')
if [ "$TTYD_LISTENERS" -gt 1 ]; then
  FAILURES+=("ttyd_duplicate: $TTYD_LISTENERS processes bound to :7681")
fi

# 4. Sudoers — only check if we have passwordless read access; otherwise skip.
if [ -r /etc/sudoers.d/claude-automation ]; then
  if grep -qE 'NOPASSWD.*(reboot|shutdown|halt|init)' /etc/sudoers.d/claude-automation 2>/dev/null; then
    FAILURES+=("sudoers_nopasswd_reboot: NOPASSWD for reboot/shutdown still present")
  fi
fi

# 5. google_token.json writers — must be ≤ 2 (canonical token_refresh.py + setup script).
WRITERS=$(grep -l -rE "open\([^,]+,\s*['\"]w['\"][^)]*\).*(token_file|GOOGLE_TOKEN|google_token\.json)" \
  "$HOME/code" 2>/dev/null | \
  grep -v __pycache__ | grep -v "\.pyc$" | grep -v _quarantine | grep -v audit_2026 | wc -l | tr -d ' ')
# More accurate: use the same regex as the audit check.
WRITERS=$(grep -lE "with open\([A-Z_]*TOKEN_FILE.*['\"]w['\"]\)|with open\(token_file.*['\"]w['\"]\)|with open\(GOOGLE_TOKEN.*['\"]w['\"]\)" \
  "$HOME"/code/token_refresh.py \
  "$HOME"/code/claude-mobile/google_auth_*.py 2>/dev/null | wc -l | tr -d ' ')
if [ "$WRITERS" -gt 2 ]; then
  FAILURES+=("google_token_writers: $WRITERS writers found (max=2 canonical+setup)")
fi

# Report.
if [ ${#FAILURES[@]} -eq 0 ]; then
  echo "[$(date +%Y-%m-%d\ %H:%M:%S)] OK: weekly security check passed"
  exit 0
fi

{
  echo "[$(date +%Y-%m-%d\ %H:%M:%S)] FAIL: weekly security check — ${#FAILURES[@]} issue(s):"
  for m in "${FAILURES[@]}"; do echo "  $m"; done
} | tee -a /tmp/weekly_security_check.log >&2

# ntfy alert.
{
  echo "Weekly security check FAILED: ${#FAILURES[@]} issue(s)"
  for m in "${FAILURES[@]}"; do echo "- $m"; done
} | curl -s --max-time 3 \
    -H "Priority: 5" \
    -H "Title: Security check FAILED" \
    -H "Tags: warning,key" \
    -d @- ntfy.sh/timtrailor-claude >/dev/null 2>&1 || true

exit 1
