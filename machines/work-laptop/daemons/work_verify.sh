#!/bin/bash
# work_verify.sh — work-laptop verification entrypoint (Slice N).
#
# Behaviour:
#   * Gates on ~/.claude/.work-laptop marker. If absent, exit 0 silently —
#     this script is a no-op on personal machines so it can ship in the
#     shared controlplane repo without firing anywhere it shouldn't.
#   * Runs work_acceptance_tests.py (Pattern-3: each test EXERCISES the
#     real feature — feeding hook payloads, querying ChromaDB, probing
#     the live gateway — not just file-existence pokes).
#   * Tails /tmp/work_acceptance_results.json for the summary counts and
#     prints a one-line "VERIFY: g/total green, a amber, r red".
#   * Exit codes: 0 if no red, 1 if amber-only, 2 if any red.
#   * Logs run output to /tmp/work_verify.log (append; rotated by macOS
#     /tmp clean-up policy on reboot).
#
# Why this lives here and not under shared/: the Mac Mini has its own
# verify.sh, its own acceptance-tests LaunchAgent, and its own
# /tmp/acceptance_results.json. Mixing the two would give one verify
# pass/fail signal that doesn't correspond to any single machine's
# health. machines/work-laptop/daemons/ is the one canonical place
# work-laptop verification lives.

set -uo pipefail

MARKER="$HOME/.claude/.work-laptop"
LOG="/tmp/work_verify.log"
RESULTS="/tmp/work_acceptance_results.json"

# Resolve directory of this script so the .py probe can be found whether
# we're running from the controlplane checkout or a deployed copy.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE="$SCRIPT_DIR/work_acceptance_tests.py"

ts() { date "+%Y-%m-%d %H:%M:%S"; }

log() {
    # Always print to stderr so cron output / launchctl logs capture it,
    # AND append to /tmp/work_verify.log so a developer running by hand
    # can `tail -f` it.
    echo "[$(ts)] $*" | tee -a "$LOG" >&2
}

# ── Gate: skip silently on non-work machines ─────────────────────────────
# The controlplane repo is shared. work_setup.sh on the work laptop drops
# ~/.claude/.work-laptop. Personal Mac Mini and personal MacBook Pro do
# NOT have that marker, so this script becomes a no-op there.
if [ ! -f "$MARKER" ]; then
    # Don't even log — this fires from cron / SessionStart on every
    # machine and we don't want to spam personal logs with noise.
    exit 0
fi

log "verify start"

# ── Sanity: does the probe script exist? ─────────────────────────────────
if [ ! -f "$PROBE" ]; then
    log "FATAL: $PROBE not found — was deploy.sh run on this machine?"
    echo "VERIFY: 0/0 green, 0 amber, 1 red"
    exit 2
fi

# ── Pick a Python interpreter ────────────────────────────────────────────
# Same dual-path-or pattern as elsewhere in the controlplane: we prefer
# Homebrew's 3.11 because that's what FastMCP/chromadb were installed
# against, but fall back to whatever python3 is on PATH.
PY_BIN=""
for cand in /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY_BIN="$cand"
        break
    fi
done

if [ -z "$PY_BIN" ]; then
    log "FATAL: no python3 interpreter on PATH"
    echo "VERIFY: 0/0 green, 0 amber, 1 red"
    exit 2
fi

# ── Run the probe ────────────────────────────────────────────────────────
# work_acceptance_tests.py is responsible for catching its own per-test
# exceptions — a single test failing must NOT crash the suite. We still
# capture stderr to the log for any catastrophic failure (e.g. import
# error) and surface a synthetic red result.
PROBE_OUT=$("$PY_BIN" "$PROBE" 2>&1)
PROBE_RC=$?
echo "$PROBE_OUT" >> "$LOG"

if [ "$PROBE_RC" -ne 0 ] && [ ! -f "$RESULTS" ]; then
    log "probe exited rc=$PROBE_RC and produced no results JSON — treating as red"
    echo "VERIFY: 0/0 green, 0 amber, 1 red"
    exit 2
fi

# ── Tail the JSON for the summary line ───────────────────────────────────
# Use python3 to parse rather than jq (which the work laptop may not have
# pre-installed). Defensive: if any field is missing we treat the whole
# run as red so the operator notices.
SUMMARY_LINE=$("$PY_BIN" - "$RESULTS" <<'PYEOF' 2>>"$LOG"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
except Exception as exc:
    print(f"PARSE_FAIL:{exc}")
    sys.exit(0)

summary = data.get("summary") or {}
total = int(summary.get("total", 0))
green = int(summary.get("green", 0))
amber = int(summary.get("amber", 0))
red = int(summary.get("red", 0))
# Format: "VERIFY: g/total green, a amber, r red" — matches the spec line
# in slice N exactly so dashboards / grep pipelines can rely on it.
print(f"OK:{green}:{total}:{amber}:{red}")
PYEOF
)

case "$SUMMARY_LINE" in
    OK:*)
        IFS=":" read -r _ G T A R <<< "$SUMMARY_LINE"
        echo "VERIFY: ${G}/${T} green, ${A} amber, ${R} red"
        log "summary green=$G total=$T amber=$A red=$R"
        if [ "${R:-1}" -gt 0 ]; then
            exit 2
        fi
        if [ "${A:-0}" -gt 0 ]; then
            exit 1
        fi
        exit 0
        ;;
    PARSE_FAIL:*)
        log "could not parse $RESULTS (${SUMMARY_LINE#PARSE_FAIL:})"
        echo "VERIFY: 0/0 green, 0 amber, 1 red"
        exit 2
        ;;
    *)
        log "unexpected summary output: $SUMMARY_LINE"
        echo "VERIFY: 0/0 green, 0 amber, 1 red"
        exit 2
        ;;
esac
