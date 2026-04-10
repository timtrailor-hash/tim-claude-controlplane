#!/bin/bash
# Laptop SessionEnd hook — push laptop's ~/.claude/projects/ JSONL transcripts
# to Mac Mini so the central indexer can pick them up.
#
# Why: implements the bidirectional memory sync (Option C+). Laptop is the
# producer of new transcripts when Tim is on the laptop; Mac Mini is the
# canonical indexer. After indexing, Mac Mini's data dir gets pulled back
# by memory_sync_pull.sh on next SessionStart.
#
# Behaviour:
# - rsync -au (archive, update — only files newer than destination)
# - --delete is OFF (never delete remote files)
# - Includes only .jsonl files, excludes everything else
# - Backgrounded with nohup so SessionEnd isn't blocked
# - Logs to ~/.claude/memory_sync.log

set -uo pipefail

LOG=~/.claude/memory_sync.log
TS=$(date "+%Y-%m-%d %H:%M:%S")

# Machine-aware: this script is a no-op on Mac Mini (it IS the master, nothing to push to)
HOSTNAME_NOW=$(hostname -s)
case "$HOSTNAME_NOW" in
    *macmini*|*mac-mini*|*mini*)
        echo "[$TS] PUSH: skip — running on Mac Mini (it's the master)" >> "$LOG"
        exit 0
        ;;
esac

# Skip if Mac Mini unreachable
if ! ssh -o ConnectTimeout=3 -o BatchMode=yes timtrailor@192.168.0.172 'true' 2>/dev/null; then
    echo "[$TS] PUSH: skip — Mac Mini unreachable" >> "$LOG"
    exit 0
fi

# rsync in background, log result
(
    OUT=$(rsync -au --include='*/' --include='*.jsonl' --exclude='*' \
        ~/.claude/projects/ \
        timtrailor@192.168.0.172:.claude/projects/ 2>&1)
    RC=$?
    NUM=$(echo "$OUT" | grep -c "^.*\.jsonl$" || true)
    echo "[$TS] PUSH: rc=$RC files_changed=$NUM" >> "$LOG"
) &

exit 0
