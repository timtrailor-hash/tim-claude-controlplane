#!/bin/bash
# Claude Code post-command hook: logs all Bash commands for audit trail.
# Appends to ~/.claude/audit.log with timestamp, command, and exit code.

INPUT=$(cat)

COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('command', data.get('stdout', '')))
except:
    print('')
" 2>/dev/null)

EXIT_CODE=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('exit_code', data.get('exitCode', '?')))
except:
    print('?')
" 2>/dev/null)

if [ -n "$COMMAND" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') | exit=$EXIT_CODE | $COMMAND" >> ~/.claude/audit.log
fi

exit 0
