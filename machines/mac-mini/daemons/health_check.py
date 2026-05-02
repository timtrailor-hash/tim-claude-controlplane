#!/usr/bin/env python3
"""
Comprehensive health check — runs every 4 hours via LaunchAgent.
Checks: all LaunchAgents, services, printers, backups, disk, git repos,
        credentials, memory DB, cross-machine code drift.
Alerts via ntfy + email on FAIL/WARN only. Silence = healthy.
Writes results to /tmp/health_check_results.json for iOS dashboard.
"""

import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────

HOME = Path.home()
CODE = HOME / "code"

# All LaunchAgents that should be loaded on the Mac Mini.
# SOURCE OF TRUTH: tim-claude-controlplane/machines/mac-mini/system_map.yaml services: section.
# Loaded at runtime via shared/lib/system_map.py. _FALLBACK_LAUNCHAGENTS is a
# safety net only — any divergence between fallback and map is caught by
# verify.sh 6b on every deploy. Pattern 17 structural fix: this list is no
# longer authoritative, the map is.
_FALLBACK_LAUNCHAGENTS = [
    "com.timtrailor.conversation-server",
    "com.timtrailor.printer-snapshots",
    "com.timtrailor.governors",
    "com.timtrailor.streamlit-https",
    "com.timtrailor.backup-to-drive",
    "com.timtrailor.token-refresh",
    "com.timtrailor.ttyd-tunnel",
    "com.timtrailor.unlock-keychain",
    "com.timtrailor.health-check",
    "com.timtrailor.bgt-date-monitor",
    "com.timtrailor.ci-failure-poller",
    "com.timtrailor.governorhub-sync",
    "com.timtrailor.acceptance-tests",
    "com.timtrailor.trend-tracker",
    "com.timtrailor.credential-rotation",
    "com.timtrailor.stale-pr-alert",
]


# Tracks whether the fallback was used on load, so check_launchagents
# can emit a loud WARN result and the iOS app / ntfy see it.
_FALLBACK_USED = False
_FALLBACK_REASON = ""


def _load_launchagents_from_system_map():
    global _FALLBACK_USED, _FALLBACK_REASON
    try:
        import os as _os
        import sys as _sys
        _sys.path.insert(
            0,
            "/Users/timtrailor/code/tim-claude-controlplane/shared/lib",
        )
        _os.environ.setdefault("SYSTEM_MAP_MACHINE", "mac-mini")
        import system_map as _sm
        # Also validate the map we just loaded. A malformed map must NOT
        # silently fall back — it must raise a loud warning.
        sm = _sm.load()
        issues = _sm.validate(sm)
        if issues:
            _FALLBACK_USED = True
            _FALLBACK_REASON = f"schema_invalid: {len(issues)} issue(s); first: {issues[0]}"
            return _FALLBACK_LAUNCHAGENTS
        labels = _sm.service_labels()
        if labels and isinstance(labels, list) and len(labels) >= 8:
            return labels
        _FALLBACK_USED = True
        _FALLBACK_REASON = f"service_labels returned {labels!r}"
    except Exception as _e:
        import sys as _sys
        print(
            f"[health_check] WARN: could not load system_map.yaml: {_e}",
            file=_sys.stderr,
        )
        _FALLBACK_USED = True
        _FALLBACK_REASON = f"exception: {_e}"
    return _FALLBACK_LAUNCHAGENTS


LAUNCHAGENTS = _load_launchagents_from_system_map()

GIT_REPOS = [
    CODE / "claude-mobile",
    CODE / "sv08-print-tools",
    CODE / "ofsted-agent",
]

BACKUP_MANIFEST = CODE / ".backup_manifest.json"
BACKUP_MAX_AGE_HOURS = 48

DISK_WARN_PCT = 80
DISK_FAIL_PCT = 90

REQUIRED_FILES = [
    (CODE / "credentials.py", "Master credentials — copy from laptop backup"),
    (CODE / "claude-mobile" / "google_token.json",
     "Google OAuth token — run: cd ~/code/claude-mobile && python3 google_auth_setup.py"),
    (CODE / "claude-mobile" / "google_credentials.json",
     "Google OAuth client creds — copy from GCP console or backup"),
]

# Printers
SV08_MOONRAKER = "http://192.168.0.108:7125/printer/info"
BAMBU_A1_IP = "192.168.0.214"
BAMBU_A1_PORT = 8883

# Cross-machine code drift — Mac Mini is canonical, laptop gets synced
LAPTOP_SSH = "timtrailor@100.112.125.42"
SSH_OPTS = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no"]
SHARED_FILES = {
    # mac_mini_path: laptop_path
    "health_check.py": "Documents/Claude code/health_check.py",
    "backup_to_drive.py": "Documents/Claude code/backup_to_drive.py",
    "shared_utils.py": "Documents/Claude code/shared_utils.py",
    "mac_mini_health_monitor.sh": "code/mac_mini_health_monitor.sh",
}

