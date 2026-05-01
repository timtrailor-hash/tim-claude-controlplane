#!/usr/bin/env python3
"""
memory_index_all.py — find every JSONL transcript in ~/.claude/projects/ that
hasn't been indexed yet, and index it via memory_server.index_conversation.

Designed to run on Mac Mini as a periodic cron. After a sync from the laptop
deposits new transcripts, this picks them up.

Tracks indexed conversations in the indexed_convs SQLite table that
memory_server.py already maintains. Idempotent — safe to run repeatedly.

Logs to ~/code/memory_server_data/index_all.log.
Exit codes: 0 = OK, 1 = fatal error (e.g. memory_server import failed).
"""

import sqlite3
import sys
import time
from pathlib import Path

LOG = Path.home() / "code" / "memory_server_data" / "index_all.log"
PROJECTS = Path.home() / ".claude" / "projects"
SQLITE = Path.home() / "code" / "memory_server_data" / "fts.db"


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def main():
    sys.path.insert(0, str(Path.home() / "code"))
    try:
        from memory_server import index_conversation
    except Exception as e:
        log(f"FATAL: cannot import memory_server: {e}")
        return 1

    if not PROJECTS.exists():
        log(f"FATAL: {PROJECTS} does not exist")
        return 1

    # Read indexed_convs table to find what's already done
    indexed = set()
    try:
        conn = sqlite3.connect(str(SQLITE))
        for (cid,) in conn.execute("SELECT conv_id FROM indexed_convs"):
            indexed.add(cid)
        conn.close()
    except Exception as e:
        log(f"WARN: cannot read indexed_convs: {e} — will index everything")

    # Walk projects directory
    jsonl_files = sorted(PROJECTS.rglob("*.jsonl"))
    new_count = 0
    skipped = 0
    failed = 0

    log(f"START: scanning {len(jsonl_files)} JSONL files, {len(indexed)} already indexed")

    for jsonl in jsonl_files:
        # Use the JSONL filename (UUID) as the conversation id
        conv_id = jsonl.stem
        if conv_id in indexed:
            skipped += 1
            continue

        # Skip empty files
        if jsonl.stat().st_size == 0:
            skipped += 1
            continue

        try:
            result = index_conversation(conv_id, str(jsonl))
            log(f"INDEXED {conv_id}: {result}")
            new_count += 1
        except Exception as e:
            log(f"FAILED {conv_id}: {type(e).__name__}: {e}")
            failed += 1

    log(f"DONE: indexed {new_count} new, skipped {skipped}, failed {failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
