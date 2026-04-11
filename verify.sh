#!/bin/bash
# verify.sh — system verification script
#
# Checks that all referenced hooks, MCPs, launchers exist and are executable.
# Runs critical safety scenario tests if pytest is available.
# Used as a gate by deploy.sh — deployment blocked if verify fails.
#
# Usage:
#   ./verify.sh           # full verify (hooks + scenarios)
#   ./verify.sh --quick   # hooks only (skip scenarios, for speed)

set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
QUICK=0
for arg in "$@"; do
    [ "$arg" = "--quick" ] && QUICK=1
done

PASS=0
FAIL=0
WARN=0

check() {
    local desc="$1"
    local result="$2"  # 0=pass, 1=fail, 2=warn
    if [ "$result" = "0" ]; then
        PASS=$((PASS + 1))
    elif [ "$result" = "2" ]; then
        echo "  WARN: $desc"
        WARN=$((WARN + 1))
    else
        echo "  FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== verify.sh ==="

# 1. Check all hooks referenced in settings.json exist
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
    MISSING_HOOKS=$(python3 - "$SETTINGS" <<PYEOF
import json, sys, os
data = json.load(open(sys.argv[1]))
missing = []
for stage in data.get("hooks", {}).values():
    for entry in stage:
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            for tok in cmd.split():
                if tok.startswith("/") and ("/" in tok[1:]):
                    if not os.path.exists(tok):
                        missing.append(tok)
                    break
for m in data.get("mcpServers", {}).values():
    cmd = m.get("command", "")
    if cmd.startswith("/") and not os.path.exists(cmd):
        missing.append(cmd)
for p in missing:
    print(p)
PYEOF
)
    if [ -z "$MISSING_HOOKS" ]; then
        check "all hook/MCP paths exist in settings.json" "0"
    else
        while IFS= read -r p; do
            check "path exists: $p" "1"
        done <<< "$MISSING_HOOKS"
    fi
else
    check "settings.json exists" "1"
fi

# 2. Check printer safety hook is wired
if grep -q "printer-safety-check" "$SETTINGS" 2>/dev/null; then
    check "printer-safety-check wired in settings.json" "0"
else
    check "printer-safety-check wired in settings.json" "1"
fi

# 3. Check keychain_pass permissions
if [ -f "$HOME/.keychain_pass" ]; then
    PERMS=$(stat -f "%Lp" "$HOME/.keychain_pass")
    if [ "$PERMS" = "600" ]; then
        check "~/.keychain_pass permissions = 600" "0"
    else
        check "~/.keychain_pass permissions = $PERMS (should be 600)" "1"
    fi
fi

# 4. Check critical binaries
for bin in /opt/homebrew/bin/python3.11 /opt/homebrew/bin/ruff /opt/homebrew/bin/semgrep /opt/homebrew/bin/swiftlint; do
    if [ -x "$bin" ]; then
        check "binary exists: $(basename $bin)" "0"
    else
        check "binary exists: $(basename $bin)" "2"
    fi
done

# 5. Memory health (quick functional probe)
if command -v python3.11 >/dev/null 2>&1 || [ -x /opt/homebrew/bin/python3.11 ]; then
    MEM_RESULT=$(/opt/homebrew/bin/python3.11 - <<MEMEOF 2>&1
import sys
try:
    import chromadb
    from pathlib import Path
    for p in [Path.home()/"code"/"memory_server_data"/"chroma", Path.home()/"code"/"memory_server"/"data"/"chroma"]:
        if p.exists():
            c = chromadb.PersistentClient(path=str(p))
            coll = c.get_or_create_collection("conversations", metadata={"hnsw:space":"cosine"})
            count = coll.count()
            if count > 0:
                print(f"OK:{count}")
                sys.exit(0)
    print("EMPTY")
except Exception as e:
    print(f"FAIL:{e}")
MEMEOF
)
    case "$MEM_RESULT" in
        OK:*) check "memory ChromaDB has $(echo $MEM_RESULT | cut -d: -f2) chunks" "0" ;;
        EMPTY) check "memory ChromaDB is empty" "2" ;;
        *) check "memory ChromaDB: $MEM_RESULT" "1" ;;
    esac
