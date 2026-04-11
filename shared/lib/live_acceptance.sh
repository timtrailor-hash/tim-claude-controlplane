#!/bin/bash
# live_acceptance.sh — post-deploy live-acceptance gate.
#
# After deploy.sh applies changes, this script verifies the system is
# user-visible-healthy:
#   1. Force-runs every critical scheduled job that produces a monitored
#      output artifact (per system_map.yaml user_visible_outputs).
#   2. Stats each artifact, asserts mtime post-dates the deploy start.
#   3. HTTP-probes services declared in system_map.yaml with probe.type=http.
#   4. Returns exit code 0 if all pass, 2 if any fail (advisory in week 1).
#
# Pattern 20 fix: tonight I declared "clean" twice before verifying the
# downstream artifact the iOS app reads was actually refreshed. This gate
# makes that verification mandatory.
#
# Usage:
#   bash live_acceptance.sh <deploy_start_epoch>
#
# Mode:
#   LIVE_ACCEPTANCE_MODE=advisory (default): logs warnings, exits 0
#   LIVE_ACCEPTANCE_MODE=strict: exits 2 on any failure
#
# Runs only on mac-mini (the machine that hosts user-visible monitors).
# On laptop, no-ops and exits 0.

set -uo pipefail

DEPLOY_START="${1:-$(date +%s)}"
MODE="${LIVE_ACCEPTANCE_MODE:-advisory}"
LOG="/tmp/live_acceptance.log"
TS=$(date "+%Y-%m-%d %H:%M:%S")

PASS=0
FAIL=0
WARN=0

log() {
    echo "[$TS] $1" | tee -a "$LOG"
}

check_pass() {
    PASS=$((PASS + 1))
    log "  PASS: $1"
}

check_fail() {
    FAIL=$((FAIL + 1))
    log "  FAIL: $1"
}

check_warn() {
    WARN=$((WARN + 1))
    log "  WARN: $1"
}

# Machine check — laptop has no live-acceptance surface
case "$(hostname -s)" in
    *[Mm]ini*) : ;;
    *)
        log "live_acceptance: skip — not mac-mini"
        exit 0
        ;;
esac

log "=== live_acceptance.sh start (deploy_start=$DEPLOY_START mode=$MODE) ==="

REPO_ROOT="/Users/timtrailor/code/tim-claude-controlplane"
PYTHON="/opt/homebrew/bin/python3.11"

# 1. Force-run the critical monitor (health_check.py) so its output artifact
#    refreshes. This directly addresses Pattern 20.
log "Force-running health_check.py..."
if HEALTH_OUTPUT=$("$PYTHON" /Users/timtrailor/code/health_check.py 2>&1 | tail -3); then
    check_pass "health_check.py ran: $HEALTH_OUTPUT"
else
    check_fail "health_check.py execution failed"
fi

# 2. Verify user-visible outputs are fresh (mtime > deploy_start)
log "Verifying user-visible outputs are fresh..."
UV_OUTPUTS=$(SYSTEM_MAP_MACHINE=mac-mini "$PYTHON" "$REPO_ROOT/shared/lib/system_map.py" user_outputs 2>/dev/null)
if [ -z "$UV_OUTPUTS" ]; then
    check_warn "could not parse user_visible_outputs from system_map.yaml"
else
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        NAME=$(echo "$line" | cut -d'|' -f1)
        PATH_RAW=$(echo "$line" | cut -d'|' -f2)
        PROBE_URL=$(echo "$line" | cut -d'|' -f3)
        REQUIRED=$(echo "$line" | cut -d'|' -f4)
        # Expand ~ in path
        ARTIFACT=$(echo "$PATH_RAW" | sed "s|^~|$HOME|")
        if [ "$ARTIFACT" != "/dev/null" ] && [ -n "$ARTIFACT" ]; then
            if [ -f "$ARTIFACT" ]; then
                MTIME=$(stat -f "%m" "$ARTIFACT" 2>/dev/null || echo 0)
                if [ "$MTIME" -gt "$DEPLOY_START" ]; then
                    check_pass "$NAME: $ARTIFACT fresh (mtime=$MTIME > deploy_start=$DEPLOY_START)"
                elif [ "$REQUIRED" = "required" ]; then
                    AGE=$((DEPLOY_START - MTIME))
                    check_fail "$NAME: $ARTIFACT STALE (${AGE}s older than deploy start, required_on_deploy=true)"
                else
                    AGE=$((DEPLOY_START - MTIME))
                    check_warn "$NAME: $ARTIFACT not refreshed (${AGE}s older than deploy, required_on_deploy=false, OK)"
                fi
            elif [ "$REQUIRED" = "required" ]; then
                check_fail "$NAME: $ARTIFACT missing (required_on_deploy=true)"
            else
                check_warn "$NAME: $ARTIFACT missing (required_on_deploy=false, OK)"
            fi
        fi
        # HTTP probe on the user-visible output if declared
        if [ -n "$PROBE_URL" ]; then
            if curl -s --max-time 5 -o /dev/null -w "%{http_code}" "$PROBE_URL" 2>/dev/null | grep -q "^200$"; then
                check_pass "$NAME: $PROBE_URL returns 200"
            elif [ "$REQUIRED" = "required" ]; then
                check_fail "$NAME: $PROBE_URL did not return 200"
            else
                check_warn "$NAME: $PROBE_URL did not return 200 (optional)"
            fi
        fi
    done <<< "$UV_OUTPUTS"
