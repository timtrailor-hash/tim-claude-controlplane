#!/usr/bin/env python3
"""Daily trend tracker — appends current system metrics to a jsonl trend file
and, on Mondays, emails a digest. Wired as a LaunchAgent that fires daily.

Metrics captured daily:
 - conversation_server.py line count (monolith trajectory)
 - acceptance_tests compliance score (from /tmp/acceptance_results.json)
 - count of lessons.md patterns (new failure classes appearing)
 - verify.sh pass/fail counts

The point is to make DECAY visible weekly without anyone having to look.
Slow drift is invisible day-to-day; a Monday email forces review.
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
HISTORY = HOME / "code" / "trend_history.jsonl"
CONV_SERVER = HOME / "code" / "claude-mobile" / "conversation_server.py"
ACCEPTANCE_JSON = Path("/tmp/acceptance_results.json")
LESSONS = (HOME / ".claude" / "projects" / "-Users-timtrailor-code" / "memory"
           / "topics" / "lessons.md")
VERIFY_SH = HOME / "code" / "tim-claude-controlplane" / "verify.sh"


def collect_metrics() -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    m = {"timestamp": now.isoformat(), "date": now.date().isoformat()}

    if CONV_SERVER.exists():
        m["monolith_lines"] = sum(1 for _ in CONV_SERVER.open())

    # Commit bypasses in the last 24h (from post-commit audit log).
    audit = HOME / "code" / "commit_audit.log"
    if audit.exists():
        cutoff = (now - datetime.timedelta(hours=24)).isoformat()
        with audit.open() as f:
            recent_bypasses = [
                line for line in f
                if line.strip() and line > f'{{"timestamp":"{cutoff}'
            ]
        m["bypass_count_24h"] = len(recent_bypasses)
        # Gemini R1 #3: bound the log. Rotate at 1 MB; keep 3 generations.
        size_bytes = audit.stat().st_size
        if size_bytes > 1 * 1024 * 1024:
            for n in (3, 2, 1):
                old = audit.with_suffix(f".log.{n}")
                nxt = audit.with_suffix(f".log.{n + 1}")
                if old.exists() and n == 3:
                    old.unlink()
                elif old.exists():
                    old.rename(nxt)
            audit.rename(audit.with_suffix(".log.1"))
            audit.touch()

    if ACCEPTANCE_JSON.exists():
        try:
            d = json.loads(ACCEPTANCE_JSON.read_text())
            m["compliance_score"] = d.get("score")
            m["compliance_pass"] = d.get("pass")
            m["compliance_total"] = d.get("total")
            m["compliance_fails"] = d.get("fails") or []
        except json.JSONDecodeError:
            m["compliance_score"] = None

    if LESSONS.exists():
        text = LESSONS.read_text()
        m["lessons_pattern_count"] = len(re.findall(r"^## Pattern \d+:", text, re.M))

    if VERIFY_SH.exists():
        try:
            r = subprocess.run(
                ["bash", str(VERIFY_SH)],
                capture_output=True, text=True, timeout=120,
            )
            mm = re.search(r"(\d+) passed, (\d+) failed, (\d+) warnings", r.stdout)
            if mm:
                m["verify_pass"] = int(mm.group(1))
                m["verify_fail"] = int(mm.group(2))
                m["verify_warn"] = int(mm.group(3))
        except subprocess.TimeoutExpired:
            m["verify_error"] = "timeout"
    return m


def append_entry(m: dict) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a") as f:
        f.write(json.dumps(m) + "\n")


def _read_history(n: int = 14) -> list[dict]:
    if not HISTORY.exists():
        return []
    with HISTORY.open() as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    out = []
    for raw in lines[-n:]:
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def build_digest() -> str:
    entries = _read_history(14)
    if len(entries) < 2:
        return ""  # not enough history yet
    first, last = entries[0], entries[-1]

    def delta(key: str) -> str:
        a, b = first.get(key), last.get(key)
        if a is None or b is None:
            return "n/a"
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            diff = b - a
            sign = "+" if diff > 0 else ""
            return f"{b} ({sign}{diff} over {len(entries)-1} days)"
        return str(b)

    lines = [
        "=== Weekly trend — Tim's control plane ===",
        f"Window: {first['date']} -> {last['date']} ({len(entries)} samples)",
        "",
        f"Monolith lines:       {delta('monolith_lines')}",
        f"Compliance score:     {delta('compliance_score')}%",
        f"Lessons patterns:     {delta('lessons_pattern_count')}",
        f"verify.sh pass/fail:  {last.get('verify_pass','?')}/{last.get('verify_fail','?')}",
        "",
        "Latest sample:",
        f"  compliance fails: {last.get('compliance_fails') or 'none'}",
        "",
        "Action: if monolith grew, compliance dropped, or patterns increased,",
        "this week is a good time for a decomposition/review session.",
    ]
    return "\n".join(lines)


def _send_digest_email(body: str) -> None:
    try:
        sys.path.insert(0, str(HOME / "code"))
        import credentials
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = "Weekly control-plane trend"
        msg["From"] = credentials.SMTP_USER
        msg["To"] = credentials.SMTP_USER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            s.login(credentials.SMTP_USER, credentials.SMTP_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f"WARN: digest email failed: {e}", file=sys.stderr)


def main() -> int:
    m = collect_metrics()
    append_entry(m)
    print(json.dumps(m, indent=2))

    # Monday = 0 in isoweekday (Mon=1). Use Python weekday (Mon=0) for clarity.
    today = datetime.date.today()
    if today.weekday() == 0:  # Monday
        digest = build_digest()
        if digest:
            _send_digest_email(digest)
            print("--- Monday digest emailed ---")
            print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