fi

# 6. Services manifest exists
if [ -f "$REPO_DIR/machines/mac-mini/services.yaml" ]; then
    check "services.yaml exists" "0"
else
    check "services.yaml exists" "1"
fi

# 6b. Services manifest ↔ plists ↔ health_check.py three-way alignment
#
# Pattern 17 (observability drift): services.yaml is the declared source of
# truth, but downstream consumers (plists, monitors) silently drift. This
# check enforces that all three agree, so the next Phase 4-style deletion
# can't break the monitor.
if command -v /opt/homebrew/bin/python3.11 >/dev/null 2>&1; then
    ALIGN_RESULT=$(/opt/homebrew/bin/python3.11 - "$REPO_DIR" <<'PYEOF' 2>&1
import os, sys, re
from pathlib import Path

repo = Path(sys.argv[1])
svc_yaml = repo / "machines" / "mac-mini" / "services.yaml"
plist_dir = repo / "machines" / "mac-mini" / "launchagents"

# Parse services.yaml services: section (top-level entries under `services:`)
in_services = False
declared = set()
for line in svc_yaml.read_text().splitlines():
    if line.rstrip() == "services:":
        in_services = True
        continue
    if in_services:
        if line and not line.startswith(" "):
            break
        m = re.match(r"^  ([a-z][a-z0-9\-]*):\s*$", line)
        if m:
            declared.add(m.group(1))

# Plist basenames
plists = {p.stem.replace("com.timtrailor.", "")
          for p in plist_dir.glob("com.timtrailor.*.plist")}

# health_check.py LAUNCHAGENTS list (on Mac Mini, via SSH if not local)
hc_path = Path("/Users/timtrailor/code/health_check.py")
if hc_path.exists():
    hc_text = hc_path.read_text()
else:
    import subprocess
    try:
        hc_text = subprocess.check_output(
            ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
             "timtrailor@192.168.0.172", "cat ~/code/health_check.py"],
            text=True, timeout=10)
    except Exception:
        hc_text = ""

monitored = set()
if hc_text:
    m = re.search(r"LAUNCHAGENTS\s*=\s*\[(.*?)\]", hc_text, re.DOTALL)
    if m:
        monitored = set(re.findall(r"com\.timtrailor\.([a-z][a-z0-9\-]*)", m.group(1)))

issues = []
if declared != plists:
    issues.append(f"services.yaml vs plists: only_in_yaml={sorted(declared-plists)} only_in_plists={sorted(plists-declared)}")
if monitored and declared != monitored:
    issues.append(f"services.yaml vs health_check.py LAUNCHAGENTS: only_in_yaml={sorted(declared-monitored)} only_in_monitor={sorted(monitored-declared)}")
if not monitored:
    issues.append("could not parse health_check.py LAUNCHAGENTS (not reachable?)")

for i in issues:
    print(i)
PYEOF
)
    if [ -z "$ALIGN_RESULT" ]; then
        check "services.yaml ↔ plists ↔ health_check.py aligned" "0"
    else
        while IFS= read -r line; do
            [ -n "$line" ] && check "alignment drift: $line" "1"
        done <<< "$ALIGN_RESULT"
    fi
fi

# 7. Run pytest scenarios (unless --quick)
if [ "$QUICK" = "0" ] && [ -d "$REPO_DIR/scenarios" ]; then
    if /opt/homebrew/bin/python3.11 -m pytest --version 2>/dev/null >/dev/null 2>&1; then
        echo "  Running safety scenarios..."
        if /opt/homebrew/bin/python3.11 -m pytest "$REPO_DIR/scenarios/" -q --tb=short 2>&1 | tail -5; then
            check "safety scenarios" "0"
        else
            check "safety scenarios" "1"
        fi
    else
        check "pytest not installed (scenarios skipped)" "2"
    fi
fi

echo
echo "Results: $PASS passed, $FAIL failed, $WARN warnings"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
