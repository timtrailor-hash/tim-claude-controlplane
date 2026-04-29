#!/bin/bash
# freeze_mode_hook.sh — PreToolUse hook restricting Write/Edit during autonomous mode
#
# Fires on: PreToolUse Write/Edit
# When /tmp/autonomous_task_active exists, only allows writes to paths
# listed in freeze_mode_allowed.yaml. Composes with printer-safety-check.sh
# (both must pass for an action to proceed).
#
# Exit 0 = allow, Exit 2 = block with message

LOCK_FILE="/tmp/autonomous_task_active"

if [ ! -f "$LOCK_FILE" ]; then
    exit 0
fi

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(data.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')" 2>/dev/null)

[ -z "$FILE_PATH" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG=""
for p in \
    "$SCRIPT_DIR/freeze_mode_allowed.yaml" \
    /Users/timtrailor/.claude/hooks/freeze_mode_allowed.yaml \
    /Users/timtrailor/code/tim-claude-controlplane/shared/hooks/freeze_mode_allowed.yaml; do
    [ -f "$p" ] && CONFIG="$p" && break
done

if [ -z "$CONFIG" ]; then
    echo "[freeze_mode] BLOCKED: autonomous mode active but freeze_mode_allowed.yaml not found. Denying all writes for safety." >&2
    exit 2
fi

DECISION=$(python3 -c "
import sys, yaml, os

config_path = '$CONFIG'
file_path = os.path.realpath('$FILE_PATH')

with open(config_path) as f:
    cfg = yaml.safe_load(f)

forbidden = cfg.get('forbidden_paths', [])
for fp in forbidden:
    if file_path.startswith(fp):
        print('FORBIDDEN')
        sys.exit(0)

allowed = cfg.get('allowed_paths', [])
for ap in allowed:
    if file_path.startswith(ap):
        print('ALLOWED')
        sys.exit(0)

print('DENIED')
" 2>/dev/null)

case "$DECISION" in
    ALLOWED)
        exit 0
        ;;
    FORBIDDEN)
        echo "[freeze_mode] BLOCKED: '$FILE_PATH' is in the forbidden list. Autonomous mode cannot write here." >&2
        exit 2
        ;;
    *)
        echo "[freeze_mode] BLOCKED: '$FILE_PATH' is outside the allowed subtree for autonomous mode." >&2
        echo "Allowed paths are defined in $CONFIG" >&2
        exit 2
        ;;
esac
