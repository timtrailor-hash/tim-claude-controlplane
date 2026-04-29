#!/bin/bash
# continuous_learning_hook.sh — Stop hook that proposes memory updates
#
# Fires on: Stop (session end)
# Uses Haiku 4.5 to review the transcript and propose diffs to memory files.
# Proposals written to _pending_review.md for manual review next session.
#
# Non-blocking: failures are logged but never prevent session end.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/continuous_learning.py"

if [ ! -f "$HELPER" ]; then
    for p in \
        /Users/timtrailor/.claude/hooks/continuous_learning.py \
        /Users/timtrailor/code/tim-claude-controlplane/shared/hooks/continuous_learning.py; do
        [ -f "$p" ] && HELPER="$p" && break
    done
fi

[ ! -f "$HELPER" ] && exit 0

/opt/homebrew/bin/python3.11 "$HELPER" >>/tmp/continuous_learning.log 2>&1 &
disown
exit 0
