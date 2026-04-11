#!/bin/bash
# system_inventory.sh — scan the live Mac Mini environment and diff
# against system_map.yaml. Surfaces undeclared components and orphaned
# artifacts. Runnable standalone; not scheduled as cron by default.
#
# This is the "automated archaeology" Gemini proposed in the state-
# assessment debate: periodic discovery work that makes the system
# visible to itself.
#
# Usage:
#   bash system_inventory.sh          # scan, print report
#   bash system_inventory.sh --json   # machine-readable
#
# What it scans:
#   - All loaded LaunchAgents (launchctl list)
#   - Running Python processes owned by timtrailor
#   - All TCP listeners on 127.0.0.1 / 0.0.0.0
#   - Cron jobs for the current user
#   - Stray .git directories (catches repo-identity ambiguity)
#   - Files in /tmp that look like status artifacts
#   - LaunchAgent plist files in ~/Library/LaunchAgents/
#
# For each category, compare against system_map.yaml's declared set.
# Output:
#   - KNOWN: in live AND in map
#   - UNDECLARED: in live but NOT in map (investigate — may be legitimate but undocumented)
#   - MISSING: in map but NOT in live (investigate — declared but not running)
#   - ORPHAN: in live, clearly ours, not in any category

set -uo pipefail

JSON_MODE=0
for arg in "$@"; do
    [ "$arg" = "--json" ] && JSON_MODE=1
done

REPO_ROOT="/Users/timtrailor/code/tim-claude-controlplane"
PYTHON="/opt/homebrew/bin/python3.11"

# Require Mac Mini — this scan is about Mac Mini's live state
case "$(hostname -s)" in
    *[Mm]ini*) : ;;
    *)
        echo "system_inventory: must be run on Mac Mini (not $(hostname -s))" >&2
        exit 0
        ;;
esac

# Pull declared sets from system_map.yaml
DECLARED_LABELS=$(SYSTEM_MAP_MACHINE=mac-mini "$PYTHON" "$REPO_ROOT/shared/lib/system_map.py" labels 2>/dev/null | sort -u)
DECLARED_PATHS=$(SYSTEM_MAP_MACHINE=mac-mini "$PYTHON" "$REPO_ROOT/shared/lib/system_map.py" paths 2>/dev/null | cut -d= -f2 | sort -u)

# Live LaunchAgents (com.timtrailor.* only)
LIVE_LABELS=$(launchctl list 2>/dev/null | awk '{print $3}' | grep "^com.timtrailor\." | sort -u)

# Plist files on disk
PLIST_FILES=$(ls ~/Library/LaunchAgents/com.timtrailor.*.plist 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's|\.plist$||' | sort -u)

# Running Python processes
PY_PROCS=$(pgrep -af "timtrailor.*python3" 2>/dev/null || true)

# Listeners (Tim's user only)
LISTENERS=$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk '$3 == "timtrailor" {print $9}' | sort -u)

# Cron jobs
CRON_JOBS=$(crontab -l 2>/dev/null | grep -v "^#" | grep -v "^$" || true)

# Stray .git dirs: only flag those WITHOUT a .git/config file (the broken ones
# that cause git -C to resolve to a parent repo). A valid .git/config means
# it's a real clone even if it's in an unexpected place.
STRAY_GITS=""
while IFS= read -r gitdir; do
    [ -z "$gitdir" ] && continue
    if [ ! -f "$gitdir/config" ]; then
        STRAY_GITS="$STRAY_GITS$gitdir (no .git/config — orphaned)
"
    fi
done < <(find ~/.claude/projects -maxdepth 4 -name ".git" -type d 2>/dev/null)

