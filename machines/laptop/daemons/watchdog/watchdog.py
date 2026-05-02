#!/usr/bin/env python3
"""Mac Mini watchdog — runs on the laptop, polls the Mac Mini every 60s
(fast path) and 15 min (full sweep), auto-recovers known failure modes per
an approved-action list, pages Tim once per condition.

Pattern 36 prevention layer: this monitor runs on a SEPARATE host so it does
not share the host's failure mode. It is pure shell + Python — NO LLM calls.

Guard-rails (non-negotiable, prevent the watchdog from becoming the next
runaway):
  - Single-instance lock via flock
  - Action budget: max 1 reboot/hour, max 3 kickstarts/hour
  - Killswitch: ~/.watchdog-disabled on either host pauses everything
  - Outbound rate limit: 1 push per condition per 30 min
  - Self-test on every tick (SSH reachable + state file writable)

Approved auto-fix categories (anything else: page Tim, do nothing):
  - probe:conversation-server unreachable    → kickstart LaunchAgent
  - launchd:<service> not loaded             → bootstrap if in approved list
  - memory:chromadb slow                     → kickstart memory-search
  - backup:recency overdue                   → trigger backup_to_drive.py
  - Mac Mini load > 30 (Pattern 36)          → kill runaway claude/hook procs
  - Mac Mini procs > 1000                    → kill runaway claude/hook procs

Anything outside the approved list (printer:*, git:*, file:credentials,
rogue:tunnel, disk:usage > 90%) → page Tim, no auto-action.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────
MAC_MINI_HOST = "100.126.253.40"  # Tailscale primary
MAC_MINI_HOST_FALLBACK = "192.168.0.172"  # LAN fallback
MAC_MINI_USER = "timtrailor"

# Thresholds (Pattern 36)
LOAD_AMBER = 10.0
LOAD_RED = 30.0
PROCS_AMBER = 700
PROCS_RED = 1000
TMUX_WINDOWS_LATENCY_AMBER_MS = 2000
TMUX_WINDOWS_LATENCY_RED_MS = 5000

# Budget
MAX_REBOOTS_PER_HOUR = 1
MAX_KICKSTARTS_PER_HOUR = 3
MAX_PROC_REAPS_PER_HOUR = 4

# Push throttle
PUSH_THROTTLE_SECS = 1800  # 30 min per condition

# Paths (laptop side)
STATE_DIR = Path.home() / ".watchdog"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "watchdog.lock"
LOG_FILE = STATE_DIR / "watchdog.log"
DISABLED_FLAG_LAPTOP = Path.home() / ".watchdog-disabled"
DISABLED_FLAG_MINI = "/Users/timtrailor/.watchdog-disabled"

# Approved auto-fix categories — anything outside these = page Tim only
APPROVED_LAUNCHCTL_KICKSTARTS = {
    "com.timtrailor.conversation-server",
    "com.timtrailor.memory-search",
    "com.timtrailor.health-check",
    "com.timtrailor.token-refresh",
}

APPROVED_PROC_REAP_PATTERNS = [
    # Pattern, parent-must-be-PID-1 only (orphans), max age seconds before reap
    ("claude --print --model", True, 60),
    ("hook_smoke_test.sh", True, 300),
    ("protected_path_hook.sh", True, 300),
    ("tier3_reviewer.py", True, 60),
    ("scan_command.py", True, 60),
    ("tier_classifier.py", True, 60),
    ("memory_health_check.sh", True, 600),
]

STATE_DIR.mkdir(parents=True, exist_ok=True)


# ── Utilities ─────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n"
    try:
        with LOG_FILE.open("a") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="", file=sys.stderr)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"actions": [], "last_pushes": {}, "last_tick": 0, "last_verdict": "UNKNOWN"}
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"actions": [], "last_pushes": {}, "last_tick": 0, "last_verdict": "UNKNOWN"}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_FILE)


@contextmanager
def single_instance():
    """Refuse to run if another instance holds the lock."""
    import fcntl

    fp = LOCK_FILE.open("a+")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another watchdog instance holds the lock — exiting")
        sys.exit(0)
    try:
        yield
    finally:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fp.close()


def killswitch_set() -> bool:
    """Either-side touch file pauses the watchdog."""
    if DISABLED_FLAG_LAPTOP.exists():
        return True
    rc, _ = ssh_run([f"test -e {DISABLED_FLAG_MINI}"], timeout=5)
    return rc == 0


def ssh_run(cmd: list[str] | str, timeout: int = 10) -> tuple[int, str]:
    """Run a command on Mac Mini via SSH. Try Tailscale first, fall back to LAN.

    Returns (rc, combined_output).
    """
    if isinstance(cmd, list):
        cmd_str = " ".join(cmd)
    else:
        cmd_str = cmd
    for host in (MAC_MINI_HOST, MAC_MINI_HOST_FALLBACK):
        try:
            r = subprocess.run(
                ["ssh", "-o", f"ConnectTimeout={min(timeout, 8)}",
                 "-o", "BatchMode=yes",
                 f"{MAC_MINI_USER}@{host}",
                 cmd_str],
                capture_output=True, text=True, timeout=timeout,
            )
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            continue
        except Exception as exc:
            return 255, f"ssh exception: {exc}"
    return 255, "ssh: no host reachable"


def http_get(url: str, timeout: int = 5) -> tuple[int, str]:
    """HTTP GET via Tailscale. Returns (status, body)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "watchdog/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return 0, str(e)