# Healthchecks.io dead-man's-switch (set in credentials.py)
try:
    sys.path.insert(0, str(CODE))
    from credentials import NTFY_TOPIC
except ImportError:
    NTFY_TOPIC = "timtrailor-claude"

try:
    from credentials import HEALTHCHECKS_PING_URL
except ImportError:
    HEALTHCHECKS_PING_URL = None

# ── Result tracking ───────────────────────────────────────────────────────

results = []

def add(name, status, detail):
    results.append((name, status, detail))

# ── Checks ─────────────────────────────────────────────────────────────────

def check_launchagents():
    # Loud alarm if we fell back to the hardcoded list — the iOS app
    # surfaces this as a WARN so Tim knows the authority map is broken.
    if _FALLBACK_USED:
        add(
            "system_map:authority",
            "WARN",
            f"FELL BACK to hardcoded LAUNCHAGENTS list ({_FALLBACK_REASON})",
        )

    """Verify each LaunchAgent is loaded and healthy."""
    for label in LAUNCHAGENTS:
        short = label.split(".")[-1]
        try:
            raw = subprocess.check_output(
                ["launchctl", "list", label], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            add(f"launchd:{short}", "FAIL", "not loaded")
            continue

        pid = None
        exit_code = None
        for line in raw.splitlines():
            if '"PID"' in line:
                pid = line.split("=")[-1].strip().rstrip(";").strip()
            if '"LastExitStatus"' in line:
                exit_code = line.split("=")[-1].strip().rstrip(";").strip()

        if pid and pid != "-":
            add(f"launchd:{short}", "PASS", f"PID {pid}")
        elif exit_code == "0":
            add(f"launchd:{short}", "PASS", "idle (scheduled)")
        elif short == "acceptance-tests":
            # Special case: acceptance_tests.py deliberately exits 1 when any
            # test FAILs (it's a result-reporting script, not a daemon). The
            # compliance-regression trend channel surfaces those separately.
            # Use the freshness of /tmp/acceptance_results.json to distinguish
            # "ran and reported regressions" (PASS here) from "daemon crashed"
            # (FAIL). Schedule is every 2h, so anything <4h old = PASS.
            try:
                import os as _os
                import time as _time
                results_path = "/tmp/acceptance_results.json"
                if _os.path.exists(results_path):
                    age_h = (_time.time() - _os.path.getmtime(results_path)) / 3600
                    if age_h < 4:
                        add(f"launchd:{short}", "PASS",
                            f"idle (last results {age_h:.1f}h old, exit {exit_code})")
                    else:
                        add(f"launchd:{short}", "FAIL",
                            f"results stale {age_h:.1f}h, exit {exit_code}")
                else:
                    add(f"launchd:{short}", "FAIL",
                        f"no results file, exit {exit_code}")
            except Exception as _exc:
                add(f"launchd:{short}", "FAIL",
                    f"couldn't stat results file: {_exc}")
        else:
            add(f"launchd:{short}", "FAIL", f"not running, last exit {exit_code}")


def check_services():
    """Check HTTP service endpoints."""
    # Conversation server
    try:
        with urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=5) as r:
            data = json.loads(r.read())
        if data.get("ok"):
            uptime = data.get("uptime_s", 0)
            threads = len(data.get("thread_health", {}))
            add("service:conversation_server", "PASS",
                f"uptime {uptime}s, {threads} threads")
        else:
            add("service:conversation_server", "WARN", "responded but ok=false")
    except Exception as e:
        add("service:conversation_server", "FAIL", f"unreachable: {e}")

    # Streamlit governors
    try:
        with urllib.request.urlopen("http://127.0.0.1:8501/healthz", timeout=5) as r:
            add("service:streamlit", "PASS", f"HTTP {r.status}")
    except Exception as e:
        add("service:streamlit", "WARN", f"not responding: {e}")


