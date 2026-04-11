#!/bin/bash
# commit_guard.sh — PreToolUse hook that enforces commit-time policies.
#
# Only fires on `git commit` commands. Three checks:
#   1. Session: trailer present (Pattern 18 fix — I violated this all
#      night on 2026-04-11). Format: "Session: YYYY-MM-DD (<8hex>)".
#   2. Doc-loss gate: if any topics/*.md file shrinks by >20 lines,
#      commit message must include "Removed-sections:" trailer listing
#      what was removed and where it went.
#   3. Authority map guard: if machines/<host>/system_map.yaml is in the
#      diff, commit message must include "Reviewed-By:" trailer.
#
# Mode:
#   COMMIT_GUARD_MODE=advisory (default): warn to stderr, exit 0 (allow)
#   COMMIT_GUARD_MODE=strict: exit 2 on any violation
#
# Reads JSON from stdin per Claude Code hook protocol.

INPUT=$(cat)
# Mode default: STRICT as of 2026-04-11. commit-time metadata gates are
# low-blast-radius and deterministic — no reason to leave soft after
# shakedown. The three enforced policies are cheap to satisfy and the
# error messages are actionable. Set COMMIT_GUARD_MODE=advisory to
# temporarily downgrade during recovery.
MODE="${COMMIT_GUARD_MODE:-strict}"

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null)

[ -z "$COMMAND" ] && exit 0

# Only fire on git commit commands
if ! echo "$COMMAND" | grep -qE '(^|[^a-z])git commit'; then
    exit 0
fi

# Extract the commit message from the command.
# Supports:
#   git commit -m "message"
#   git commit -m 'message'
#   git commit -F /path/to/file
#   git commit -m "$(cat <<'EOF' ... EOF\n)"   (heredoc in a subshell)
# For the heredoc case we take the whole substring between EOF markers.

MSG=""

# Form 1: -F <file>
if FFILE=$(echo "$COMMAND" | grep -oE '\-F [^ &;|]+' | head -1 | awk '{print $2}'); then
    if [ -n "$FFILE" ] && [ -f "$FFILE" ]; then
        MSG=$(cat "$FFILE")
    fi
fi

# Form 2: heredoc inside the command (Tim's usual form)
if [ -z "$MSG" ] && echo "$COMMAND" | grep -q "cat <<'EOF'"; then
    MSG=$(echo "$COMMAND" | python3 -c "
import sys, re
cmd = sys.stdin.read()
m = re.search(r\"cat <<'EOF'\n(.*?)\nEOF\", cmd, re.DOTALL)
if m:
    print(m.group(1))
")
fi

# Form 3: -m "..." (simple)
if [ -z "$MSG" ]; then
    MSG=$(echo "$COMMAND" | python3 -c "
import sys, re
cmd = sys.stdin.read()
# Try -m followed by quoted string
m = re.search(r'-m\s+\"([^\"]*)\"', cmd) or re.search(r\"-m\s+'([^']*)'\", cmd)
if m:
    print(m.group(1))
")
fi

# If we couldn't parse the message, skip — better to permit than block wrongly
[ -z "$MSG" ] && exit 0

ISSUES=""

# Check 1: Session: trailer present
if ! echo "$MSG" | grep -qE '^Session:\s+20[0-9]{2}-[0-9]{2}-[0-9]{2}\s+\([0-9a-f]{6,}\)'; then
    ISSUES="$ISSUES
- missing Session: trailer (format: 'Session: YYYY-MM-DD (<first-8-chars>)')"
fi

# Check 2: Doc-loss gate on topics/*.md files with >20 line shrink
# We must be in a git repo; look at staged diff.
cd "$(pwd)" 2>/dev/null
REPO_DIR=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$REPO_DIR" ]; then
    SHRUNK=""
    while IFS=$'\t' read -r added deleted path; do
        [ -z "$path" ] && continue
        case "$path" in
            topics/*.md|*/topics/*.md|memory/topics/*.md)
                if [ "$added" != "-" ] && [ "$deleted" != "-" ]; then
                    NET=$((deleted - added))
                    if [ "$NET" -gt 20 ]; then
                        SHRUNK="$SHRUNK
  - $path: -$deleted +$added (net -$NET lines)"
                    fi
                fi
                ;;
        esac
    done < <(cd "$REPO_DIR" && git diff --cached --numstat 2>/dev/null)

    if [ -n "$SHRUNK" ]; then
        if ! echo "$MSG" | grep -qE '^Removed-sections:'; then
            ISSUES="$ISSUES
- topic file(s) shrunk >20 lines without Removed-sections: trailer:$SHRUNK"
        fi
    fi

    # Check 3: Authority map guard — system_map.yaml needs Reviewed-By:
    if cd "$REPO_DIR" && git diff --cached --name-only 2>/dev/null | grep -q "system_map\.yaml$"; then
        if ! echo "$MSG" | grep -qE '^Reviewed-By:'; then
            ISSUES="$ISSUES
- machines/<host>/system_map.yaml is in diff — commit message must include 'Reviewed-By:' trailer (authority map change-control)"
        fi
    fi
fi

if [ -z "$ISSUES" ]; then
    exit 0
fi

{
    echo "[commit_guard] commit has policy violations:"
    echo "$ISSUES"
    echo ""
    echo "  Mode: $MODE"
    echo "  To bypass in strict mode, set COMMIT_GUARD_MODE=advisory or fix the issues."
} >&2

if [ "$MODE" = "strict" ]; then
    exit 2
fi
exit 0