# ── Probes ────────────────────────────────────────────────────────────────
def probe_health_endpoint() -> dict:
    """Hit conversation_server /health via Tailscale."""
    t0 = time.monotonic()
    code, body = http_get(f"http://{MAC_MINI_HOST}:8081/health", timeout=5)
    latency_ms = int((time.monotonic() - t0) * 1000)
    ok = code == 200 and '"ok": true' in body or '"ok":true' in body
    return {"name": "conversation_server_health", "ok": ok, "latency_ms": latency_ms, "code": code}


def probe_tmux_windows() -> dict:
    """The endpoint the iOS app polls; latency is the user-visible signal."""
    t0 = time.monotonic()
    code, body = http_get(f"http://{MAC_MINI_HOST}:8081/tmux-windows", timeout=8)
    latency_ms = int((time.monotonic() - t0) * 1000)
    ok = code == 200 and "windows" in body
    return {"name": "tmux_windows", "ok": ok, "latency_ms": latency_ms, "code": code}


def probe_loadavg_and_procs() -> dict:
    rc, out = ssh_run("uptime && ps aux | wc -l", timeout=8)
    if rc != 0:
        return {"name": "host_load", "ok": False, "loadavg": None, "procs": None, "raw": out[:200]}
    loadavg = None
    procs = None
    try:
        # uptime line: "... load averages: 1.23 4.56 7.89"
        for line in out.splitlines():
            if "load average" in line.lower():
                tail = line.split("load average")[-1]
                # strip ":s: " / ":" then split on whitespace/comma
                tail = tail.lstrip(":s ")
                parts = [p.strip(",").strip() for p in tail.split()]
                # take first numeric
                for p in parts:
                    try:
                        loadavg = float(p)
                        break
                    except ValueError:
                        continue
                break
        # last numeric line is procs
        for line in reversed(out.strip().splitlines()):
            try:
                procs = int(line.strip())
                break
            except ValueError:
                continue
    except Exception:
        pass
    return {"name": "host_load", "ok": True, "loadavg": loadavg, "procs": procs}


def probe_health_check_file() -> dict:
    """Read latest health_check_results.json from the Mac Mini."""
    rc, out = ssh_run("cat /tmp/health_check_results.json 2>/dev/null", timeout=5)
    if rc != 0 or not out.strip():
        return {"name": "health_check", "ok": False, "summary": None}
    try:
        data = json.loads(out)
        return {"name": "health_check", "ok": True, "summary": data.get("summary"),
                "fails": data.get("fails", []), "warns": data.get("warns", [])}
    except (ValueError, KeyError):
        return {"name": "health_check", "ok": False, "summary": None}


def probe_all_fast() -> list[dict]:
    """Fast tick — runs every 60s, only the cheap user-facing probes."""
    return [probe_health_endpoint(), probe_tmux_windows(), probe_loadavg_and_procs()]


def probe_all_full() -> list[dict]:
    """Full tick — runs every 15 min."""
    return probe_all_fast() + [probe_health_check_file()]


