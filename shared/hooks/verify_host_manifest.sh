#!/bin/bash
# verify_host_manifest.sh — enforce hosts/<host>.yaml against live state.
#
# Audit 2026-04-11 §4.6: "laptop is thin client" was prose, not policy.
# This script loads the appropriate hosts/*.yaml for the current host and
# checks:
#   - Every allowed_launchagents entry may or may not be present (allowlist,
#     not a must-exist). Anything com.timtrailor.* NOT in the allowlist = drift.
#   - Every required_symlinks entry exists and points at the declared target.
#   - Every required_file exists.
#   - Every forbidden_file does NOT exist.
#   - Every required_repo path is a git repo.
#   - Every writable_path is writable by the current user.
#
# Usage:
#   verify_host_manifest.sh                # auto-detect host
#   verify_host_manifest.sh --strict       # also fail on warnings
#
# Exit 0 on clean, 1 on drift. Fires ntfy priority=4 on failure (non-urgent).

set -u

REPO="${CONTROLPLANE_REPO:-$HOME/code/tim-claude-controlplane}"
HOSTS_DIR="$REPO/hosts"

if [ ! -d "$HOSTS_DIR" ]; then
  echo "FAIL: $HOSTS_DIR not found"
  exit 1
fi

HOSTNAME=$(hostname)

# Find the matching manifest by hostname_match glob.
MANIFEST=""
for f in "$HOSTS_DIR"/*.yaml; do
  [ -f "$f" ] || continue
  PATTERN=$(/opt/homebrew/bin/python3.11 -c "import yaml; print(yaml.safe_load(open('$f')).get('hostname_match', ''))" 2>/dev/null)
  [ -z "$PATTERN" ] && continue
  # Bash pattern match (handles trailing *).
  case "$HOSTNAME" in
    $PATTERN) MANIFEST="$f"; break ;;
  esac
done

if [ -z "$MANIFEST" ]; then
  echo "FAIL: no host manifest matched hostname=$HOSTNAME"
  echo "Available manifests:"
  ls "$HOSTS_DIR"/*.yaml 2>&1 | sed 's/^/  /'
  exit 1
fi

echo "Host: $HOSTNAME"
echo "Manifest: $MANIFEST"
echo ""

# Run the check in Python for robust YAML parsing.
/opt/homebrew/bin/python3.11 - "$MANIFEST" "$HOSTNAME" <<'PYEOF'
import os, sys, fnmatch, subprocess, yaml

manifest_path = sys.argv[1]
host = sys.argv[2]
data = yaml.safe_load(open(manifest_path))

failures = []
warnings = []

# 1. LaunchAgents — anything com.timtrailor.* on disk or loaded must be in the allowlist.
allowed = set(data.get("allowed_launchagents") or [])
la_dir = os.path.expanduser("~/Library/LaunchAgents")
if os.path.isdir(la_dir):
    on_disk = set()
    for f in os.listdir(la_dir):
        if f.startswith("com.timtrailor.") and f.endswith(".plist"):
            label = f[:-len(".plist")]
            on_disk.add(label)
    unauthorised = on_disk - allowed
    for label in sorted(unauthorised):
        failures.append(f"unauthorised_launchagent_on_disk: {label}")

# Also check live launchctl list.
try:
    lc = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
    for line in lc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("com.timtrailor."):
            label = parts[2]
            if label not in allowed:
                failures.append(f"unauthorised_launchagent_loaded: {label}")
except Exception as e:
    warnings.append(f"launchctl_list_failed: {e}")

# 2. Required symlinks.
for link_path, spec in (data.get("required_symlinks") or {}).items():
    target = spec.get("target") if isinstance(spec, dict) else spec
    link_path = os.path.expanduser(link_path)
    target = os.path.expanduser(target) if target else ""
    if not os.path.islink(link_path):
        if os.path.exists(link_path):
            failures.append(f"required_symlink_is_not_a_link: {link_path} (real dir/file)")
        else:
            failures.append(f"required_symlink_missing: {link_path}")
        continue
    actual = os.readlink(link_path)
    if actual != target:
        failures.append(f"symlink_wrong_target: {link_path} -> {actual} (expected {target})")

# 3. Required files.
for path in (data.get("required_files") or []):
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        failures.append(f"required_file_missing: {p}")

# 4. Forbidden files.
for path in (data.get("forbidden_files") or []):
    p = os.path.expanduser(path)
    if os.path.exists(p):
        failures.append(f"forbidden_file_present: {p}")

# 5. Required repos.
for entry in (data.get("required_repos") or []):
    p = os.path.expanduser(entry["path"] if isinstance(entry, dict) else entry)
    if not os.path.isdir(os.path.join(p, ".git")):
        failures.append(f"required_repo_missing: {p}")

# 6. Writable paths.
for path in (data.get("writable_paths") or []):
    p = os.path.expanduser(path)
    if not os.access(p, os.W_OK):
        failures.append(f"path_not_writable: {p}")

# Report.
strict = "--strict" in sys.argv
if failures:
    print(f"FAIL: host manifest drift — {len(failures)} issue(s):")
    for m in failures:
        print(f"  {m}")
    for w in warnings:
        print(f"  WARN: {w}")
    sys.exit(1)

if warnings:
    print(f"WARN: {len(warnings)} warning(s):")
    for w in warnings:
        print(f"  {w}")
    if strict:
        sys.exit(1)

print(f"OK: host manifest clean for {host}")
sys.exit(0)
PYEOF
EXIT=$?

if [ "$EXIT" -ne 0 ]; then
  # ntfy at priority=4 — important, not urgent (nightly job, not real-time).
  {
    echo "Host manifest drift on $HOSTNAME"
    echo "Manifest: $MANIFEST"
    echo "Re-run locally: $0"
  } | curl -s --max-time 3 \
      -H "Priority: 4" \
      -H "Title: Host manifest drift" \
      -H "Tags: warning" \
      -d @- ntfy.sh/timtrailor-claude >/dev/null 2>&1 || true
fi

exit $EXIT
