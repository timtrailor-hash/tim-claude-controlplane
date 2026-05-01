#!/usr/bin/env python3
"""Automated quarterly credential rotation.

Rotates managed secrets that live in the macOS login keychain. Runs as
a LaunchAgent (`com.timtrailor.credential-rotation`) on a 90-day cadence.

Secrets managed here are non-OAuth, non-API-key credentials where WE
own the value (password strings for services we run). OAuth tokens
auto-refresh via token_refresh.py; API keys belong to vendors and must
be rotated via their consoles.

Each entry in SECRETS specifies:
  name       — human identifier
  service    — keychain `-s` (service) attribute
  account    — keychain `-a` (account) attribute
  post_hook  — shell command to run after rotation (restart daemon, etc.)
  max_age_d  — target rotation cadence; soft alert if exceeded.

State file: ~/code/.credential_rotation_state.json — tracks last
rotation timestamp per secret. Used by the acceptance test DRIFT9.
"""
from __future__ import annotations

import datetime
import json
import os
import secrets
import string
import subprocess
import sys
import urllib.request
from pathlib import Path

HOME = Path.home()
STATE = HOME / "code" / ".credential_rotation_state.json"
KEYCHAIN_PASS = HOME / ".keychain_pass"  # accepted-risk root-of-trust bypass

SECRETS = [
    {
        "name": "ttyd-auth",
        "service": "ttyd-auth",
        "account": "timtrailor",
        "post_hook": f"launchctl kickstart -k gui/{os.getuid()}/com.timtrailor.ttyd-tunnel",
        "max_age_d": 90,
    },
]


def _ntfy(subject: str, body: str, priority: str = "3") -> None:
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/timtrailor-claude",
            data=body.encode(),
            headers={"Title": subject, "Priority": priority, "Tags": "key,lock"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print(f"WARN: ntfy failed: {e}", file=sys.stderr)


def _unlock_keychain() -> None:
    """SSH / LaunchAgent sessions can't prompt for keychain; unlock via
    ~/.keychain_pass (documented accepted risk, see hosts/mac-mini.yaml +
    services.yaml unlock-keychain entry)."""
    if not KEYCHAIN_PASS.exists():
        raise SystemExit(f"FATAL: {KEYCHAIN_PASS} missing; cannot unlock keychain")
    pw = KEYCHAIN_PASS.read_text().strip()
    kc = str(HOME / "Library" / "Keychains" / "login.keychain-db")
    subprocess.run(["security", "unlock-keychain", "-p", pw, kc], check=True)
    subprocess.run(["security", "set-keychain-settings", kc], check=True)


def _load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=2, sort_keys=True))


def _random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _rotate_one(entry: dict, state: dict) -> bool:
    """Rotate a single secret. Returns True on success."""
    name = entry["name"]
    new_pw = _random_password()
    result = subprocess.run(
        ["security", "add-generic-password",
         "-a", entry["account"],
         "-s", entry["service"],
         "-w", new_pw,
         "-U"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        _ntfy(
            f"Credential rotation FAILED: {name}",
            f"rc={result.returncode} stderr={result.stderr.strip()[:200]}",
            priority="5",
        )
        print(f"FAIL rotate {name}: {result.stderr}", file=sys.stderr)
        return False

    post = entry.get("post_hook")
    if post:
        ph = subprocess.run(post, shell=True, capture_output=True, text=True, timeout=30)
        if ph.returncode != 0:
            _ntfy(
                f"Post-hook failed after rotating {name}",
                f"rc={ph.returncode} stderr={ph.stderr.strip()[:200]}",
                priority="4",
            )

    state[name] = {
        "rotated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "length": len(new_pw),
    }
    print(f"OK rotated {name} (new length {len(new_pw)})")
    return True


def _days_since(iso: str) -> float:
    dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    delta = datetime.datetime.now(datetime.timezone.utc) - dt
    return delta.total_seconds() / 86400


HEARTBEAT = HOME / "code" / ".credential_rotation_heartbeat"


def _touch_heartbeat() -> None:
    """Write a liveness marker every run, even when nothing rotated.
    Gemini Round-1 action #2: reduces detection latency for a silently
    failed daemon from ~120 days to whatever the heartbeat check window is."""
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(datetime.datetime.now(datetime.timezone.utc).isoformat())


def main() -> int:
    _touch_heartbeat()
    _unlock_keychain()
    state = _load_state()

    any_rotated = False
    any_failed = False
    for entry in SECRETS:
        name = entry["name"]
        max_age = entry["max_age_d"]
        last = state.get(name, {}).get("rotated_at")
        if last and _days_since(last) < max_age:
            print(f"SKIP {name}: age {_days_since(last):.1f}d < {max_age}d")
            continue
        print(f"ROTATE {name}: age "
              f"{_days_since(last):.1f}d >= {max_age}d" if last else f"ROTATE {name}: first run")
        if _rotate_one(entry, state):
            any_rotated = True
        else:
            any_failed = True

    _save_state(state)

    if any_rotated and not any_failed:
        _ntfy("Credential rotation complete",
              "\n".join(f"{e['name']}: {state[e['name']]['rotated_at']}" for e in SECRETS
                        if e["name"] in state))

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
