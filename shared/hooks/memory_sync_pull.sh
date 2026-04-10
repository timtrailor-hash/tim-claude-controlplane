#!/bin/bash
# Laptop SessionStart hook — pull Mac Mini's indexed memory data back to laptop.
#
# After Mac Mini has indexed both machines' transcripts, its
# ~/code/memory_server_data/ contains the master chroma index. Pulling it to
# the laptop means laptop's memory MCP can search both machines' history.
#
# Critical: this rsync OVERWRITES laptop's local index with Mac Mini's master.
# Any local-only indexing the laptop did since the last pull is lost. That's
# acceptable because the corresponding JSONL transcripts will have been pushed
# by memory_sync_push.sh and re-indexed by Mac Mini's hourly cron.
#
# Atomic: rsync to a temp dir, then mv. Avoids leaving chroma in inconsistent
# state if rsync is killed mid-transfer (chroma uses sqlite + binary files).
#
# Skipped if memory_server is currently running (lock file exists). Will retry
# on next session.
#
# Logs to ~/.claude/memory_sync.log.

set -uo pipefail

LOG=~/.claude/memory_sync.log
TS=$(date "+%Y-%m-%d %H:%M:%S")
DEST=~/code/memory_server_data
LOCK="$DEST/memory_server.lock"

# Machine-aware: this script is a no-op on Mac Mini (it IS the master, nothing to pull from)
HOSTNAME_NOW=$(hostname -s)
case "$HOSTNAME_NOW" in
    *macmini*|*mac-mini*|*mini*)
        echo "[$TS] PULL: skip — running on Mac Mini (it's the master)" >> "$LOG"
        exit 0
        ;;
esac

# Skip if Mac Mini unreachable
if ! ssh -o ConnectTimeout=3 -o BatchMode=yes timtrailor@192.168.0.172 'true' 2>/dev/null; then
    echo "[$TS] PULL: skip — Mac Mini unreachable" >> "$LOG"
    exit 0
fi

# Skip if local memory_server is ACTIVELY running (lsof shows a process holding it).
# A bare lock file is fine — it's normally stale across SessionStart since the MCP
# server isn't loaded yet at hook execution time.
if [ -f "$LOCK" ] && lsof "$LOCK" >/dev/null 2>&1; then
    echo "[$TS] PULL: skip — local memory_server is actively running" >> "$LOG"
    exit 0
fi

# rsync to a sibling tmp dir, then atomic swap
TMP="$DEST.sync_tmp"
rm -rf "$TMP"
mkdir -p "$TMP"

# Resolve symlink — DEST is currently a symlink to ~/code/memory_server/data,
# but we want to actually pull into the canonical location and update the
# symlink to point at it.
REAL_DEST=$(readlink -f "$DEST" 2>/dev/null || echo "$DEST")

(
    OUT=$(rsync -au --delete-after \
        --exclude='memory_server.lock' \
        --exclude='auto_index.log' \
        --exclude='index_all.log' \
        --exclude='indexer.log' \
        timtrailor@192.168.0.172:code/memory_server_data/ \
        "$TMP/" 2>&1)
    RC=$?

    if [ $RC -ne 0 ]; then
        echo "[$TS] PULL: rsync failed rc=$RC: $(echo "$OUT" | tail -3)" >> "$LOG"
        rm -rf "$TMP"
        exit 0
    fi

    # Verify the chroma directory landed
    if [ ! -d "$TMP/chroma" ]; then
        echo "[$TS] PULL: chroma missing in synced data — aborting swap" >> "$LOG"
        rm -rf "$TMP"
        exit 0
    fi

    # Atomic swap into the real destination (which may be the symlink target)
    BACKUP="$DEST.swap_backup_$(date +%s)"
    if [ -L "$DEST" ]; then
        # DEST is a symlink — point it at the new location
        rm -f "$DEST"
        mv "$TMP" "$DEST.real"
        ln -s "$DEST.real" "$DEST"
    else
        # DEST is a real directory — backup-then-replace
        mv "$REAL_DEST" "$BACKUP"
        mv "$TMP" "$REAL_DEST"
        rm -rf "$BACKUP"
    fi

    SIZE=$(du -sh "$DEST" 2>/dev/null | awk '{print $1}')
    echo "[$TS] PULL: OK — synced master index, size=$SIZE" >> "$LOG"
) &

exit 0