# Status artifacts in /tmp
STATUS_ARTIFACTS=$(ls -la /tmp/*.json /tmp/*_status* /tmp/health_* /tmp/printer_* 2>/dev/null | grep -v "^total")

# --- Diff logic ---
MISSING_FROM_LIVE=""
UNDECLARED_IN_LIVE=""

# LaunchAgents: map vs live
while IFS= read -r declared; do
    [ -z "$declared" ] && continue
    if ! echo "$LIVE_LABELS" | grep -qx "$declared"; then
        MISSING_FROM_LIVE="$MISSING_FROM_LIVE$declared
"
    fi
done <<< "$DECLARED_LABELS"

while IFS= read -r live; do
    [ -z "$live" ] && continue
    if ! echo "$DECLARED_LABELS" | grep -qx "$live"; then
        UNDECLARED_IN_LIVE="$UNDECLARED_IN_LIVE$live
"
    fi
done <<< "$LIVE_LABELS"

# Plist files vs map
MISSING_FROM_DISK=""
UNDECLARED_ON_DISK=""
while IFS= read -r declared; do
    [ -z "$declared" ] && continue
    short=$(echo "$declared" | sed 's|^com\.timtrailor\.||')
    if ! echo "$PLIST_FILES" | grep -qx "com.timtrailor.$short"; then
        MISSING_FROM_DISK="$MISSING_FROM_DISK$declared
"
    fi
done <<< "$DECLARED_LABELS"

while IFS= read -r plist; do
    [ -z "$plist" ] && continue
    if ! echo "$DECLARED_LABELS" | grep -qx "$plist"; then
        UNDECLARED_ON_DISK="$UNDECLARED_ON_DISK$plist
"
    fi
done <<< "$PLIST_FILES"

# --- Output ---
if [ "$JSON_MODE" = "1" ]; then
    cat <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "declared_labels": $(echo "$DECLARED_LABELS" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "live_labels": $(echo "$LIVE_LABELS" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "missing_from_live": $(echo "$MISSING_FROM_LIVE" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "undeclared_in_live": $(echo "$UNDECLARED_IN_LIVE" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "missing_from_disk": $(echo "$MISSING_FROM_DISK" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "undeclared_on_disk": $(echo "$UNDECLARED_ON_DISK" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "stray_gits": $(echo "$STRAY_GITS" | python3 -c "import sys, json; print(json.dumps([l for l in sys.stdin.read().splitlines() if l]))"),
  "cron_job_count": $(echo "$CRON_JOBS" | grep -cv '^$' || echo 0)
}
EOF
    exit 0
fi

# Human-readable report
cat <<EOF
=== system_inventory ($(date "+%Y-%m-%d %H:%M:%S")) ===

## LaunchAgents
EOF

DECLARED_COUNT=$(echo "$DECLARED_LABELS" | grep -cv '^$')
LIVE_COUNT=$(echo "$LIVE_LABELS" | grep -cv '^$')
echo "  declared (system_map.yaml): $DECLARED_COUNT"
echo "  live (launchctl list):       $LIVE_COUNT"

if [ -n "$(echo "$MISSING_FROM_LIVE" | grep -v '^$')" ]; then
    echo ""
    echo "  ⚠ declared but NOT loaded (MISSING):"
    echo "$MISSING_FROM_LIVE" | grep -v '^$' | sed 's|^|    - |'
fi

if [ -n "$(echo "$UNDECLARED_IN_LIVE" | grep -v '^$')" ]; then
    echo ""
    echo "  ⚠ loaded but NOT declared (UNDECLARED):"
    echo "$UNDECLARED_IN_LIVE" | grep -v '^$' | sed 's|^|    + |'
fi

echo ""
echo "## Plist files on disk"
PLIST_COUNT=$(echo "$PLIST_FILES" | grep -cv '^$')
echo "  plist files in ~/Library/LaunchAgents/: $PLIST_COUNT"

if [ -n "$(echo "$UNDECLARED_ON_DISK" | grep -v '^$')" ]; then
    echo ""
    echo "  ⚠ plist files NOT in system_map (UNDECLARED):"
    echo "$UNDECLARED_ON_DISK" | grep -v '^$' | sed 's|^|    + |'
fi

if [ -n "$(echo "$MISSING_FROM_DISK" | grep -v '^$')" ]; then
    echo ""
    echo "  ⚠ declared but no plist on disk (MISSING):"
    echo "$MISSING_FROM_DISK" | grep -v '^$' | sed 's|^|    - |'
fi

echo ""
echo "## Stray .git directories"
if [ -n "$STRAY_GITS" ]; then
    STRAY_COUNT=$(echo "$STRAY_GITS" | grep -cv '^$')
    echo "  found $STRAY_COUNT:"
    echo "$STRAY_GITS" | sed 's|^|    |'
    echo ""
    echo "  Note: any .git without a matching .git/config file is orphaned and"
    echo "  may corrupt git-aware tools (like drift_check.sh). Investigate."
else
    echo "  none"
fi

echo ""
echo "## Listeners (Tim's user)"
LISTEN_COUNT=$(echo "$LISTENERS" | grep -cv '^$')
echo "  $LISTEN_COUNT active listeners"
if [ -n "$LISTENERS" ]; then
    echo "$LISTENERS" | head -10 | sed 's|^|    |'
fi

echo ""
echo "## Cron jobs"
CRON_COUNT=$(echo "$CRON_JOBS" | grep -cv '^$')
echo "  $CRON_COUNT cron jobs"
if [ -n "$CRON_JOBS" ]; then
    echo "$CRON_JOBS" | head -10 | sed 's|^|    |'
fi

echo ""
echo "## /tmp status artifacts"
if [ -n "$STATUS_ARTIFACTS" ]; then
    echo "$STATUS_ARTIFACTS" | head -8 | awk '{printf "    %s  %s %s %s  %s\n", $9, $6, $7, $8, $5}'
else
    echo "  none"
fi

echo ""
echo "=== end inventory ==="

# Non-zero exit if any discrepancy
if [ -n "$(echo "$MISSING_FROM_LIVE$UNDECLARED_IN_LIVE$MISSING_FROM_DISK$UNDECLARED_ON_DISK$STRAY_GITS" | grep -v '^$')" ]; then
    exit 1
fi
exit 0
