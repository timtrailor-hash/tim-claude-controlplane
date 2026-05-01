#!/usr/bin/env python3
"""CI failure poller — Mac Mini-side replacement for the ntfy-based
ratchet-persistence-check.yml workflow.

Pipeline (2026-04-18 redesign — Tim wanted Terminal APNs, not ntfy):

    LaunchAgent (every 15 min)
      → this script
         → gh run list (watched repos, main branch)
            → filter: latest run per workflow where conclusion=failure AND age > 1h
               → POST /internal/ci-alert on conversation_server
                  → APNs push to TerminalApp

Design rules:
- No public endpoint; conversation_server is localhost-only.
- No duplicate alerts: state file records which run_ids we've alerted on.
- Transient failures (<1h) never surface — matches Tim's
  "can wait an hour or so to see things released" directive.
- Stateless-safe: state file can be deleted at any time; we'll re-alert
  on the next tick for any still-red workflows.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WATCHED_REPOS = [
    "timtrailor-hash/claude-mobile",
    "timtrailor-hash/PrinterPilot",
    "timtrailor-hash/tim-claude-controlplane",
    "timtrailor-hash/sv08-print-tools",
    "timtrailor-hash/TerminalApp",
]

STATE_PATH = Path("/tmp/ci_failure_alerts.json")
SERVER = "http://127.0.0.1:8081"
CI_ALERT_ENDPOINT = f"{SERVER}/internal/ci-alert"
GRACE_SEC = 3600  # 1 hour — matches Tim's "let transient issues self-resolve" rule
MAX_AGE_SEC = 14 * 24 * 3600  # 14 days — older failures are abandoned state, not persistent. Skip.
GH_BIN = "/opt/homebrew/bin/gh"
STATE_TTL_SEC = 7 * 24 * 3600  # drop alerted-state entries older than 7d


def _log(msg: str) -> None:
    print(f"[{datetime.datetime.utcnow().isoformat()}Z] {msg}", flush=True)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception as e:
        _log(f"state read failed ({e}); resetting")
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except OSError as e:
        _log(f"state write failed: {e}")


def _unlock_keychain() -> None:
    """Mac Mini LaunchAgents run in a context where login.keychain-db is
    locked. credential.sh pattern: ~/.keychain_pass holds the unlock pass."""
    pass_file = Path.home() / ".keychain_pass"
    if not pass_file.exists():
        return
    try:
        pw = pass_file.read_text().strip()
        subprocess.run(
            ["security", "unlock-keychain", "-p", pw,
             str(Path.home() / "Library" / "Keychains" / "login.keychain-db")],
            check=False, timeout=5, capture_output=True,
        )
    except Exception as e:
        _log(f"keychain unlock failed: {e}")


def _get_gh_token() -> str | None:
    try:
        r = subprocess.run(
            ["security", "find-generic-password",
             "-a", "GITHUB_TOKEN", "-s", "tim-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:
        _log(f"token fetch failed: {e}")
    return None


def _gh_runs(repo: str, token: str) -> list[dict]:
    """Pull the last 30 main-branch runs for a repo."""
    env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}
    try:
        r = subprocess.run(
            [GH_BIN, "run", "list", "--repo", repo,
             "--branch", "main", "--limit", "30",
             "--json", "databaseId,workflowName,conclusion,status,"
                       "createdAt,updatedAt,url,headSha"],
            env=env, capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        _log(f"{repo}: gh timed out")
        return []
    if r.returncode != 0:
        _log(f"{repo}: gh failed rc={r.returncode}: {r.stderr.strip()[:200]}")
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError as e:
        _log(f"{repo}: json parse failed: {e}")
        return []


def _parse_iso(ts: str) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_persistent_failures(runs: list[dict], now_utc: datetime.datetime) -> list[dict]:
    """Group runs by workflow name, return the LATEST per workflow if it's
    a failure older than GRACE_SEC."""
    by_workflow: dict[str, dict] = {}
    for run in runs:
        wf = run.get("workflowName") or "?"
        if wf not in by_workflow:
            by_workflow[wf] = run
    persistent: list[dict] = []
    for wf, run in by_workflow.items():
        if run.get("status") != "completed":
            continue
        if run.get("conclusion") != "failure":
            continue
        created = _parse_iso(run.get("createdAt", ""))
        if not created:
            continue
        age = (now_utc - created).total_seconds()
        if age < GRACE_SEC:
            continue
        if age > MAX_AGE_SEC:
            # Ancient failures are abandoned state (e.g. a one-off
            # push ran a PR-only workflow on main and never re-ran).
            # Not Tim's "persistent" category — just noise.
            continue
        persistent.append({
            "workflow": wf,
            "run_id": str(run.get("databaseId", "")),
            "run_url": run.get("url", ""),
            "sha": run.get("headSha", "")[:7],
            "age_sec": int(age),
        })
    return persistent


def _alert(repo: str, failure: dict, state: dict, now: float) -> bool:
    """POST to the conversation server's CI-alert endpoint. Updates state
    so we don't re-alert on the same run_id."""
    key = f"{repo}::{failure['workflow']}::{failure['run_id']}"
    if state.get(key, {}).get("alerted"):
        return False  # already sent
    payload = {
        "repo": repo.split("/")[-1],
        "workflow": failure["workflow"],
        "branch": "main",
        "run_url": failure["run_url"],
        "age_min": failure["age_sec"] // 60,
    }
    req = urllib.request.Request(
        CI_ALERT_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"POST failed for {key}: {e}")
        return False
    state[key] = {
        "alerted": True,
        "when": now,
        "repo": repo,
        "workflow": failure["workflow"],
        "run_url": failure["run_url"],
    }
    _log(f"ALERTED {key} age={failure['age_sec']}s")
    return True


def _prune_state(state: dict, now: float) -> dict:
    cutoff = now - STATE_TTL_SEC
    return {k: v for k, v in state.items() if v.get("when", 0) > cutoff}


def main() -> int:
    now = time.time()
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    _unlock_keychain()
    token = _get_gh_token()
    if not token:
        _log("FATAL: no GITHUB_TOKEN in keychain (service=tim-credentials). "
             "Bootstrap the token first; see rotate-token skill.")
        return 2

    state = _load_state()
    n_checked = 0
    n_alerted = 0
    for repo in WATCHED_REPOS:
        runs = _gh_runs(repo, token)
        if not runs:
            continue
        failures = _find_persistent_failures(runs, now_utc)
        for f in failures:
            n_checked += 1
            if _alert(repo, f, state, now):
                n_alerted += 1

    state = _prune_state(state, now)
    _save_state(state)
    _log(f"tick done: checked={n_checked} alerted={n_alerted} repos={len(WATCHED_REPOS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
