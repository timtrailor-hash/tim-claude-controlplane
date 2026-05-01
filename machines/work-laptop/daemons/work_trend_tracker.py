#!/usr/bin/env python3
"""work_trend_tracker.py — rolling 30-day window of work-laptop health.

Reads /tmp/work_health_check_results.json (produced hourly by
work_health_check.py) and appends each item's status into a jsonl
trend file at ~/.claude/work_trends.jsonl. After append, regression
detection runs:

    Regression rule: a probe that has been "green" for at least 7
    consecutive days, then transitions to "amber" or "red", emits a
    bridge_push_notification with the probe name and the latest
    detail string.

State is the jsonl itself — no separate state file. We trim to a
30-day window on every run.

Exits 0 on every error path; the jsonl write is best-effort.
"""
from __future__ import annotations

import json
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
MARKER = HOME / ".claude" / ".work-laptop"
HEALTH_RESULTS = Path("/tmp/work_health_check_results.json")
TREND = HOME / ".claude" / "work_trends.jsonl"
LOG = Path("/tmp/work_trend_tracker.log")
BRIDGE_OUTBOX = HOME / ".claude" / "bridge_outbox"

WINDOW_DAYS = 30
GREEN_STREAK_DAYS = 7  # how long green must hold before a regression is "real"


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_history() -> list[dict]:
    if not TREND.exists():
        return []
    rows: list[dict] = []
    try:
        for line in TREND.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as e:
        _log(f"history read failed: {e}")
    return rows


def _trim_to_window(rows: list[dict]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    out = []
    for r in rows:
        ts = r.get("timestamp")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if t >= cutoff:
            out.append(r)
    return out


def _write_history(rows: list[dict]) -> None:
    try:
        TREND.parent.mkdir(parents=True, exist_ok=True)
        TREND.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + ("\n" if rows else ""))
    except OSError as e:
        _log(f"history write failed: {e}")


def _green_streak_days(rows: list[dict], probe: str, before: datetime) -> float:
    """How many days of consecutive 'green' for `probe` immediately
    preceding `before`?"""
    relevant = []
    for r in rows:
        if r.get("probe") != probe:
            continue
        ts = r.get("timestamp")
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if t < before:
            relevant.append((t, r.get("status")))
    if not relevant:
        return 0.0
    relevant.sort(reverse=True)
    streak_start = None
    for t, status in relevant:
        if status != "green":
            break
        streak_start = t
    if streak_start is None:
        return 0.0
    return (relevant[0][0] - streak_start).total_seconds() / 86400


def _try_bridge_push(title: str, body: str) -> tuple[bool, str]:
    try:
        import importlib.util
        server = HOME / "code" / "claude-bridge" / "tools" / "work_mcp_server.py"
        if not server.is_file():
            return False, f"server file missing at {server}"
        spec = importlib.util.spec_from_file_location("_work_mcp_for_trend", server)
        if spec is None or spec.loader is None:
            return False, "could not build import spec"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "bridge_push_notification"):
            return False, "no bridge_push_notification tool"
        result = mod.bridge_push_notification(title=title, body=body, level="info")
        return True, f"bridge result: {result}"
    except Exception as e:
        return False, f"bridge import/call failed: {e}"


def _drop_outbox(payload: dict) -> tuple[bool, str]:
    try:
        BRIDGE_OUTBOX.mkdir(parents=True, exist_ok=True)
        f = BRIDGE_OUTBOX / f"trend-regression-{uuid.uuid4().hex}.json"
        f.write_text(json.dumps(payload, indent=2))
        return True, f"dropped {f}"
    except Exception as e:
        return False, f"outbox drop failed: {e}"


def _emit_regression(probe: str, status: str, detail: str, streak_days: float) -> None:
    title = f"Regression: {probe} → {status}"
    body = (
        f"Probe '{probe}' was green for {streak_days:.1f} days, now {status}.\n"
        f"Detail: {detail}\n"
        f"Source: /tmp/work_health_check_results.json"
    )
    sent, sd = _try_bridge_push(title, body)
    if sent:
        _log(f"bridge_push for {probe}: {sd}")
    else:
        _log(f"bridge push unavailable ({sd}); falling back to outbox")
        _drop_outbox({
            "type": "bridge_push_notification",
            "title": title,
            "body": body,
            "level": "info",
            "probe": probe,
            "status": status,
            "detail": detail,
            "streak_days": streak_days,
            "created_at": _now_iso(),
        })


def main() -> int:
    if not MARKER.exists():
        _log(f"marker {MARKER} missing; exiting 0")
        return 0

    if not HEALTH_RESULTS.exists():
        _log(f"{HEALTH_RESULTS} missing; nothing to do")
        return 0

    try:
        latest = json.loads(HEALTH_RESULTS.read_text())
    except Exception as e:
        _log(f"could not parse {HEALTH_RESULTS}: {e}")
        return 0

    items = latest.get("items") or []
    ts = latest.get("timestamp") or _now_iso()

    history = _trim_to_window(_load_history())

    # Detect regressions BEFORE we append the new rows, so we measure
    # the streak that PRECEDED this run.
    try:
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        ts_dt = datetime.now(timezone.utc)

    new_rows: list[dict] = []
    for it in items:
        probe = it.get("name")
        status = it.get("status")
        detail = it.get("detail", "")
        if not probe or not status:
            continue
        new_rows.append({
            "timestamp": ts,
            "probe": probe,
            "status": status,
            "detail": detail,
        })

        if status in ("amber", "red"):
            streak = _green_streak_days(history, probe, ts_dt)
            if streak >= GREEN_STREAK_DAYS:
                _emit_regression(probe, status, detail, streak)

    history.extend(new_rows)
    history = _trim_to_window(history)
    _write_history(history)
    _log(f"appended {len(new_rows)} row(s); history now {len(history)} row(s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"fatal: {e}")
        _log(traceback.format_exc())
        sys.exit(0)
