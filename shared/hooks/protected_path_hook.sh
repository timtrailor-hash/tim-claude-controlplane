#!/bin/bash
# Claude Code pre-command hook: for risky operations, request explicit user
# approval via permissionDecision: "ask" rather than hard-blocking with exit 2.

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

# SSH/SCP: remote host has its own hooks.
if echo "$COMMAND" | grep -qE '^(ssh |scp )'; then
    exit 0
fi

# Pattern-33 fix: truly read-only, SIMPLE commands cannot modify anything
# this hook protects. But only bypass if the command has NO redirect operators,
# pipes, semicolons, or && — any of those could route output to a protected
# path or chain a dangerous command after a safe one.
if ! echo "$COMMAND" | grep -qE '[>|;&]'; then
    FIRST_VERB=$(echo "$COMMAND" | sed 's/^cd [^[:space:]]*[[:space:]]*//' | awk '{print $1}')
    case "$FIRST_VERB" in
        cat|head|tail|less|more|grep|rg|wc|file|stat|ls|diff|strings|xxd|od|hexdump|readlink|realpath|basename|dirname|test|true|false|type|which|id|whoami|date|uname|sw_vers|df|du|uptime|ps|pgrep|lsof|netstat|dig|nslookup|host|ping|traceroute|jq|yq|printenv)
            exit 0 ;;
        git)
            SECOND_WORD=$(echo "$COMMAND" | sed 's/^cd [^[:space:]]*[[:space:]]*//' | awk '{print $2}')
            case "$SECOND_WORD" in
                add|status|diff|log|show|blame|branch|remote|fetch|stash|rev-parse|config|check-ignore|ls-files|ls-tree|shortlog|reflog|describe|name-rev|for-each-ref)
                    exit 0 ;;
            esac ;;
    esac
fi

# scan_command.py: strip data tokens (commit bodies, quoted heredocs).
SCAN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN_PY="$SCAN_DIR/scan_command.py"
PYTHON_BIN=""
for cand in /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.12 \
            /usr/local/bin/python3.11 /usr/local/bin/python3.12 \
            python3.11 python3.12 python3; do
    if [ -x "$cand" ] || command -v "$cand" >/dev/null 2>&1; then
        PYTHON_BIN="$cand"
        break
    fi
done
if [ -n "$PYTHON_BIN" ] && [ -f "$SCAN_PY" ]; then
    SCAN=$(echo "$COMMAND" | "$PYTHON_BIN" "$SCAN_PY" 2>/dev/null)
fi
[ -z "$SCAN" ] && SCAN="$COMMAND"

ask() {
    "${PYTHON_BIN:-python3}" -c "
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

# Pattern 1: WRITE to a LaunchAgent/LaunchDaemon plist.
if echo "$SCAN" | grep -q '__LA_WRITE__'; then
    ask "Command writes to LaunchAgent/LaunchDaemon plist. Approve to proceed."
fi

# Pattern 1a: find ... -delete / -exec rm on LaunchAgent path.
if echo "$SCAN" | grep -qE '(Library/LaunchAgents|Library/LaunchDaemons)' && \
   echo "$SCAN" | grep -qE 'find\s.*(-delete|-exec\s+rm)'; then
    ask "find ... -delete / -exec rm on LaunchAgent path. Approve to proceed."
fi

# Pattern 1b: launchctl read-only (list, print) — safe when no state-changing verb.
if echo "$SCAN" | grep -qE 'launchctl\s+(list|print)' && \
   ! echo "$SCAN" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable|setenv|unsetenv)'; then
    exit 0
fi

# Pattern 2: launchctl state-changing commands
if echo "$SCAN" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable)'; then
    ask "launchctl state-changing command. Approve to proceed."
fi

# Pattern 3: plutil -extract without -o
if echo "$SCAN" | grep -qE 'plutil\s+-extract' && ! echo "$SCAN" | grep -qE '\-o\s'; then
    ask "plutil -extract without -o overwrites the source file. Approve to proceed."
fi

# Pattern 4: sudo reboot / shutdown / halt / init
if echo "$SCAN" | grep -qE 'sudo\s+(-n\s+)?(reboot|shutdown|halt|init)'; then
    ask "System reboot/shutdown. Approve to proceed."
fi

# Pattern 5: writes into /etc/ or /Library/ (system paths only).
if echo "$SCAN" | grep -q '__SYS_WRITE__'; then
    ask "Modifies /etc or /Library system path. Approve to proceed."
fi

# Pattern 6: chflags (immutability changes)
if echo "$SCAN" | grep -qE 'chflags\s+(no)?uchg'; then
    ask "Immutability flag change (chflags uchg/nouchg). Approve to proceed."
fi

# Pattern 7: git push --force
if echo "$SCAN" | grep -qE 'git\s+push\s+(.*\s)?(--force\b|-f\b|--force-with-lease\b)'; then
    ask "git push --force can rewrite remote history. Approve to proceed."
fi

exit 0