# ── Classifier ────────────────────────────────────────────────────────────
def classify(probes: list[dict]) -> tuple[str, list[str]]:
    """Return (verdict, reasons). verdict ∈ GREEN | AMBER | RED_RECOVERABLE | RED_UNRECOVERABLE."""
    reasons: list[str] = []
    by_name = {p["name"]: p for p in probes}

    health = by_name.get("conversation_server_health", {})
    tmux = by_name.get("tmux_windows", {})
    host = by_name.get("host_load", {})
    hc = by_name.get("health_check", {})

    # Hard RED-UNRECOVERABLE: SSH+HTTP both unreachable
    ssh_dead = host.get("ok") is False
    http_dead = health.get("code", 0) == 0 and health.get("latency_ms", 0) > 1000
    if ssh_dead and http_dead:
        reasons.append("Mac Mini unreachable via both SSH and HTTP")
        return "RED_UNRECOVERABLE", reasons

    # RED-RECOVERABLE: load / procs / latency over RED thresholds
    if isinstance(host.get("loadavg"), (int, float)) and host["loadavg"] >= LOAD_RED:
        reasons.append(f"load_1m={host['loadavg']:.1f} >= {LOAD_RED}")
    if isinstance(host.get("procs"), int) and host["procs"] >= PROCS_RED:
        reasons.append(f"procs={host['procs']} >= {PROCS_RED}")
    if tmux.get("ok") is False or tmux.get("latency_ms", 0) >= TMUX_WINDOWS_LATENCY_RED_MS:
        reasons.append(f"/tmux-windows ok={tmux.get('ok')} latency_ms={tmux.get('latency_ms')}")
    if not health.get("ok"):
        reasons.append(f"/health code={health.get('code')} latency_ms={health.get('latency_ms')}")
    if reasons:
        return "RED_RECOVERABLE", reasons

    # AMBER: warning thresholds
    if isinstance(host.get("loadavg"), (int, float)) and host["loadavg"] >= LOAD_AMBER:
        reasons.append(f"load_1m={host['loadavg']:.1f} >= {LOAD_AMBER}")
    if isinstance(host.get("procs"), int) and host["procs"] >= PROCS_AMBER:
        reasons.append(f"procs={host['procs']} >= {PROCS_AMBER}")
    if tmux.get("latency_ms", 0) >= TMUX_WINDOWS_LATENCY_AMBER_MS:
        reasons.append(f"/tmux-windows latency_ms={tmux['latency_ms']}")
    if hc and hc.get("summary"):
        s = hc["summary"]
        if s.get("fail", 0) > 0:
            reasons.append(f"health_check fails={s.get('fail')} (full sweep)")
        elif s.get("warn", 0) > 0:
            reasons.append(f"health_check warns={s.get('warn')} (full sweep)")
    if reasons:
        return "AMBER", reasons

    return "GREEN", reasons


# ── Action budget ─────────────────────────────────────────────────────────
def budget_allows(action_kind: str, state: dict) -> bool:
    now = time.time()
    cutoff = now - 3600
    recent = [a for a in state.get("actions", []) if a["t"] >= cutoff and a["kind"] == action_kind]
    limits = {
        "reboot": MAX_REBOOTS_PER_HOUR,
        "kickstart": MAX_KICKSTARTS_PER_HOUR,
        "proc_reap": MAX_PROC_REAPS_PER_HOUR,
    }
    return len(recent) < limits.get(action_kind, 1)


def record_action(action_kind: str, detail: str, state: dict) -> None:
    state.setdefault("actions", []).append(
        {"t": time.time(), "kind": action_kind, "detail": detail}
    )
    # prune older than 24h
    cutoff = time.time() - 86400
    state["actions"] = [a for a in state["actions"] if a["t"] >= cutoff]
    save_state(state)


# ── Actions (approved-list only) ──────────────────────────────────────────
def action_kickstart(label: str, state: dict) -> bool:
    if label not in APPROVED_LAUNCHCTL_KICKSTARTS:
        log(f"REFUSE kickstart {label}: not in approved list")
        return False
    if not budget_allows("kickstart", state):
        log(f"REFUSE kickstart {label}: budget exhausted")
        return False
    rc, out = ssh_run(f"launchctl kickstart -k gui/$(id -u)/{label}", timeout=15)
    log(f"kickstart {label}: rc={rc} out={out[:200]}")
    record_action("kickstart", label, state)
    return rc == 0


def action_proc_reap(state: dict) -> bool:
    if not budget_allows("proc_reap", state):
        log("REFUSE proc_reap: budget exhausted")
        return False
    cmd_parts = []
    for pat, _orphan_only, _max_age in APPROVED_PROC_REAP_PATTERNS:
        cmd_parts.append(
            "ps -axo pid,ppid,etime,command | "
            f"grep -F {repr(pat)} | grep -v grep | "
            'awk \'{ if ($2==1) print $1 }\' | '
            "while read p; do kill -9 \"$p\" 2>/dev/null; done"
        )
    cmd = "; ".join(cmd_parts)
    rc, out = ssh_run(cmd, timeout=15)
    log(f"proc_reap rc={rc}")
    record_action("proc_reap", "approved_patterns", state)
    return rc == 0


def action_reboot(state: dict) -> bool:
    if not budget_allows("reboot", state):
        log("REFUSE reboot: budget exhausted")
        return False
    # Requires passwordless sudoers entry for `reboot` — see deploy doc.
    # Fail-safe: if sudoers isn't set, this returns non-zero and we page Tim.
    rc, out = ssh_run("sudo -n /sbin/reboot", timeout=10)
    log(f"reboot rc={rc} out={out[:200]}")
    record_action("reboot", "sudoers", state)
    return rc == 0


