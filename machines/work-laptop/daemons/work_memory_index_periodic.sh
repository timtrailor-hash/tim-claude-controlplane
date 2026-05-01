#!/bin/bash
# work_memory_index_periodic.sh — 6-hourly LaunchAgent wrapper around the
# existing claude-bridge/tools/work_memory_index.sh indexer.
#
# Why a wrapper exists separately from work_memory_index.sh:
#   - The SessionEnd hook calls work_memory_index.sh directly with set -e.
#   - LaunchAgents must NOT crash-loop on indexer failures, so this wrapper
#     swallows non-zero exits and always returns 0.
#   - Marker file gate (~/.claude/.work-laptop) prevents accidental run on
#     the personal Mac Mini if a deploy mistakenly copies the plist.

set -u  # do NOT set -e — we want to keep going past a failed inner script.

LOG="/tmp/work_memory_indexer.log"
ts() { date -u "+%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

MARKER="$HOME/.claude/.work-laptop"
INNER="$HOME/code/claude-bridge/tools/work_memory_index.sh"

if [ ! -f "$MARKER" ]; then
  log "marker $MARKER missing; not a work laptop, exiting 0"
  exit 0
fi

if [ ! -f "$INNER" ]; then
  log "indexer $INNER missing; exiting 0 (nothing to do)"
  exit 0
fi

log "running $INNER"

# Run the inner script. Capture rc but never propagate non-zero.
"$INNER" >> "$LOG" 2>&1
rc=$?
log "inner indexer exited rc=$rc"

# Always exit 0 — LaunchAgent must stay healthy.
exit 0
