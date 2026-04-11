#!/bin/bash
# Claude Code pre-command hook: blocks commands touching protected paths
# without explicit user approval. Installed via settings.json hooks.
#
# Protected paths: ~/Library/LaunchAgents, ~/Library/LaunchDaemons,
# /Library/, /etc/, and launchctl bootstrap/bootout/kickstart commands.
#
# This hook reads the command from stdin and checks for protected patterns.
# Exit 0 = allow, Exit 2 = block with message.

# Read the tool input from stdin
INPUT=$(cat)

# Extract the command from the JSON input
COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('command', ''))
except:
    print('')
" 2>/dev/null)

if [ -z "$COMMAND" ]; then
    exit 0  # Can't parse, allow (don't block on hook failures)
fi

# Pattern 1: Commands touching LaunchAgent/LaunchDaemon plist files
if echo "$COMMAND" | grep -qE '(Library/LaunchAgents|Library/LaunchDaemons)'; then
    # Allow read-only commands
    if echo "$COMMAND" | grep -qE '^(cat |ls |plutil -p |plutil -lint |head |tail |wc |file |stat |md5 |shasum )'; then
        exit 0
    fi
    echo "BLOCKED: Command touches LaunchAgent/LaunchDaemon files. These are protected."
    echo "Use read_plist.sh for safe reading, or get explicit approval for modifications."
    echo "Command was: $COMMAND"
    exit 2
fi

# Pattern 1b: launchctl read-only commands (list, print) — always allowed
if echo "$COMMAND" | grep -qE 'launchctl\s+(list|print)'; then
    exit 0
fi

# Pattern 2: launchctl state-changing commands
if echo "$COMMAND" | grep -qE 'launchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable)'; then
    echo "BLOCKED: launchctl state-changing command requires explicit approval."
    echo "Command was: $COMMAND"
    exit 2
fi

# Pattern 3: plutil -extract without -o (destructive — overwrites file in-place)
if echo "$COMMAND" | grep -qE 'plutil\s+-extract' && ! echo "$COMMAND" | grep -qE '\-o\s'; then
    echo "BLOCKED: plutil -extract without -o flag overwrites the source file."
    echo "Use 'plutil -extract <key> <format> -o - <file>' for stdout output,"
    echo "or use read_plist.sh for safe extraction."
    echo "Command was: $COMMAND"
    exit 2
fi

# Pattern 4: sudo reboot/shutdown
if echo "$COMMAND" | grep -qE 'sudo\s+(-n\s+)?(reboot|shutdown|halt|init)'; then
    echo "BLOCKED: System reboot/shutdown requires explicit approval from Tim."
    echo "Command was: $COMMAND"
    exit 2
fi

# Pattern 5: Commands modifying /etc/ or /Library/ system paths
if echo "$COMMAND" | grep -qE '(>[> ]*|tee\s+|cp\s+.*|mv\s+.*|rm\s+.*)(/etc/|/Library/)'; then
    echo "BLOCKED: Command modifies system config paths. Requires explicit approval."
    echo "Command was: $COMMAND"
    exit 2
fi

# Pattern 6: chflags (immutability changes)
if echo "$COMMAND" | grep -qE 'chflags\s+(no)?uchg'; then
    echo "BLOCKED: Immutability flag changes require explicit approval."
    echo "Command was: $COMMAND"
    exit 2
fi

# Pattern 7: SSH commands to printer with dangerous gcode
# Blocks FIRMWARE_RESTART, RESTART, G28, PROBE, QGL, BED_MESH_CALIBRATE, SAVE_CONFIG via SSH
if echo "$COMMAND" | grep -qE 'ssh.*192\.168\.0\.108' || echo "$COMMAND" | grep -qE 'curl.*192\.168\.0\.108.*gcode/script'; then
    if echo "$COMMAND" | grep -qiE '(FIRMWARE_RESTART|RESTART[^_]|G28|PROBE|QUAD_GANTRY_LEVEL|BED_MESH_CALIBRATE|SAVE_CONFIG)'; then
        echo "BLOCKED: Dangerous printer gcode detected. These commands can destroy active prints."
        echo "Check print_stats.state first and get explicit approval from Tim."
        echo "Command was: $COMMAND"
        exit 2
    fi
fi

# Pattern 8: git push to known public repos without review
if echo "$COMMAND" | grep -qE 'git\s+push.*(sv08-print-tools|ClaudeCode|claude-mobile|castle-ofsted-agent)'; then
    echo "BLOCKED: Push to public repository requires explicit approval from Tim."
    echo "Command was: $COMMAND"
    exit 2
fi

exit 0
