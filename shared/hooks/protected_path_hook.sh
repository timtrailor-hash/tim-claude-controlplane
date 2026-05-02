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

# Pattern-34 fix: read-only command bypass that handles pipes, chains, and
# multi-line commands. Previous Pattern-33 bypass rejected any command with
# |, &, or ; — causing false positives on safe patterns like `grep foo |
# head -5` or `cd dir && git diff`. Now: split on &&, ||, ;, pipe, newline
# and check whether EVERY segment is a known-safe read-only verb. Only if a
# redirect (>) is present do we skip the bypass entirely.
#
# Pipe splitting uses ' | ' (with spaces) to avoid matching \| inside grep
# patterns or || in conditionals (|| is split separately).
_is_safe_verb() {
    local verb="$1" second="$2" third="$3"
    case "$verb" in
        cat|head|tail|less|more|grep|rg|egrep|fgrep|wc|file|stat|ls|diff|strings|xxd|od|hexdump|readlink|realpath|basename|dirname|test|true|false|type|which|id|whoami|date|uname|sw_vers|df|du|uptime|ps|pgrep|lsof|netstat|dig|nslookup|host|ping|traceroute|jq|yq|printenv|echo|printf|sleep|sort|uniq|cut|tr|expr|bc|md5|shasum|sha256sum|md5sum|column|fmt|fold|expand|unexpand|rev|nl|comm|join|paste|tsort|seq|shuf)
            return 0 ;;
        git)
            case "$second" in
                add|status|diff|log|show|blame|branch|remote|fetch|rev-parse|config|check-ignore|ls-files|ls-tree|shortlog|reflog|describe|name-rev|for-each-ref|merge-base)
                    return 0 ;;
            esac ;;
        cd|pushd|popd)
            return 0 ;;
        gh)
            # third already set by caller
            case "$second" in
                browse|search|status)
                    return 0 ;;
                pr)
                    case "$third" in view|list|diff|checks|status|"") return 0 ;; esac ;;
                issue)
                    case "$third" in view|list|status|"") return 0 ;; esac ;;
                repo)
                    # `gh repo clone` writes to disk — exclude from read-only
                    # bypass. Keep view/list (read-only).
                    case "$third" in view|list|"") return 0 ;; esac ;;
                release)
                    case "$third" in view|list|"") return 0 ;; esac ;;
                label)
                    case "$third" in list|"") return 0 ;; esac ;;
                gist)
                    case "$third" in view|list|"") return 0 ;; esac ;;
                run)
                    case "$third" in view|list|"") return 0 ;; esac ;;
            esac ;;
    esac
    return 1
}

if ! echo "$COMMAND" | grep -qE '[>]' \
    && ! echo "$COMMAND" | grep -qE '\$\(|`' \
    && ! echo "$COMMAND" | grep -qF '<('; then
    ALL_SAFE=true
    # Split: && and || first (multi-char), then ; and newline, then pipe
    # (space-padded to avoid matching \| inside grep patterns).
    _SPLIT=$(echo "$COMMAND" | sed 's/ && /\n/g; s/ || /\n/g; s/;/\n/g; s/ | /\n/g')
    while IFS= read -r _seg; do
        _seg=$(echo "$_seg" | sed 's/^[[:space:]]*//')
        [ -z "$_seg" ] && continue
        # Residual metacharacter check: if segment still contains unescaped
        # |, &&, or || after splitting, it was a spaceless operator (e.g.
        # echo foo|rm or cd dir&&rm). Fall through to full scan.
        _stripped=$(echo "$_seg" | sed 's/\\|//g')
        if echo "$_stripped" | grep -qF '|'; then
            ALL_SAFE=false; break
        fi
        if echo "$_seg" | grep -qF '&&'; then
            ALL_SAFE=false; break
        fi
        if echo "$_seg" | grep -qF '||'; then
            ALL_SAFE=false; break
        fi
        _verb=$(echo "$_seg" | awk '{print $1}')
        _second=$(echo "$_seg" | awk '{print $2}')
        _third=$(echo "$_seg" | awk '{print $3}')
        [ -z "$_verb" ] && continue
        if ! _is_safe_verb "$_verb" "$_second" "$_third"; then
            ALL_SAFE=false
            break
        fi
    done <<< "$_SPLIT"
    if [ "$ALL_SAFE" = true ]; then
        exit 0
    fi
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
