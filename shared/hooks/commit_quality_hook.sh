#!/bin/bash
# commit_quality_hook.sh — PreToolUse hook for git commit quality gates
#
# Fires on: PreToolUse Bash matching "git commit"
# Three checks in order:
#   (a) Secret scan — regex patterns for common credential types in staged files
#   (b) Ruff lint — ruff check on staged .py files (skipped if ruff not installed)
#   (c) Session trailer — must include "Session: YYYY-MM-DD (<hex>)" line
#
# Exit 0 = allow, Exit 2 = block with message

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('command', ''))
except Exception:
    print('')" 2>/dev/null)

[ -z "$COMMAND" ] && exit 0

# Pattern-28 fix: scan only the operative shell tokens, not heredoc bodies or
# -m message values. Otherwise this hook fires on any Bash command that
# happens to mention "git commit" inside a heredoc/quoted string (e.g. when
# Claude builds a /tmp file containing review context that documents commit
# semantics). See lessons.md Pattern 28 + scan_command.py.
#
# Resolve scan_command.py relative to this script so it works regardless of
# whether this hook is deployed under ~/.claude/hooks/ or run from the
# controlplane checkout. Use python3 from PATH for portability across
# Homebrew prefixes / Linux CI / different machines.
SCAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN_PY="$SCAN_DIR/scan_command.py"
SCAN=""
if command -v python3 >/dev/null 2>&1 && [ -f "$SCAN_PY" ]; then
    SCAN=$(echo "$COMMAND" | python3 "$SCAN_PY" 2>/dev/null)
fi
[ -z "$SCAN" ] && SCAN="$COMMAND"

if ! echo "$SCAN" | grep -qE '(^|[^a-z])git commit'; then
    exit 0
fi

ISSUES=""

# ── (a) Secret scan on staged file content ──
# Skip paths whose entire purpose is to DEFINE credential-detection patterns
# (the patterns will look like real secrets to this scanner). Matches the
# names of the hook source files, the controlplane allowlist, work-topic
# rule files, and lessons.md (which discusses these patterns by name).
EXCLUDE_RE='(credential_leak_hook\.sh|commit_quality_hook\.sh|WORK_ALLOWLIST\.yaml|shared/work-topics/lessons\.md|shared/work-topics/quality-toolchain\.md|shared/work-topics/credentials-keychain-work\.md)'
STAGED_FILES_KEEP=$(git diff --cached --name-only 2>/dev/null | grep -vE "$EXCLUDE_RE" | tr '\n' ' ')
if [ -n "$STAGED_FILES_KEEP" ]; then
    # shellcheck disable=SC2086
    STAGED_CONTENT=$(git diff --cached --unified=0 -- $STAGED_FILES_KEEP 2>/dev/null | grep '^\+[^+]' | head -500)
else
    STAGED_CONTENT=""
fi

if [ -n "$STAGED_CONTENT" ]; then
    while IFS= read -r pat; do
        MATCH=$(echo "$STAGED_CONTENT" | grep -oiE "$pat" | head -1)
        if [ -n "$MATCH" ]; then
            ISSUES="$ISSUES
- SECRET DETECTED in staged content: credential pattern matched. Remove credentials before committing."
            break
        fi
    done << 'PATTERNS'
AKIA[0-9A-Z]{16}
sk-[a-zA-Z0-9]{20,}
ghp_[a-zA-Z0-9]{36}
gho_[a-zA-Z0-9]{36}
github_pat_[a-zA-Z0-9_]{22,}
sk-ant-[a-zA-Z0-9-]{20,}
xoxb-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}
xoxp-[0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{24}
AIza[0-9A-Za-z_-]{35}
BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY
password\s*[=:]\s*["'][^\s"']{8,}
PATTERNS
fi

# ── (b) Ruff lint on staged .py files ──
if command -v ruff >/dev/null 2>&1; then
    STAGED_PY=$(git diff --cached --name-only 2>/dev/null | grep '\.py$')
    if [ -n "$STAGED_PY" ]; then
        RUFF_ERRORS=""
        while IFS= read -r pyfile; do
            [ -f "$pyfile" ] || continue
            RESULT=$(ruff check --select E,F --no-fix "$pyfile" 2>&1)
            if [ $? -ne 0 ] && [ -n "$RESULT" ]; then
                RUFF_ERRORS="$RUFF_ERRORS
$RESULT"
            fi
        done <<< "$STAGED_PY"
        if [ -n "$RUFF_ERRORS" ]; then
            ISSUES="$ISSUES
- LINT ERRORS in staged Python files (ruff):$RUFF_ERRORS"
        fi
    fi
fi

# ── (c) Session trailer check ──
MSG=""
if echo "$COMMAND" | grep -q "cat <<'EOF'"; then
    MSG=$(echo "$COMMAND" | python3 -c "
import sys, re
cmd = sys.stdin.read()
m = re.search(r\"cat <<'EOF'\n(.*?)\nEOF\", cmd, re.DOTALL)
if m: print(m.group(1))" 2>/dev/null)
fi
if [ -z "$MSG" ]; then
    MSG=$(echo "$COMMAND" | python3 -c "
import sys, re
cmd = sys.stdin.read()
m = re.search(r'-m\s+\"([^\"]*)\"', cmd) or re.search(r\"-m\s+'([^']*)'\", cmd)
if m: print(m.group(1))" 2>/dev/null)
fi
if [ -n "$MSG" ]; then
    if ! echo "$MSG" | grep -qE '^Session:\s+20[0-9]{2}-[0-9]{2}-[0-9]{2}\s+\([0-9a-f]{6,}\)'; then
        ISSUES="$ISSUES
- Missing Session: trailer (required format: 'Session: YYYY-MM-DD (<first-8-chars>)')"
    fi
fi

if [ -z "$ISSUES" ]; then
    exit 0
fi

{
    echo "[commit_quality] blocked — quality gate violations:"
    echo "$ISSUES"
} >&2

exit 2
