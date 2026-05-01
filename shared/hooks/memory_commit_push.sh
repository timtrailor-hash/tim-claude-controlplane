#!/bin/bash
# SessionEnd hook - commit + push any uncommitted changes in the memory repo.
# Replaces the broken inline command that lived in settings.json where variable
# references were stripped (silent no-op since 2026-04-11).
set -uo pipefail

LOG=~/.claude/memory_sync.log
TS=$(date "+%Y-%m-%d %H:%M:%S")

MEM=""
for p in \
    /Users/timtrailor/.claude/projects/-Users-timtrailor-Documents-Claude-code/memory \
    /Users/timtrailor/.claude/projects/-Users-timtrailor-code/memory; do
    if [ -d "$p/.git" ]; then
        MEM="$p"
        break
    fi
done

if [ -z "$MEM" ]; then
    echo "[$TS] COMMIT: no memory repo found" >> "$LOG"
    exit 0
fi

cd "$MEM" || { echo "[$TS] COMMIT: cd failed" >> "$LOG"; exit 0; }
git add -A
if git diff --cached --quiet; then
    echo "[$TS] COMMIT: clean" >> "$LOG"
    exit 0
fi
if git commit -m "Auto-sync: session end" >/dev/null 2>&1; then
    echo "[$TS] COMMIT: ok" >> "$LOG"
    if git push origin main 2>/tmp/claude_git_push.log; then
        echo "[$TS] PUSH: ok" >> "$LOG"
    else
        echo "[$TS] PUSH: failed" >> "$LOG"
        echo "Memory push failed" | curl -s -d @- ntfy.sh/timtrailor-claude 2>/dev/null || true
    fi
else
    echo "[$TS] COMMIT: failed" >> "$LOG"
fi
exit 0
