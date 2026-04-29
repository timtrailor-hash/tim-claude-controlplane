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

# Resolve symlinks for macOS (/tmp → /private/tmp)
REAL_PATH=$(python3 -c "import os; print(os.path.realpath('$FILE_PATH'))" 2>/dev/null || echo "$FILE_PATH")

# Parse YAML with grep (avoids PyYAML dependency)
check_path_list() {
    local section="$1"
    local in_section=0
    while IFS= read -r line; do
        case "$line" in
            "${section}:"*) in_section=1; continue ;;
            *:) in_section=0; continue ;;
        esac
        if [ "$in_section" = "1" ]; then
            local path_entry
            path_entry=$(echo "$line" | sed -n 's/^[[:space:]]*-[[:space:]]*//p')
            [ -z "$path_entry" ] && continue
            if [[ "$FILE_PATH" == "$path_entry"* ]] || [[ "$REAL_PATH" == "$path_entry"* ]]; then
                return 0
            fi
        fi
    done < "$CONFIG"
    return 1
}

if check_path_list "forbidden_paths"; then
    echo "[freeze_mode] BLOCKED: '$FILE_PATH' is in the forbidden list. Autonomous mode cannot write here." >&2
    exit 2
fi

if check_path_list "allowed_paths"; then
    exit 0
fi

echo "[freeze_mode] BLOCKED: '$FILE_PATH' is outside the allowed subtree for autonomous mode." >&2
echo "Allowed paths are defined in $CONFIG" >&2
exit 2
