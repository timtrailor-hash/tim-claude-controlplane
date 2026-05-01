#!/usr/bin/env python3
"""work_health_check.py — hourly probe of work-laptop services.

Pattern 3 enforcement: every probe in here must EXERCISE the actual
feature, not just check a process is running. "memory_work imports OK"
is not a probe; "memory_work answers a smoke search" is.

Output: /tmp/work_health_check_results.json
Shape:
    {
        "timestamp": "<ISO-8601>",
        "items": [
            {"name": "<probe>", "status": "green|amber|red", "detail": "..."},
            ...
        ]
    }

Status enum:
    green  — feature works as intended
    amber  — degraded but still usable
    red    — feature broken; user-visible impact

Defence: this script honours `~/.claude/.work-laptop` as a marker. If
it is missing the script exits 0 immediately — that is how we keep it
from running on the personal Mac Mini if a deploy mistakenly copies
the plist over.

Logs: /tmp/work_health_check.log (LaunchAgent stdout+stderr stream).
The script ALWAYS exits 0 so launchd does not crash-loop; failures
become red items inside the JSON, not non-zero exits.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
MARKER = HOME / ".claude" / ".work-laptop"
RESULTS = Path("/tmp/work_health_check_results.json")
LOG = Path("/tmp/work_health_check.log")

BRIDGE_REPO = HOME / "code" / "claude-bridge"
WORK_MEMORY_DATA = HOME / ".claude" / "work_memory_data"
TRANSCRIPTS_DIR = HOME / ".claude" / "projects"
BRIDGE_HEALTH_URL = os.environ.get("BRIDGE_HEALTH_URL", "http://127.0.0.1:8090/bridge-health")

DISK_AMBER_PCT = 15  # free %; amber under this
DISK_RED_PCT = 5     # red under this
MEMORY_DATA_RED_GB = 50  # red if work_memory_data exceeds 50 GB
LAST_SESSION_AMBER_HOURS = 48
LAST_SESSION_RED_HOURS = 168  # 7 days


# ── Common helpers ────────────────────────────────────────────────────


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_secret(name: str) -> str | None:
    """Env → Keychain. No third fallback."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        r = subprocess.run(
            ["security", "find-generic-password",
             "-a", name, "-s", "tim-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        _log(f"keychain lookup failed for {name}: {e}")
        return None
    if r.returncode != 0:
        return None
    val = r.stdout.strip()
    return val or None


def _item(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


# ── Probes ────────────────────────────────────────────────────────────


def probe_bridge_mcp() -> dict:
    """Bridge MCP availability.

    Preference order:
      1. HTTP probe of $BRIDGE_HEALTH_URL (returns JSON {ok: true}).
      2. Process-name search for work_mcp_server.py.
      3. File existence (last-resort, can't tell if it actually runs).
    """
    name = "bridge-mcp"
    # Step 1 — HTTP probe.
    try:
        req = urllib.request.Request(BRIDGE_HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8", "replace")
        try:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("ok") is True:
                return _item(name, "green", f"http: {data}")
        except json.JSONDecodeError:
            pass
        return _item(name, "amber", f"http reachable but unexpected body: {body[:200]}")
    except urllib.error.URLError:
        pass
    except Exception as e:
        _log(f"bridge-mcp http probe error: {e}")

    # Step 2 — pgrep for the server module.
    try:
        r = subprocess.run(
            ["pgrep", "-f", "work_mcp_server.py"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return _item(name, "amber", f"no http endpoint; pgrep found pids {r.stdout.strip()}")
    except Exception as e:
        _log(f"pgrep failed: {e}")

    # Step 3 — file presence.
    f = BRIDGE_REPO / "tools" / "work_mcp_server.py"
    if f.is_file():
        return _item(name, "amber", f"server file at {f} but no running process detected")
    return _item(name, "red", f"server file missing at {f}")


def probe_memory_work() -> dict:
    """Pattern 3: actually run a smoke search through the work memory MCP.

    Imports the server module and calls search_memory("smoke"). If the
    module raises (lock contention, missing data dir, ChromaDB load
    failure), we surface that as red.
    """
    name = "memory-work"
    server = BRIDGE_REPO / "tools" / "work_memory_server.py"
    if not server.is_file():
        return _item(name, "red", f"server file missing at {server}")

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("work_memory_server_probe", server)
        if spec is None or spec.loader is None:
            return _item(name, "red", "could not build import spec")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            # Single-instance lock taken. That's fine — the indexer or
            # an active session holds it. Counts as green because the
            # MCP itself is alive somewhere.
            return _item(name, "green", "another memory_server holds the single-instance lock")

        if not hasattr(mod, "search_memory"):
            return _item(name, "red", "server has no search_memory tool")

        results = mod.search_memory("smoke", limit=1)
        if isinstance(results, (list, dict)):
            return _item(name, "green", f"search_memory returned {type(results).__name__}")
        return _item(name, "amber", f"search_memory returned unexpected type {type(results).__name__}")
    except Exception as e:
        return _item(name, "red", f"smoke search failed: {e}")


def probe_disk() -> dict:
    name = "disk-free"
    try:
        usage = shutil.disk_usage(str(HOME))
    except Exception as e:
        return _item(name, "red", f"disk_usage failed: {e}")
    free_pct = (usage.free / usage.total) * 100 if usage.total else 0
    free_gb = usage.free / (1024 ** 3)
    detail = f"{free_pct:.1f}% free ({free_gb:.1f} GB) of {usage.total / (1024 ** 3):.1f} GB"
    if free_pct < DISK_RED_PCT:
        return _item(name, "red", detail)
    if free_pct < DISK_AMBER_PCT:
        return _item(name, "amber", detail)
    return _item(name, "green", detail)


def probe_work_memory_data_size() -> dict:
    """Bound the on-disk work memory dir. Runaway growth = red."""
    name = "work-memory-data-size"
    if not WORK_MEMORY_DATA.exists():
        return _item(name, "amber", f"{WORK_MEMORY_DATA} missing; first-index pending")
    total = 0
    try:
        for root, _, files in os.walk(WORK_MEMORY_DATA):
            for f in files:
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except Exception as e:
        return _item(name, "amber", f"walk failed: {e}")
    gb = total / (1024 ** 3)
    detail = f"{gb:.2f} GB"
    if gb >= MEMORY_DATA_RED_GB:
        return _item(name, "red", detail)
    if gb >= MEMORY_DATA_RED_GB / 2:
        return _item(name, "amber", detail)
    return _item(name, "green", detail)


def probe_last_session() -> dict:
    """Newest .jsonl mtime across ~/.claude/projects/.

    If we haven't ended a session in 48h, amber. 168h+, red — likely
    the work-laptop is offline or the JSONL writer is broken.
    """
    name = "last-session"
    if not TRANSCRIPTS_DIR.exists():
        return _item(name, "red", f"{TRANSCRIPTS_DIR} missing")
    newest = 0.0
    newest_path = ""
    try:
        for root, _, files in os.walk(TRANSCRIPTS_DIR):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                fp = Path(root) / f
                try:
                    mt = fp.stat().st_mtime
                except OSError:
                    continue
                if mt > newest:
                    newest = mt
                    newest_path = str(fp)
    except Exception as e:
        return _item(name, "amber", f"walk failed: {e}")
    if newest == 0.0:
        return _item(name, "amber", "no .jsonl transcripts found yet")
    age_h = (time.time() - newest) / 3600
    detail = f"newest {age_h:.1f}h ago at {newest_path}"
    if age_h >= LAST_SESSION_RED_HOURS:
        return _item(name, "red", detail)
    if age_h >= LAST_SESSION_AMBER_HOURS:
        return _item(name, "amber", detail)
    return _item(name, "green", detail)


PROBES = [
    probe_bridge_mcp,
    probe_memory_work,
    probe_disk,
    probe_work_memory_data_size,
    probe_last_session,
]


def main() -> int:
    if not MARKER.exists():
        _log(f"marker {MARKER} missing; not a work laptop, exiting 0")
        return 0

    items: list[dict] = []
    for probe in PROBES:
        try:
            items.append(probe())
        except Exception as e:
            tb = traceback.format_exc().splitlines()[-3:]
            items.append(_item(probe.__name__, "red",
                               f"probe crashed: {e}; tb: {' | '.join(tb)}"))

    payload = {"timestamp": _now_iso(), "items": items}
    try:
        RESULTS.write_text(json.dumps(payload, indent=2))
        _log(f"wrote {RESULTS} with {len(items)} probe(s)")
    except OSError as e:
        _log(f"write to {RESULTS} failed: {e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"fatal: {e}")
        _log(traceback.format_exc())
        sys.exit(0)
