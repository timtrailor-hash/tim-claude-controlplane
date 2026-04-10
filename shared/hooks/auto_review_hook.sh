#!/bin/bash
# SessionEnd hook — leaves a tripwire entry in ~/.claude/auto_review.log
# if the session touched substantive code (>20 added lines in .py/.swift/.sh
# files) in any git repo it can find. Does NOT spawn an LLM.
#
# Dedup: uses `git rev-parse --show-toplevel` so overlapping cwd paths don't
# produce duplicate entries for the same repo.
#
# Counts ADDITIONS only (not deletions) to avoid double-counting refactors.

set -uo pipefail

LOG=~/.claude/auto_review.log
THRESHOLD=20

PWD_NOW=$(pwd)
SEEN=""

for dir in "$PWD_NOW" "/Users/timtrailor/Documents/Claude code" "/Users/timtrailor/code"; do
    [ -d "$dir/.git" ] || continue
    cd "$dir" 2>/dev/null || continue

    TOPLEVEL=$(git rev-parse --show-toplevel 2>/dev/null)
    [ -z "$TOPLEVEL" ] && continue

    # Dedupe by canonical toplevel
    case " $SEEN " in
        *" $TOPLEVEL "*) continue ;;
    esac
    SEEN="$SEEN $TOPLEVEL"

    # Count ADDED lines only (column 1 of numstat); skip binary (-) and noisy paths
    CHANGED=$(git diff HEAD --numstat 2>/dev/null | \
        awk '$1 != "-" && $3 ~ /\.(py|swift|sh|ts|js)$/ && $3 !~ /(venv|node_modules|__pycache__|\.build|DerivedData|Pods)/ {sum += $1} END {print sum+0}')

    [ "$CHANGED" -lt "$THRESHOLD" ] && continue

    TS=$(date "+%Y-%m-%d %H:%M:%S")
    {
        echo "=== $TS — auto-review tripwire fired in $TOPLEVEL ==="
        echo "Added lines: $CHANGED (threshold: $THRESHOLD)"
        echo "Files:"
        git diff HEAD --name-only 2>/dev/null | head -20
        echo ""
        echo "Run /review in next session for full code-reviewer subagent verdict."
        echo ""
    } >> "$LOG"
done

exit 0