fi

# 3. Service probes — HTTP probes from system_map.yaml services section
log "Probing services with declared http probes..."
PROBE_RESULT=$(SYSTEM_MAP_MACHINE=mac-mini "$PYTHON" - <<'PYEOF' 2>&1
import sys, urllib.request, urllib.error
sys.path.insert(0, "/Users/timtrailor/code/tim-claude-controlplane/shared/lib")
import system_map as sm
services = sm.services()
for name, entry in services.items():
    if not isinstance(entry, dict):
        continue
    probe = entry.get("probe") or {}
    if probe.get("type") != "http":
        continue
    url = probe.get("url")
    if not url:
        continue
    expect_status = probe.get("expect_status", 200)
    timeout = probe.get("timeout_s", 5)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            if status == expect_status:
                print(f"PASS {name} {url} -> {status}")
            else:
                print(f"FAIL {name} {url} -> {status} (expected {expect_status})")
    except urllib.error.HTTPError as e:
        if e.code == expect_status:
            print(f"PASS {name} {url} -> {e.code}")
        else:
            print(f"FAIL {name} {url} -> HTTP {e.code}")
    except Exception as e:
        print(f"FAIL {name} {url} -> {e}")
PYEOF
)
while IFS= read -r line; do
    [ -z "$line" ] && continue
    if [[ "$line" == PASS* ]]; then
        check_pass "${line#PASS }"
    else
        check_fail "${line#FAIL }"
    fi
done <<< "$PROBE_RESULT"

# 4. Memory ChromaDB semantic probe
log "Probing ChromaDB for non-zero chunks..."
CHROMA_COUNT=$("$PYTHON" - <<'PYEOF' 2>&1
try:
    import chromadb
    from pathlib import Path
    for p in [Path.home() / "code" / "memory_server_data" / "chroma",
              Path.home() / "code" / "memory_server" / "data" / "chroma"]:
        if p.exists():
            c = chromadb.PersistentClient(path=str(p))
            coll = c.get_or_create_collection("conversations", metadata={"hnsw:space": "cosine"})
            print(coll.count())
            break
    else:
        print(0)
except Exception as e:
    print(f"ERR:{e}")
PYEOF
)
if [[ "$CHROMA_COUNT" == ERR:* ]]; then
    check_fail "ChromaDB probe: $CHROMA_COUNT"
elif [ "$CHROMA_COUNT" -ge 1000 ] 2>/dev/null; then
    check_pass "ChromaDB has $CHROMA_COUNT chunks"
elif [ "$CHROMA_COUNT" -gt 0 ] 2>/dev/null; then
    check_warn "ChromaDB has only $CHROMA_COUNT chunks"
else
    check_fail "ChromaDB has 0 chunks"
fi

# 5. Printer Moonraker probe (only if printer reachable — not critical)
log "Probing printer Moonraker..."
if curl -s --max-time 3 http://192.168.0.108:7125/printer/info 2>/dev/null | grep -q "result"; then
    check_pass "Moonraker reachable at 192.168.0.108"
else
    check_warn "Moonraker unreachable (non-critical for deploy)"
fi

log "=== live_acceptance.sh complete: $PASS pass, $FAIL fail, $WARN warn ==="

if [ "$FAIL" -gt 0 ]; then
    if [ "$MODE" = "strict" ]; then
        echo "live_acceptance FAIL (strict mode) — deploy considered failed" >&2
        exit 2
    else
        echo "live_acceptance FAIL (advisory mode) — logged, not blocking" >&2
        exit 0
    fi
fi

exit 0
