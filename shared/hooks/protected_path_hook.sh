#!/bin/bash
# Claude Code pre-command hook: for risky operations, request explicit user
# approval via permissionDecision: "ask" rather than hard-blocking with exit 2.
#
# Protected targets: ~/Library/LaunchAgents, ~/Library/LaunchDaemons,
# /Library/, /etc/, launchctl state changes, sudo reboot/shutdown, chflags,
# git push --force.
#
# Emits a PreToolUse JSON decision ("ask") so Claude Code prompts Tim to
# approve or deny. Exit 0 with no JSON = pass through to default handling.

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('command', ''))
except:
    print('')
" 2>/dev/null)

if [ -z "$COMMAND" ]; then
    exit 0
fi

# Pattern-28 fix (lessons.md): for git commit/tag/notes/stash, the message
# body is data (the value of -m/-F), and quoted-heredoc bodies are literal.
# Previously the regexes below scanned the raw COMMAND, so a body that
# documented a state-change verb (e.g. "see launchctl kickstart runbook")
# would trigger a false-positive `permissionDecision: ask` and surface as
# "user doesn't want to proceed". `scan_command.py` returns a string that
# contains only operative shell tokens — substitutions inside -m args ARE
# kept (they execute at parse time), pure data is dropped. On parse failure
# the script falls back to the raw command (conservative).
# Resolve the scanner script relative to this hook so the same file
# works under ~/.claude/hooks/ on the live machine AND under the
# controlplane checkout in CI runners. Resolve the Python interpreter
# from PATH so different machines (different Homebrew prefixes, Linux
# CI, etc.) all work without hardcoded paths.
SCAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN_PY="$SCAN_DIR/scan_command.py"
PYTHON_BIN=""
for cand in python3 python3.11 python3.12; do
    if command -v "$cand" >/dev/null 2>&1; then PYTHON_BIN="$cand"; break; fi
done
if [ -n "$PYTHON_BIN" ] && [ -f "$SCAN_PY" ]; then
    SCAN=$(echo "$COMMAND" | "$PYTHON_BIN" "$SCAN_PY" 2>/dev/null)
fi
[ -z "$SCAN" ] && SCAN="$COMMAND"

# SSH commands operate on a different machine. The remote host has its
# own hooks and its own safety rules; scanning the SSH payload for local
# path names generates false positives (e.g. `ssh host "ls ~/Library/
# LaunchAgents"` would otherwise match Pattern 1 below). Skip SSH/SCP
# commands entirely — the remote host is responsible for its own gating.
if echo "$COMMAND" | grep -qE '^(ssh |scp )'; then
    exit 0
fi

ask() {
    python3 -c "
import json, sys
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'ask',
        'permissionDecisionReason': sys.argv[1]
    }
}))" "$1"
    exit 0
}

# Pattern 1: Commands that WRITE to a LaunchAgent/LaunchDaemon plist.
#
# Pattern-28 second-order fix (2026-05-01, lessons.md):
# `scan_command.py` now emits the `__LA_WRITE__` sentinel ONLY when an
# operative argument in a write position (cp/mv dest, tee/rm/chmod arg,
# `>`/`>>` redirect target, etc.) resolves to a path under
# `Library/LaunchAgents` or `Library/LaunchDaemons`. Read-only commands
# (cat, ls, diff, head, tail, file, stat, grep) and SOURCE args of
# cp/mv/rsync/install/ln no longer trigger this pattern, because the
# substring `Library/LaunchAgents` appearing in a path-arg-as-data was
# never semantically a "write to LaunchAgents" — it was just data.
#
# Anti-pattern this fixes (recorded for future hooks): never match a
# load-bearing path substring anywhere in a command. Always classify by
# argument role (write target vs source vs verb) at AST level.
if echo "$SCAN" | grep -q '__LA_WRITE__'; then
    ask "Command writes to LaunchAgent/LaunchDaemon plist. Approve to proceed."
fi

# Pattern 1a: defence-in-depth — `find ... -delete` / `find ... -exec rm`
# on a Library/LaunchAgents path. `find` is not in the write-classification
# table (its args are search roots, not write targets), so the sentinel
# won't fire on a benign `find Library/LaunchAgents -name x.plist`. Match
# the literal pattern only when the find pipeline carries a destructive
# action.
if echo "$SCAN" | grep -qE '(Library/LaunchAgents|Library/LaunchDaemons)' && \
   echo "$SCAN" | grep -qE 'find\s.*(-delete|-exec\s+rm)'; then
    ask "find ... -delete / -exec rm on LaunchAgent path. Approve to proceed."
fi

# Pattern 1b: launchctl read-only (list, print) — allowed ONLY when no
# state-changing verb appears anywhere in the pipeline. Without this guard,
# `launchctl list && launchctl kickstart -k foo` would exit 0 here before
# Pattern 2 ever runs.
if echo "$SCAN" | grep -qE 'launchctl\s+(list|print)' && \
   ! echo "$SCAN" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable|setenv|unsetenv)'; then
    exit 0
fi

# Pattern 2: launchctl state-changing commands
if echo "$SCAN" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable)'; then
    ask "launchctl state-changing command. Approve to proceed."
fi

# Pattern 3: plutil -extract without -o (destructive — overwrites source file)
if echo "$SCAN" | grep -qE 'plutil\s+-extract' && ! echo "$SCAN" | grep -qE '\-o\s'; then
    ask "plutil -extract without -o overwrites the source file. Approve to proceed."
fi

# Pattern 4: sudo reboot / shutdown / halt / init
if echo "$SCAN" | grep -qE 'sudo\s+(-n\s+)?(reboot|shutdown|halt|init)'; then
    ask "System reboot/shutdown. Approve to proceed."
fi

# Pattern 5: writes into /etc/ or /Library/ (system paths only).
#
# Pattern-28 second-order fix (2026-05-01): the previous regex
# `(>[> ]*|tee\s+|cp\s+.*|mv\s+.*|rm\s+.*)(/etc/|/Library/)` matched
# `/Library/` anywhere in the command — so `cp /Users/x/Library/foo /tmp/y`
# (path SUBSTRING) tripped it the same way Pattern 1 did. The scanner now
# emits `__SYS_WRITE__` only when the WRITE-TARGET arg begins with `/etc/`
# or `/Library/` (i.e. a true system path, not a user home Library path).
if echo "$SCAN" | grep -q '__SYS_WRITE__'; then
    ask "Modifies /etc or /Library system path. Approve to proceed."
fi

# Pattern 6: chflags (immutability changes)
if echo "$SCAN" | grep -qE 'chflags\s+(no)?uchg'; then
    ask "Immutability flag change (chflags uchg/nouchg). Approve to proceed."
fi

# Pattern 7: git push --force / -f / +<ref> to any remote.
# Force-pushes can rewrite history on a shared branch — always ask.
if echo "$SCAN" | grep -qE 'git\s+push\s+(.*\s)?(--force\b|-f\b|--force-with-lease\b)'; then
    ask "git push --force can rewrite remote history. Approve to proceed."
fi

exit 0