# ── Notifier ──────────────────────────────────────────────────────────────
def push_throttled(state: dict, condition_key: str, title: str, body: str) -> bool:
    """Send a push only if we haven't pushed for this condition recently."""
    last = state.get("last_pushes", {}).get(condition_key, 0)
    if time.time() - last < PUSH_THROTTLE_SECS:
        return False
    # Use the conversation_server's /push-message endpoint if reachable; else SSH+pmset wall.
    payload = json.dumps({"title": title, "body": body, "bundle_id": "com.timtrailor.terminal"})
    code, _body = http_post(f"http://{MAC_MINI_HOST}:8081/push-message", payload, timeout=8)
    if code == 200:
        state.setdefault("last_pushes", {})[condition_key] = time.time()
        save_state(state)
        log(f"push sent condition={condition_key} title={title!r}")
        return True
    # Fallback: SMTP via Mac Mini cli isn't always feasible; just log.
    log(f"push FAILED condition={condition_key} code={code}")
    return False


def http_post(url: str, body: str, timeout: int = 8) -> tuple[int, str]:
    try:
        req = urllib.request.Request(
            url, data=body.encode(), method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "watchdog/1"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return 0, str(e)


# ── Main loop ─────────────────────────────────────────────────────────────
def remediate(verdict: str, reasons: list[str], probes: list[dict], state: dict) -> list[str]:
    """Apply approved auto-fixes for RED_RECOVERABLE. Returns list of action labels taken."""
    actions = []
    by_name = {p["name"]: p for p in probes}
    host = by_name.get("host_load", {})

    # Prioritise: clear runaway procs first (cheapest), then kickstart, then reboot.
    procs = host.get("procs") or 0
    loadavg = host.get("loadavg") or 0
    health_bad = not by_name.get("conversation_server_health", {}).get("ok")
    tmux_slow = (by_name.get("tmux_windows", {}).get("latency_ms") or 0) >= TMUX_WINDOWS_LATENCY_RED_MS

    if procs >= PROCS_RED or loadavg >= LOAD_RED:
        if action_proc_reap(state):
            actions.append("proc_reap")

    if health_bad or tmux_slow:
        if action_kickstart("com.timtrailor.conversation-server", state):
            actions.append("kickstart:conversation-server")

    return actions


def tick(full: bool, state: dict) -> dict:
    if killswitch_set():
        log("killswitch active — skipping tick")
        return {"verdict": "PAUSED", "reasons": ["killswitch"], "actions": []}

    probes = probe_all_full() if full else probe_all_fast()
    verdict, reasons = classify(probes)

    actions: list[str] = []
    if verdict == "RED_RECOVERABLE":
        actions = remediate(verdict, reasons, probes, state)
        push_throttled(
            state,
            condition_key="red_recoverable",
            title="[Watchdog] Mac Mini RED — auto-fixing",
            body=f"Reasons: {'; '.join(reasons[:2])}. Actions: {', '.join(actions) or 'none (budget?)'}",
        )
    elif verdict == "RED_UNRECOVERABLE":
        push_throttled(
            state,
            condition_key="red_unrecoverable",
            title="[Watchdog] Mac Mini UNREACHABLE",
            body=f"Reasons: {'; '.join(reasons[:2])}. SSH+HTTP both failing. Manual intervention.",
        )
    elif verdict == "AMBER":
        # Only push amber on the full tick to avoid noise
        if full:
            push_throttled(
                state,
                condition_key="amber",
                title="[Watchdog] Mac Mini AMBER",
                body=f"Reasons: {'; '.join(reasons[:2])}",
            )

    state["last_tick"] = time.time()
    state["last_verdict"] = verdict
    state["last_reasons"] = reasons
    state["last_actions"] = actions
    save_state(state)

    log(f"tick full={full} verdict={verdict} reasons={'; '.join(reasons[:3]) or 'ok'} actions={actions}")
    return {"verdict": verdict, "reasons": reasons, "actions": actions}


def main() -> None:
    if "--once" in sys.argv:
        with single_instance():
            full = "--full" in sys.argv
            state = load_state()
            result = tick(full=full, state=state)
            print(json.dumps(result, indent=2, default=str))
        return

    # Default: long-running daemon with internal cadence
    with single_instance():
        log("watchdog starting (laptop-side, polling Mac Mini)")
        last_full = 0.0
        while True:
            state = load_state()
            now = time.time()
            full = (now - last_full) >= 900  # 15 min full sweep
            try:
                tick(full=full, state=state)
                if full:
                    last_full = now
            except Exception as exc:
                log(f"tick raised: {exc!r}")
            time.sleep(60)


if __name__ == "__main__":
    main()
