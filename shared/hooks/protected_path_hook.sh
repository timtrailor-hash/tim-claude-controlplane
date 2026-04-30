#!/bin/bash
# Claude Code pre-command hook: for risky operations, request explicit user
# approval via permissionDecision: "ask" rather than hard-blocking with exit 2.
#
# Protected targets: ~/Library/LaunchAgents, ~/Library/LaunchDaemons,
# /Library/, /etc/, launchctl state changes, sudo reboot/shutdown, chflags,
# dangerous printer gcode via SSH/curl, pushes to public repos.
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
SCAN=$(echo "$COMMAND" | /opt/homebrew/bin/python3.11 /Users/timtrailor/.claude/hooks/scan_command.py 2>/dev/null)
[ -z "$SCAN" ] && SCAN="$COMMAND"

# SSH commands operate on a different machine. The remote host has its
# own hooks and its own safety rules; scanning the SSH payload for local
# path names generates false positives (e.g. `ssh host "ls ~/Library/
# LaunchAgents"` would otherwise match Pattern 1 below). Printer gcode
# and destructive patterns still match further down if they appear in
# an SSH command, because those regex patterns intentionally include
# explicit printer IPs or filesystem markers that are NOT local-only.
if echo "$COMMAND" | grep -qE '^(ssh |scp )'; then
    # Still apply printer-specific patterns (7) since they reference
    # printer IPs and are equally dangerous over SSH. Everything else
    # skips.
    if echo "$COMMAND" | grep -qE 'ssh.*192\.168\.0\.108' || \
       echo "$COMMAND" | grep -qE 'curl.*192\.168\.0\.108.*gcode/script'; then
        if echo "$COMMAND" | grep -qiE '(FIRMWARE_RESTART|RESTART[^_]|G28|PROBE|QUAD_GANTRY_LEVEL|BED_MESH_CALIBRATE|SAVE_CONFIG)'; then
            ask "Dangerous printer gcode via SSH. Confirm print_stats.state first."
        fi
    fi
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

# Pattern 1: Commands touching LaunchAgent/LaunchDaemon plist files
if echo "$SCAN" | grep -qE '(Library/LaunchAgents|Library/LaunchDaemons)'; then
    # Allow read-only commands straight through. `find` is treated as
    # read-only if the pipeline doesn't include -delete / -exec rm.
    if echo "$SCAN" | grep -qE '^(cat |ls |plutil -p |plutil -lint |head |tail |wc |file |stat |md5 |shasum |grep |find )'; then
        if echo "$SCAN" | grep -qE 'find\s.*(-delete|-exec\s+rm)'; then
            ask "find ... -delete / -exec rm on LaunchAgent path. Approve to proceed."
        fi
        # Defence-in-depth: a chained `cat ... && launchctl kickstart` would
        # otherwise exit 0 here without ever reaching Pattern 2. Check for
        # state-changing launchctl verbs in the pipeline before allowing.
        if echo "$SCAN" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable|setenv|unsetenv)'; then
            ask "launchctl state-changing command in pipeline alongside LaunchAgent path read. Approve to proceed."
        fi
        exit 0
    fi
    # launchctl print/list anywhere in the pipeline is read-only even if
    # the pipeline also touches Library/LaunchAgents (e.g. combined with find).
    if echo "$SCAN" | grep -qE 'launchctl\s+(list|print)' && \
       ! echo "$SCAN" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable|setenv|unsetenv)'; then
        exit 0
    fi
    ask "Command writes to LaunchAgent/LaunchDaemon plist. Approve to proceed."
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

# Pattern 5: writes into /etc/ or /Library/
if echo "$SCAN" | grep -qE '(>[> ]*|tee\s+|cp\s+.*|mv\s+.*|rm\s+.*)(/etc/|/Library/)'; then
    ask "Modifies /etc or /Library system path. Approve to proceed."
fi

# Pattern 6: chflags (immutability changes)
if echo "$SCAN" | grep -qE 'chflags\s+(no)?uchg'; then
    ask "Immutability flag change (chflags uchg/nouchg). Approve to proceed."
fi

# Pattern 7: Dangerous printer gcode via SSH or direct curl to Moonraker
# Bypasses the Moonraker allowlist — can kill an active print.
if echo "$SCAN" | grep -qE 'ssh.*192\.168\.0\.108' || echo "$SCAN" | grep -qE 'curl.*192\.168\.0\.108.*gcode/script'; then
    if echo "$SCAN" | grep -qiE '(FIRMWARE_RESTART|RESTART[^_]|G28|PROBE|QUAD_GANTRY_LEVEL|BED_MESH_CALIBRATE|SAVE_CONFIG)'; then
        ask "Dangerous printer gcode can destroy an active print. Confirm print_stats.state first, then approve to proceed."
    fi
fi

# Pattern 8: git push to known public repos
if echo "$SCAN" | grep -qE 'git\s+push.*(sv08-print-tools|ClaudeCode|claude-mobile|castle-ofsted-agent)'; then
    ask "Push to public GitHub repo. Approve to proceed."
fi

exit 0
