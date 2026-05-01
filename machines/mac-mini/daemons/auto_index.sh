#!/usr/bin/env bash
# Auto-index conversation on SessionEnd hook for Mac Mini.
# Reads JSON from stdin: {"session_id": "...", "transcript_path": "...", "cwd": "..."}

set -euo pipefail

LOG_DIR="/Users/timtrailor/code/memory_server_data"
LOG_FILE="$LOG_DIR/auto_index.log"
MEMORY_SERVER="/Users/timtrailor/code/memory_server.py"

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }
trap 'echo "{\"continue\": true}"' EXIT

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | /opt/homebrew/bin/python3.11 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null || true)
TRANSCRIPT=$(echo "$INPUT" | /opt/homebrew/bin/python3.11 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || true)

if [ -z "$SESSION_ID" ] || [ -z "$TRANSCRIPT" ]; then
    log "SKIP: missing session_id or transcript_path"
    exit 0
fi

if [ ! -s "$TRANSCRIPT" ]; then
    log "SKIP: transcript empty or missing: $TRANSCRIPT"
    exit 0
fi

log "START: indexing $SESSION_ID"

# Pass paths as env vars to avoid shell injection via TRANSCRIPT path
RESULT=$(SESSION_ID="$SESSION_ID" TRANSCRIPT_PATH="$TRANSCRIPT" /opt/homebrew/bin/python3.11 -c "
import os, sys
sys.path.insert(0, os.environ['HOME'] + '/code')
from memory_server import index_conversation
sid = os.environ['SESSION_ID']
path = os.environ['TRANSCRIPT_PATH']
print(index_conversation(sid, path))
" 2>&1) || true

log "DONE: $RESULT"
exit 0
