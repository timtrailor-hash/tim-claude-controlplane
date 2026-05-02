#!/bin/bash

# Pattern 36 fix: bypass for server-internal claude calls.
# conversation_server's haiku tab-title generator sets
# CLAUDE_HOOKS_BYPASS=server_internal via env_for_claude_cli().
# Skip - the call is already inside a trusted parent process.
if [ "${CLAUDE_HOOKS_BYPASS:-}" = "server_internal" ]; then
    exit 0
fi

# memory_sync_pull.sh — pull transcripts from Mac Mini and rebuild locally
#
# ARCHITECTURE (Phase 5): Transcripts are canonical. Indexes are derived.
# Instead of rsyncing live DB files (brittle), we:
# 1. rsync JSONL transcripts from Mac Mini
# 2. Run rebuild_index.sh to regenerate local ChromaDB + FTS5
#
# This is slower than raw DB copy but deterministic and corruption-proof.

set -uo pipefail

LOG=~/.claude/memory_sync.log
TS=$(date "+%Y-%m-%d %H:%M:%S")

# Machine-aware: no-op on Mac Mini
case "$(hostname -s)" in *mini*|*Mini*) exit 0 ;; esac

# Skip if Mac Mini unreachable
if ! ssh -o ConnectTimeout=3 -o BatchMode=yes timtrailor@192.168.0.172 "true" 2>/dev/null; then
    echo "[$TS] PULL: skip — Mac Mini unreachable" >> "$LOG"
    exit 0
fi

# Skip if local memory_server is running
if [ -f ~/code/memory_server_data/memory_server.lock ] && lsof ~/code/memory_server_data/memory_server.lock >/dev/null 2>&1; then
    echo "[$TS] PULL: skip — memory_server running" >> "$LOG"
    exit 0
fi

# Step 1: rsync transcripts from Mac Mini (additive, never delete)
(
    rsync -au --include="*/" --include="*.jsonl" --exclude="*" \
        timtrailor@192.168.0.172:.claude/projects/ \
        ~/.claude/projects/ 2>&1
    RC=$?
    echo "[$TS] PULL: transcripts synced (rc=$RC)" >> "$LOG"
    
    # Step 2: rebuild local index from all transcripts
    if [ -x ~/code/rebuild_index.sh ]; then
        bash ~/code/rebuild_index.sh >> "$LOG" 2>&1
        echo "[$TS] PULL: index rebuilt" >> "$LOG"
    fi
) &

exit 0