def check_printers():
    """Check printer reachability (no commands sent — read-only).

    Uses curl/nc subprocesses instead of urllib/socket because macOS 15
    Local Network privacy silently denies homebrew Python outbound to
    192.168.x.x. curl (Apple-signed) and nc inherit the LaunchAgent's
    permission; raw Python sockets do not.
    """
    # SV08 Max via Moonraker — curl subprocess
    try:
        out = subprocess.check_output(
            ["curl", "-s", "-m", "5", SV08_MOONRAKER],
            text=True, stderr=subprocess.DEVNULL, timeout=8,
        )
        data = json.loads(out)
        state = data.get("result", {}).get("state", "unknown")
        add("printer:sv08", "PASS", f"Moonraker OK, state={state}")
    except Exception as e:
        add("printer:sv08", "WARN", f"unreachable: {e}")

    # Bambu A1 via TCP connect (MQTT port) — nc -z subprocess
    try:
        subprocess.run(
            ["nc", "-z", "-w", "5", BAMBU_A1_IP, str(BAMBU_A1_PORT)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=8,
        )
        add("printer:bambu_a1", "PASS", f"TCP {BAMBU_A1_IP}:{BAMBU_A1_PORT} open")
    except Exception as e:
        add("printer:bambu_a1", "WARN", f"unreachable: {e}")


def check_backups():
    """Check backup manifest recency."""
    if not BACKUP_MANIFEST.exists():
        old = HOME / "projects" / "claude" / ".backup_manifest.json"
        if old.exists():
            add("backup:manifest", "WARN",
                f"manifest at old path {old} — update CODE_DIR")
            return
        add("backup:manifest", "WARN", "manifest not found")
        return

    try:
        manifest = json.loads(BACKUP_MANIFEST.read_text())
    except Exception as e:
        add("backup:manifest", "FAIL", f"could not parse: {e}")
        return

    last_str = manifest.get("last_backup")
    if not last_str:
        add("backup:recency", "WARN", "no last_backup timestamp")
        return

    try:
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        count = len(manifest.get("files", {}))
        if age_h > BACKUP_MAX_AGE_HOURS:
            add("backup:recency", "WARN",
                f"last backup {age_h:.1f}h ago (threshold {BACKUP_MAX_AGE_HOURS}h)")
        else:
            add("backup:recency", "PASS", f"{age_h:.1f}h ago, {count} files")
    except Exception as e:
        add("backup:recency", "FAIL", f"timestamp parse error: {e}")


def check_disk():
    """Check disk usage percentage."""
    try:
        result = subprocess.check_output(["df", "-h", "/"], text=True)
        parts = result.splitlines()[1].split()
        pct = int(parts[4].rstrip("%"))
        avail = parts[3]
        if pct >= DISK_FAIL_PCT:
            add("disk:usage", "FAIL", f"{pct}% used ({avail} free)")
        elif pct >= DISK_WARN_PCT:
            add("disk:usage", "WARN", f"{pct}% used ({avail} free)")
        else:
            add("disk:usage", "PASS", f"{pct}% used ({avail} free)")
    except Exception as e:
        add("disk:usage", "FAIL", f"could not check: {e}")


def check_git_repos():
    """Verify git repos exist and report uncommitted changes."""
    for repo in GIT_REPOS:
        name = repo.name
        if not repo.exists():
            add(f"git:{name}", "FAIL", "directory not found")
            continue
        if not (repo / ".git").exists():
            add(f"git:{name}", "FAIL", "not a git repo")
            continue
        try:
            dirty = subprocess.check_output(
                ["git", "-C", str(repo), "status", "--short"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            if dirty:
                lines = len(dirty.splitlines())
                add(f"git:{name}", "WARN", f"{lines} uncommitted change(s)")
            else:
                add(f"git:{name}", "PASS", "clean")
        except Exception as e:
            add(f"git:{name}", "WARN", f"git status failed: {e}")


def check_printer_daemon():
    """Check printer daemon status file freshness."""
    status_file = Path("/tmp/printer_status/status.json")
    if not status_file.exists():
        add("printer:daemon", "WARN", "no status.json — daemon may not have polled yet")
        return
    try:
        age_s = datetime.now().timestamp() - status_file.stat().st_mtime
        age_min = age_s / 60
        data = json.loads(status_file.read_text())
        state = data.get("print_stats", {}).get("state", "unknown")
        if age_min > 10:
            add("printer:daemon", "WARN",
                f"stale ({age_min:.0f}min old, state={state})")
        else:
            add("printer:daemon", "PASS",
                f"{age_min:.1f}min old, state={state}")
    except Exception as e:
        add("printer:daemon", "WARN", f"could not read: {e}")


def check_required_files():
    """Verify critical credential/token files exist."""
    for path, remediation in REQUIRED_FILES:
        if path.exists():
            age_days = (datetime.now().timestamp() - path.stat().st_mtime) / 86400
            add(f"file:{path.name}", "PASS", f"{age_days:.0f}d old")
        else:
            add(f"file:{path.name}", "FAIL", f"MISSING — {remediation}")


def check_memory_db():
    """Functional probe of ChromaDB — verifies actual search works (Pattern 3)."""
    chroma_dir = CODE / "memory_server_data" / "chroma"
    if not chroma_dir.exists():
        add("memory:chromadb", "WARN", "chroma dir not found")
        return

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/python3.11", "-c", """
import chromadb, sys
from pathlib import Path
chroma_dir = Path.home() / "code" / "memory_server_data" / "chroma"
client = chromadb.PersistentClient(path=str(chroma_dir))
coll = client.get_or_create_collection(name="conversations", metadata={"hnsw:space": "cosine"})
count = coll.count()
if count == 0:
    print(f"WARN:0 chunks")
    sys.exit(0)
res = coll.query(query_texts=["printer safety"], n_results=3)
n_hits = len(res.get("ids", [[]])[0]) if res.get("ids") else 0
print(f"OK:{count} chunks, {n_hits} hits")
"""],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        if output.startswith("OK:"):
            add("memory:chromadb", "PASS", output[3:])
        elif output.startswith("WARN:"):
            add("memory:chromadb", "WARN", output[5:])
        else:
            add("memory:chromadb", "FAIL", result.stderr.strip()[:100] or "unknown error")
    except subprocess.TimeoutExpired:
        add("memory:chromadb", "WARN", "ChromaDB query timed out (30s)")
    except Exception as e:
        add("memory:chromadb", "FAIL", f"{e}")


def check_code_divergence():
    """Compare shared files between Mac Mini (canonical) and laptop.
    Auto-sync Mac Mini → laptop when Mac Mini is newer.
    Alert when laptop is unexpectedly newer."""

    # First check if laptop is reachable
    try:
        subprocess.run(
            ["ssh"] + SSH_OPTS + [LAPTOP_SSH, "echo ok"],
            capture_output=True, timeout=10,
        ).check_returncode()
    except Exception:
        # Laptop unreachable (asleep, away) — skip silently, this is normal
        add("drift:laptop", "PASS", "laptop unreachable (expected when away)")
        return

    drifted = []
    synced = []

    for mac_file, laptop_rel in SHARED_FILES.items():
        mac_path = CODE / mac_file
        laptop_path = f"/Users/timtrailor/{laptop_rel}"

        if not mac_path.exists():
            continue

        try:
            # Get Mac Mini hash
            mac_hash = hashlib.md5(mac_path.read_bytes()).hexdigest()[:12]

            # Get laptop hash
            laptop_hash_raw = subprocess.check_output(
                ["ssh"] + SSH_OPTS + [LAPTOP_SSH, f"md5 -q '{laptop_path}' 2>/dev/null"],
                text=True, timeout=10,
            ).strip()[:12]

            if not laptop_hash_raw:
                # File doesn't exist on laptop — sync it
                subprocess.run(
                    ["scp"] + SSH_OPTS[:-1] + [str(mac_path), f"{LAPTOP_SSH}:{laptop_path}"],
                    capture_output=True, timeout=30,
                )
                synced.append(mac_file)
                continue

            if mac_hash == laptop_hash_raw:
                continue  # identical, no action

            # Files differ — check which is newer
            mac_mtime = mac_path.stat().st_mtime
            laptop_mtime_raw = subprocess.check_output(
                ["ssh"] + SSH_OPTS + [LAPTOP_SSH, f"stat -f %m '{laptop_path}' 2>/dev/null"],
                text=True, timeout=10,
            ).strip()
            laptop_mtime = float(laptop_mtime_raw) if laptop_mtime_raw else 0

            if mac_mtime >= laptop_mtime:
                # Mac Mini is newer — auto-sync
                subprocess.run(
                    ["scp"] + SSH_OPTS[:-1] + [str(mac_path), f"{LAPTOP_SSH}:{laptop_path}"],
                    capture_output=True, timeout=30,
                )
                synced.append(mac_file)
            else:
                # Laptop is newer — unexpected, alert
                drifted.append(mac_file)

        except Exception:
            continue  # SSH issues on individual files — skip silently

    if synced:
        add("drift:auto_synced", "PASS", f"synced {len(synced)} file(s): {', '.join(synced)}")
    if drifted:
        add("drift:laptop_newer", "WARN",
            f"laptop has newer: {', '.join(drifted)} — needs manual review")
    if not synced and not drifted:
        add("drift:code", "PASS", "all shared files identical")


def check_cross_device_consistency():
    """Compare memory git and settings.json between machines."""
    remote = LAPTOP_SSH
    # Memory git dirs differ per machine
    local_memory = HOME / ".claude" / "projects" / "-Users-timtrailor-code" / "memory"
    remote_memory = "/Users/timtrailor/.claude/projects/-Users-timtrailor-code/memory"

    # Memory git HEAD match
    try:
        local_head = subprocess.check_output(
            ["git", "-C", str(local_memory), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
        remote_head = subprocess.check_output(
            ["ssh"] + SSH_OPTS + [remote,
             f"git -C {remote_memory} rev-parse HEAD 2>/dev/null"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        ).strip()
        if not remote_head:
            add("sync:memory_git", "PASS", "laptop unreachable (expected)")
        elif local_head == remote_head:
            # Use --porcelain so we get a stable, parseable list of dirty paths.
            # Active-session WIP is expected and noisy; only warn once dirty state
            # has persisted past the longest realistic session window (2h).
            # See auto-rca-inbox alert_signature cc5de5f1555b (2026-05-01) for RCA.
            dirty_raw = subprocess.check_output(
                ["git", "-C", str(local_memory), "status", "--porcelain"],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            ).strip()
            if dirty_raw:
                now = time.time()
                grace_seconds = 2 * 3600
                oldest_age = 0
                for line in dirty_raw.splitlines():
                    rel = line[3:].strip().strip('"')
                    fpath = local_memory / rel
                    try:
                        age = now - fpath.stat().st_mtime
                        if age > oldest_age:
                            oldest_age = age
                    except OSError:
                        oldest_age = grace_seconds + 1
                        break
                if oldest_age > grace_seconds:
                    add("sync:memory_git", "WARN",
                        f"HEAD matches ({local_head[:8]}) but uncommitted changes (oldest {int(oldest_age/60)}m)")
                else:
                    add("sync:memory_git", "PASS",
                        f"both at {local_head[:8]} (WIP, {int(oldest_age/60)}m old)")
            else:
                add("sync:memory_git", "PASS", f"both at {local_head[:8]}")
        else:
            # Classify the divergence rather than blanket-WARNing.
            # See auto-rca-inbox alert_id 110a6544a625 (2026-05-01) — repeated
            # false-positives because ahead-only / behind-only / true-fork all
            # collapsed to 'diverged'.
            try:
                subprocess.check_output(
                    ["git", "-C", str(local_memory), "fetch", "--quiet", "origin", "main"],
                    stderr=subprocess.DEVNULL, timeout=10,
                )
                merge_base = subprocess.check_output(
                    ["git", "-C", str(local_memory), "merge-base", local_head, remote_head],
                    text=True, stderr=subprocess.DEVNULL, timeout=5,
                ).strip()
            except Exception:
                merge_base = ""

            if merge_base == remote_head:
                # Laptop strictly behind; will catch up on next SessionStart.
                # Grace: only WARN if it has stayed behind for >6h (genuine staleness).
                last_session = 0
                try:
                    last_session = int(subprocess.check_output(
                        ["ssh"] + SSH_OPTS + [remote,
                         f"stat -f %m {remote_memory}/.git/FETCH_HEAD 2>/dev/null || echo 0"],
                        text=True, timeout=10,
                    ).strip() or "0")
                except Exception:
                    pass
                age_h = (time.time() - last_session) / 3600 if last_session else 999
                if age_h > 6:
                    add("sync:memory_git", "WARN",
                        f"laptop {remote_head[:8]} behind {local_head[:8]} for {age_h:.1f}h")
                else:
                    add("sync:memory_git", "PASS",
                        f"laptop behind by {local_head[:8]} (will pull on next session, {age_h:.1f}h)")
            elif merge_base == local_head:
                add("sync:memory_git", "PASS",
                    f"laptop ahead at {remote_head[:8]} (Mac Mini will pull next session)")
            else:
                add("sync:memory_git", "WARN",
                    f"true divergence — local {local_head[:8]} vs remote {remote_head[:8]} (need merge)")
    except subprocess.TimeoutExpired:
        add("sync:memory_git", "PASS", "laptop unreachable (expected)")
    except Exception as e:
        add("sync:memory_git", "WARN", f"could not compare: {e}")


def check_rogue_listeners():
    """Detect tunnel processes and unexpected listening ports.

    Pattern 22 (lessons.md): An ngrok tunnel ran for 12 days exposing a shell
    to the public internet, invisible to all audits because it wasn't a
    LaunchAgent. This check catches manually started tunnels and unexpected
    listeners.
    """
    # Known tunnel binaries — any running instance is suspicious
    # Use exact process name match (-x) where possible; for multi-word names
    # use -f (full command line) with careful filtering
    TUNNEL_BINARIES = ["ngrok", "cloudflared", "bore", "localtunnel"]

    for binary in TUNNEL_BINARIES:
        try:
            # -x matches exact process name; -f adds full command line to output
            out = subprocess.check_output(
                ["pgrep", "-xfl", binary],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            ).strip()
            if out:
                lines = [line for line in out.splitlines()
                         if "pgrep" not in line]
                if lines:
                    add("rogue:tunnel", "FAIL",
                        f"{binary} running: {lines[0][:80]}")
                    return
        except subprocess.CalledProcessError:
            pass  # pgrep returns 1 when no match — expected
        except Exception as e:
            add("rogue:tunnel", "WARN", f"scan error for {binary}: {e}")
            # Don't return — continue scanning remaining binaries

    add("rogue:tunnel", "PASS", "no tunnel processes found")

    # Phase 2: Port-diff check — compare actual listeners against declared services
    # Catches any new listener regardless of binary name
    KNOWN_PORTS = set()
    try:
        import sys as _sys
        _sys.path.insert(
            0,
            "/Users/timtrailor/code/tim-claude-controlplane/shared/lib",
        )
        import os as _os
        _os.environ.setdefault("SYSTEM_MAP_MACHINE", "mac-mini")
        import system_map as _sm
        for name, entry in _sm.services().items():
            if isinstance(entry, dict) and entry.get("port"):
                KNOWN_PORTS.add(int(entry["port"]))
    except Exception:
        pass  # If system_map unavailable, skip port-diff

    # Add well-known system ports that aren't in system_map
    KNOWN_PORTS.update({
        22,     # SSH
        5000,   # AirPlay / ControlCenter
        5555,   # Dragon Maze leaderboard proxy (com.dragonmaze.leaderboard)
        20241,  # cloudflared metrics/control for Dragon Maze tunnel (com.dragonmaze.cloudflared)
        7000,   # AirPlay / ControlCenter
        8502,   # Streamlit HTTPS proxy (part of streamlit-https service)
    })

    if KNOWN_PORTS:
        try:
            out = subprocess.check_output(
                ["lsof", "-i", "-P", "-n"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            unexpected = set()
            for line in out.splitlines():
                if "LISTEN" not in line:
                    continue
                # Extract port from address like *:8081 or 100.x.x.x:7681
                parts = line.split()
                if len(parts) < 9:
                    continue
                addr = parts[8]
                if ":" in addr:
                    try:
                        port = int(addr.rsplit(":", 1)[1])
                    except ValueError:
                        continue
                    if port not in KNOWN_PORTS:
                        proc = parts[0]
                        # Skip known Apple system processes (dynamic ports)
                        if proc in ("rapportd", "ControlCe", "AirPlayXP"):
                            continue
                        unexpected.add(f"{proc}:{port}")

            if unexpected:
                add("rogue:ports", "WARN",
                    f"unexpected listeners: {', '.join(sorted(unexpected)[:5])}")
            else:
                add("rogue:ports", "PASS",
                    f"all {len(KNOWN_PORTS)} known ports, no unexpected")
        except Exception as e:
            add("rogue:ports", "WARN", f"port scan error: {e}")


# ── Output ─────────────────────────────────────────────────────────────────

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
COLOURS = {"PASS": GREEN, "WARN": YELLOW, "FAIL": RED}


def print_results():
    name_w = max(len(r[0]) for r in results) if results else 20
    print(f"\nHealth Check — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 70)
    for name, status, detail in results:
        c = COLOURS.get(status, "")
        print(f"  {name:<{name_w}}  {c}{status}{RESET}  {detail}")
    print("-" * 70)
    passes = sum(1 for _, s, _ in results if s == "PASS")
    warns  = sum(1 for _, s, _ in results if s == "WARN")
    fails  = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"  {len(results)} checks: {passes} pass, {warns} warn, {fails} fail\n")
    return fails


def write_results_json():
    """Write results to JSON for iOS dashboard consumption."""
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": [
            {"name": name, "status": status, "detail": detail}
            for name, status, detail in results
        ],
        "summary": {
            "total": len(results),
            "pass": sum(1 for _, s, _ in results if s == "PASS"),
            "warn": sum(1 for _, s, _ in results if s == "WARN"),
            "fail": sum(1 for _, s, _ in results if s == "FAIL"),
        },
    }
    Path("/tmp/health_check_results.json").write_text(json.dumps(output, indent=2))


def send_ntfy(title, message, priority="default"):
    """Send push notification via ntfy.sh."""
    try:
        data = json.dumps({
            "topic": NTFY_TOPIC,
            "title": title,
            "message": message,
            "priority": 4 if priority == "high" else 3,
            "tags": ["warning"] if priority == "high" else ["info"],
        }).encode()
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def push_alert(message):
    """Send alert via conversation server (for iOS app)."""
    try:
        data = json.dumps({"content": message}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8081/push-message",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def post_alert_to_responder(fail_items, warn_items, raw_summary):
    """Hand the alert to auto-alert-responder via conversation_server.

    Returns True on 2xx — the responder will own notification delivery (APNs
    push via TerminalApp with Accept/Reject/Discuss buttons).
    Returns False on any failure — caller falls back to ntfy + push_alert so
    the alert is never silently swallowed.

    See memory/topics/auto-alert-responder.md.
    """
    try:
        payload = {
            "source": "mac_mini_health_check",
            "fails": [{"name": n, "detail": d} for n, d in fail_items],
            "warns": [{"name": n, "detail": d} for n, d in warn_items],
            "raw_summary": raw_summary,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8081/internal/alert-fired",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = resp.status
        return 200 <= code < 300
    except Exception as e:
        print(f"[health_check] responder POST failed: {e}", file=sys.stderr)
        return False


def ping_deadman():
    """Ping healthchecks.io to signal we're alive."""
    if not HEALTHCHECKS_PING_URL:
        return
    try:
        urllib.request.urlopen(HEALTHCHECKS_PING_URL, timeout=10)
    except Exception:
        pass



GOVERNORHUB_SYNC_LOG = CODE / "governorhub_sync.log"
GOVERNORHUB_MAX_AGE_HOURS = 192  # 8 days


def check_governorhub_sync():
    """Check GovernorHub document sync recency and success."""
    if not GOVERNORHUB_SYNC_LOG.exists():
        add("governorhub:sync", "WARN", "log file not found")
        return

    try:
        log_text = GOVERNORHUB_SYNC_LOG.read_text()
        # Find last successful run
        last_done = None
        for line in log_text.splitlines():
            if "Done." in line or "SYNC COMPLETE" in line:
                # Extract timestamp: "2026-04-10 16:25:43,535 INFO Done."
                m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if m:
                    last_done = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")

        if not last_done:
            # Check for errors in last run
            last_error = None
            for line in log_text.splitlines()[-20:]:
                if "ERROR" in line or "Traceback" in line or "Error" in line:
                    last_error = line.strip()[:80]
            if last_error:
                add("governorhub:sync", "FAIL", f"last run errored: {last_error}")
            else:
                add("governorhub:sync", "WARN", "no successful completion found in log")
            return

        age_h = (datetime.now() - last_done).total_seconds() / 3600
        if age_h > GOVERNORHUB_MAX_AGE_HOURS:
            add("governorhub:sync", "WARN",
                f"last success {age_h:.0f}h ago (threshold {GOVERNORHUB_MAX_AGE_HOURS}h)")
        else:
            add("governorhub:sync", "PASS", f"{age_h:.0f}h ago")
    except Exception as e:
        add("governorhub:sync", "FAIL", f"check error: {e}")

# ── Main ─────────────────────────────────────────────────────────────────


def check_semantic_probes():
    """Run the probes declared in system_map.yaml services section.

    Each service may declare probe.type in {http, file_age, process, keychain}.
    HTTP probes assert a GET returns expect_status (default 200).
    file_age probes assert the file exists and is within max_age_hours/minutes.
    process probes assert the process name is in pgrep output.
    keychain probes assert security find-generic-password returns 0.

    This is the structural fix for Gemini's stateful-degradation blind spot
    from the 2026-04-11 retro debate.
    """
    try:
        import os as _os
        import sys as _sys
        _sys.path.insert(
            0,
            "/Users/timtrailor/code/tim-claude-controlplane/shared/lib",
        )
        _os.environ.setdefault("SYSTEM_MAP_MACHINE", "mac-mini")
        import system_map as _sm
        services = _sm.services()
    except Exception as _e:
        add("probe:system_map", "WARN", f"could not load system_map: {_e}")
        return

    import urllib.request
    import urllib.error
    import subprocess as _sp
    import time as _time
    from pathlib import Path as _P

    for name, entry in services.items():
        if not isinstance(entry, dict):
            continue
        probe = entry.get("probe") or {}
        ptype = probe.get("type")
        check_name = f"probe:{name}"

        if ptype == "http":
            url = probe.get("url")
            if not url:
                continue
            expect = probe.get("expect_status", 200)
            timeout = probe.get("timeout_s", 5)
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    status = r.status
                    if status == expect:
                        add(check_name, "PASS", f"{url} -> {status}")
                    else:
                        add(check_name, "FAIL", f"{url} -> {status} (expected {expect})")
            except urllib.error.HTTPError as e:
                if e.code == expect:
                    add(check_name, "PASS", f"{url} -> {e.code}")
                else:
                    add(check_name, "FAIL", f"{url} -> HTTP {e.code}")
            except Exception as e:
                add(check_name, "FAIL", f"{url} unreachable: {e}")

        elif ptype == "file_age":
            path_raw = probe.get("path", "")
            path = _P(str(path_raw).replace("~", str(_P.home())))
            max_hours = probe.get("max_age_hours")
            max_minutes = probe.get("max_age_minutes")
            if not path.exists():
                add(check_name, "FAIL", f"{path} missing")
                continue
            age_s = _time.time() - path.stat().st_mtime
            if max_minutes is not None:
                limit_s = max_minutes * 60
                label = f"{age_s/60:.1f}min old"
            elif max_hours is not None:
                limit_s = max_hours * 3600
                label = f"{age_s/3600:.1f}h old"
            else:
                add(check_name, "PASS", f"{path} exists")
                continue
            if age_s <= limit_s:
                add(check_name, "PASS", label)
            else:
                add(check_name, "FAIL", f"{label} (limit {limit_s/60:.0f}min)")

        elif ptype == "process":
            pname = probe.get("name", "")
            if not pname:
                continue
            try:
                out = _sp.check_output(["pgrep", "-f", pname], text=True).strip()
                if out:
                    add(check_name, "PASS", f"process {pname} running")
                else:
                    add(check_name, "FAIL", f"process {pname} not found")
            except _sp.CalledProcessError:
                add(check_name, "FAIL", f"process {pname} not found")
            except Exception as e:
                add(check_name, "WARN", f"pgrep {pname} error: {e}")

        elif ptype == "keychain":
            service = probe.get("service", "")
            account = probe.get("account", "")
            if not service or not account:
                continue
            try:
                rc = _sp.call(
                    ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                )
                if rc == 0:
                    add(check_name, "PASS", f"keychain {service}/{account} accessible")
                else:
                    add(check_name, "FAIL", f"keychain {service}/{account} not accessible (rc={rc})")
            except Exception as e:
                add(check_name, "WARN", f"keychain probe error: {e}")


def main():
    check_launchagents()
    check_semantic_probes()
    check_services()
    check_printers()
    check_backups()
    check_disk()
    check_git_repos()
    check_printer_daemon()
    check_required_files()
    check_memory_db()
    check_code_divergence()
    check_cross_device_consistency()
    check_rogue_listeners()
    check_governorhub_sync()

    fails = print_results()
    write_results_json()

    # Two-consecutive-failure damping — Tim 2026-04-18: transient exit-1-
    # then-recovery was paging every 10 min during tonight's fix cycle.
    # Only alert on checks that were ALSO failing on the previous run.
    # Persists the previous run's failing-check names in a tiny state
    # file; new-this-run failures wait one run before paging.
    from pathlib import Path as _Path
    prev_file = _Path("/tmp/health_check_prev_fails.json")
    current_fail_names = {n for n, s, _ in results if s == "FAIL"}
    current_warn_names = {n for n, s, _ in results if s == "WARN"}
    prev_fail_names = set()
    prev_warn_names = set()
    if prev_file.exists():
        try:
            prev = json.loads(prev_file.read_text())
            prev_fail_names = set(prev.get("fails") or [])
            prev_warn_names = set(prev.get("warns") or [])
        except (OSError, json.JSONDecodeError):
            pass
    # Only alert on items present in BOTH the current and previous runs.
    persistent_fails = current_fail_names & prev_fail_names
    persistent_warns = current_warn_names & prev_warn_names
    # Persist the current names for the next run, regardless of alerting.
    try:
        prev_file.write_text(json.dumps({
            "fails": sorted(current_fail_names),
            "warns": sorted(current_warn_names),
        }))
    except OSError:
        pass

    if persistent_fails or persistent_warns:
        fail_items = [(n, d) for n, s, d in results if s == "FAIL" and n in persistent_fails]
        warn_items = [(n, d) for n, s, d in results if s == "WARN" and n in persistent_warns]
        parts = []
        if fail_items:
            parts.append("FAIL: " + ", ".join(n for n, _ in fail_items))
        if warn_items:
            parts.append("WARN: " + ", ".join(n for n, _ in warn_items))
        summary = "; ".join(parts)

        total = len(persistent_fails) + len(persistent_warns)

        # Primary: hand off to auto-alert-responder. Server owns notification
        # delivery (APNs push w/ Accept/Reject/Discuss buttons). Only fall
        # through to ntfy+push_alert if the POST fails, so nothing is ever
        # silently swallowed.
        handed_off = post_alert_to_responder(fail_items, warn_items, summary)

        if not handed_off:
            send_ntfy(
                f"[Mac Mini] {total} persistent issue(s)",
                summary,
                priority="high" if fail_items else "default",
            )
            push_alert(f"[Mac Mini] Health: {total} persistent issue(s). {summary}")
    elif fails > 0:
        # First-occurrence: log to stderr so it's visible in /tmp/health_check.err
        # but don't page. Second run confirms or clears.
        first_seen = current_fail_names | current_warn_names
        print(
            f"[health_check] first-occurrence (not paging): {sorted(first_seen)}",
            file=sys.stderr,
        )

    # Dead-man's-switch ping always fires on success; ping on fail too
    # so the script being alive is still proven.
    ping_deadman()

    sys.exit(1 if fails > 0 else 0)


if __name__ == "__main__":
    main()
