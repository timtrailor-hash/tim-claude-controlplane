#!/usr/bin/env python3
"""
BGT Live Semi-Finals 2026 — new-date monitor.

Runs every 10 minutes via LaunchAgent com.timtrailor.bgt-date-monitor.
Fetches the Applause Store booking page, extracts every show date listed,
and alerts (email + ntfy push + Slack DM) the moment a date appears that
isn't already in the known set. Tim has already applied for 25 Apr 2026,
so that date is the seeded baseline and is silent.
"""

import json
import logging
import re
import smtplib
import sys
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path

HOME = Path.home()
CODE = HOME / "code"
STATE_FILE = CODE / ".bgt_known_dates.json"
LOG_FILE = Path("/tmp/bgt_date_monitor.log")
URL = "https://www.applausestore.com/book-britain-got-talent-live-semi-finals-2026"
BASELINE = {"Sat 25th Apr 2026"}
SLACK_USER_ID = "U03H1AN51MZ"  # Tim Trailor
EMAIL_RECIPIENTS = ["timtrailor@gmail.com", "gemmajackson8@gmail.com"]
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ── Logging ──────────────────────────────────────────────────────────────
log = logging.getLogger("bgt_date_monitor")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
_fh = RotatingFileHandler(LOG_FILE, maxBytes=1_048_576, backupCount=3)
_fh.setFormatter(_fmt)
log.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
log.addHandler(_sh)

# ── Credentials ──────────────────────────────────────────────────────────
sys.path.insert(0, str(CODE))
try:
    from credentials import (
        SMTP_HOST,
        SMTP_PORT,
        SMTP_USER,
        SMTP_PASS,
        NTFY_TOPIC,
        SLACK_BOT_TOKEN,
    )
except ImportError as exc:
    log.error("Failed to import credentials: %s", exc)
    SMTP_HOST = SMTP_USER = SMTP_PASS = NTFY_TOPIC = SLACK_BOT_TOKEN = ""
    SMTP_PORT = 587

# ── Date parsing ─────────────────────────────────────────────────────────
DATE_RE = re.compile(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+"
    r"(\d{1,2})(?:st|nd|rd|th)\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
    r"(20\d{2})",
    re.IGNORECASE,
)


def parse_dates(html: str) -> set[str]:
    """Return canonicalised date strings like 'Sat 25th Apr 2026'."""
    out: set[str] = set()
    for dow, day, mon, year in DATE_RE.findall(html):
        day_int = int(day)
        suffix = (
            "th"
            if 11 <= day_int % 100 <= 13
            else {1: "st", 2: "nd", 3: "rd"}.get(day_int % 10, "th")
        )
        out.add(
            f"{dow.title()[:3]} {day_int}{suffix} {mon.title()[:3]} {year}"
        )
    return out


def fetch_page() -> str:
    req = urllib.request.Request(URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── State ────────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            data["known_dates"] = set(data.get("known_dates", []))
            return data
        except Exception as exc:
            log.warning("State file unreadable, reseeding: %s", exc)
    return {"known_dates": set(BASELINE), "last_check": None}


def save_state(state: dict) -> None:
    payload = {
        "known_dates": sorted(state["known_dates"]),
        "last_check": state.get("last_check"),
    }
    STATE_FILE.write_text(json.dumps(payload, indent=2))


# ── Notifications ────────────────────────────────────────────────────────
def send_email(subject: str, body: str) -> None:
    if not SMTP_USER or not SMTP_PASS:
        log.warning("No SMTP creds, skipping email")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(EMAIL_RECIPIENTS)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg, to_addrs=EMAIL_RECIPIENTS)
        log.info("Email sent to %s", EMAIL_RECIPIENTS)
    except Exception as exc:
        log.warning("Email failed: %s", exc)


def send_push(title: str, body: str) -> None:
    if not NTFY_TOPIC:
        return
    try:
        data = json.dumps(
            {
                "topic": NTFY_TOPIC,
                "title": title,
                "message": body,
                "priority": 5,
                "tags": ["tada", "bgt"],
            }
        ).encode()
        req = urllib.request.Request(
            "https://ntfy.sh",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        log.info("ntfy push sent")
    except Exception as exc:
        log.warning("ntfy push failed: %s", exc)


def send_slack_dm(message: str) -> None:
    if not SLACK_BOT_TOKEN:
        log.warning("No SLACK_BOT_TOKEN, skipping Slack DM")
        return
    try:
        data = json.dumps(
            {
                "channel": SLACK_USER_ID,
                "text": f":tada: *BGT Date Alert*\n{message}",
            }
        ).encode()
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if resp.get("ok"):
            log.info("Slack DM sent")
        else:
            log.warning("Slack API error: %s", resp.get("error", "unknown"))
    except Exception as exc:
        log.warning("Slack DM failed: %s", exc)


def notify_new_dates(new_dates: set[str], all_current: set[str]) -> None:
    new_sorted = sorted(new_dates)
    all_sorted = sorted(all_current)
    n = len(new_sorted)
    subject = f"BGT: {n} new show date{'s' if n != 1 else ''} added"
    body = (
        f"{n} new BGT Live Semi-Finals 2026 date"
        f"{'s have' if n != 1 else ' has'} appeared on Applause Store:\n\n"
        + "\n".join(f"  • {d}" for d in new_sorted)
        + "\n\nFull list now showing:\n"
        + "\n".join(f"  - {d}" for d in all_sorted)
        + f"\n\n{URL}\n"
    )
    send_email(subject, body)
    send_push(subject, ", ".join(new_sorted))
    send_slack_dm(body)


# ── Main ─────────────────────────────────────────────────────────────────
def main() -> int:
    log.info("BGT date monitor check starting")
    state = load_state()

    try:
        html = fetch_page()
    except Exception as exc:
        log.warning("Fetch failed (state untouched): %s", exc)
        return 0

    current = parse_dates(html)
    log.info("parsed %d date(s): %s", len(current), sorted(current) or "[]")

    if not current:
        log.warning("No dates parsed — page format may have changed; not updating state")
        return 0

    new = current - state["known_dates"]
    if new:
        log.info("NEW dates detected: %s", sorted(new))
        notify_new_dates(new, current)
        state["known_dates"] |= current
    else:
        log.info("0 new dates")

    state["last_check"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
