#!/usr/bin/env python3
"""
Autonomous Task Runner — persistent retry loop for Claude Code tasks.

Usage:
    python3 autonomous_runner.py --prompt "do X and Y" [--max-retries 5] [--email tim@example.com]

How it works:
    1. Runs `claude -p "prompt"` non-interactively from the correct working directory
    2. If Claude fails, times out, or produces no output → retry with exponential backoff
    3. On success → emails the result
    4. On final failure → emails what happened + all attempt logs
    5. Keeps going until email is confirmed sent (retries email too)
    6. Logs everything to /tmp/autonomous_runner.log

This script is designed to be launched with nohup and forgotten:
    nohup python3 autonomous_runner.py --prompt "..." &
"""

import argparse
import json
import os
import smtplib
import subprocess
import sys
import time
import traceback
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

WORK_DIR = os.path.expanduser("~/Documents/Claude code")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
LOG_FILE = "/tmp/autonomous_runner.log"
MARKER_FILE = "/tmp/autonomous_task_active"
EMAIL_SENT_MARKER = "/tmp/autonomous_email_sent"

# Load credentials from credentials.py
def load_creds():
    """Load all credentials from credentials.py."""
    creds_paths = [
        os.path.expanduser("~/Documents/Claude code/credentials.py"),
        os.path.expanduser("~/code/credentials.py"),
    ]
    for path in creds_paths:
        if os.path.exists(path):
            creds = {}
            with open(path) as f:
                exec(f.read(), creds)
            return creds
    raise FileNotFoundError("credentials.py not found")


def load_smtp_creds():
    """Load SMTP credentials from credentials.py."""
    creds = load_creds()
    return {
        "host": creds.get("SMTP_HOST", "smtp.gmail.com"),
        "port": creds.get("SMTP_PORT", 587),
        "user": creds.get("SMTP_USER", ""),
        "password": creds.get("SMTP_PASS", ""),
    }


# ── Logging ────────────────────────────────────────────────────────────────────

def log(msg):
    """Log to file and stdout."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Claude execution ──────────────────────────────────────────────────────────

def run_claude(prompt, timeout_seconds=600):
    """
    Run claude -p non-interactively.
    Returns (success: bool, output: str, duration: float).
    """
    log(f"Starting Claude with timeout={timeout_seconds}s")
    log(f"Prompt (first 200 chars): {prompt[:200]}...")

    # Build clean environment — strip API key to force subscription auth
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    # Ensure PATH includes claude
    paths = [
        os.path.expanduser("~/.local/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    env["PATH"] = ":".join(paths) + ":" + env.get("PATH", "")

    start = time.time()
    try:
        result = subprocess.run(
            [
                CLAUDE_BIN,
                "-p", prompt,
                "--output-format", "text",
                "--max-turns", "100",
                "--model", "claude-opus-4-6",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=WORK_DIR,
            env=env,
        )
        duration = time.time() - start

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            log(f"Claude exited with code {result.returncode} after {duration:.0f}s")
            if stderr:
                log(f"stderr: {stderr[:500]}")
            return False, f"Exit code {result.returncode}\nstderr: {stderr}\nstdout: {stdout}", duration

        if not stdout or len(stdout) < 50:
            log(f"Claude produced insufficient output ({len(stdout)} chars) after {duration:.0f}s")
            return False, f"Insufficient output ({len(stdout)} chars): {stdout}", duration

        log(f"Claude succeeded after {duration:.0f}s ({len(stdout)} chars output)")
        return True, stdout, duration

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        log(f"Claude timed out after {duration:.0f}s")
        return False, f"Timed out after {timeout_seconds}s", duration
    except Exception as e:
        duration = time.time() - start
        log(f"Claude execution error: {e}")
        return False, f"Exception: {e}\n{traceback.format_exc()}", duration


# ── Notification channels ─────────────────────────────────────────────────────

def send_email(to_email, subject, body_text, body_html=None, max_retries=3):
    """Send email via SMTP with retries. Returns True if sent."""
    creds = load_smtp_creds()

    for attempt in range(1, max_retries + 1):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = creds["user"]
            msg["To"] = to_email

            msg.attach(MIMEText(body_text, "plain"))
            if body_html:
                msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(creds["host"], creds["port"]) as server:
                server.starttls()
                server.login(creds["user"], creds["password"])
                server.send_message(msg)

            log(f"Email sent successfully to {to_email} (attempt {attempt})")

            Path(EMAIL_SENT_MARKER).write_text(
                json.dumps({
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                    "to": to_email,
                    "subject": subject,
                    "channel": "email",
                })
            )
            return True

        except Exception as e:
            log(f"Email attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(5 * attempt)

    log("All email attempts failed")
    return False


def send_ntfy(subject, body_text, max_retries=3):
    """Send push notification via ntfy.sh. Returns True if sent."""
    try:
        creds = load_creds()
        topic = creds.get("NTFY_TOPIC")
        if not topic:
            log("ntfy: No NTFY_TOPIC in credentials.py, skipping")
            return False
    except Exception as e:
        log(f"ntfy: Could not load credentials: {e}")
        return False

    # ntfy has a 4096-byte message limit — truncate body
    max_body = 3800
    truncated = body_text[:max_body] + ("\n\n[TRUNCATED — full result in email or /tmp/autonomous_result.txt]" if len(body_text) > max_body else "")

    for attempt in range(1, max_retries + 1):
        try:
            data = truncated.encode("utf-8")
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=data,
                method="POST",
            )
            req.add_header("Title", subject)
            req.add_header("Priority", "urgent")
            req.add_header("Tags", "robot,white_check_mark" if "Complete" in subject else "robot,x")

            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    log(f"ntfy sent successfully (attempt {attempt})")
                    Path(EMAIL_SENT_MARKER).write_text(
                        json.dumps({
                            "sent_at": datetime.now(timezone.utc).isoformat(),
                            "subject": subject,
                            "channel": "ntfy",
                        })
                    )
                    return True
                else:
                    log(f"ntfy attempt {attempt}: HTTP {resp.status}")
        except Exception as e:
            log(f"ntfy attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(3 * attempt)

    log("All ntfy attempts failed")
    return False


def send_slack_dm(subject, body_text, max_retries=3):
    """Send Slack DM via Slack API. Returns True if sent."""
    try:
        creds = load_creds()
        token = creds.get("SLACK_BOT_TOKEN") or creds.get("SLACK_USER_TOKEN")
        user_id = creds.get("SLACK_USER_ID")
        if not token or not user_id:
            log("Slack: Missing SLACK_BOT_TOKEN/SLACK_USER_TOKEN or SLACK_USER_ID, skipping")
            return False
    except Exception as e:
        log(f"Slack: Could not load credentials: {e}")
        return False

    # Slack message limit is ~40k chars but keep it reasonable
    max_body = 3800
    truncated = body_text[:max_body] + ("\n\n_[Truncated — full result in email or /tmp/autonomous_result.txt]_" if len(body_text) > max_body else "")
    message = f"*{subject}*\n\n{truncated}"

    for attempt in range(1, max_retries + 1):
        try:
            # Post directly using user_id as channel (works with chat:write scope)
            msg_data = json.dumps({"channel": user_id, "text": message}).encode("utf-8")
            msg_req = urllib.request.Request(
                "https://slack.com/api/chat.postMessage",
                data=msg_data,
                method="POST",
            )
            msg_req.add_header("Authorization", f"Bearer {token}")
            msg_req.add_header("Content-Type", "application/json; charset=utf-8")

            with urllib.request.urlopen(msg_req, timeout=15) as resp:
                msg_result = json.loads(resp.read().decode("utf-8"))
                if msg_result.get("ok"):
                    log(f"Slack DM sent successfully (attempt {attempt})")
                    Path(EMAIL_SENT_MARKER).write_text(
                        json.dumps({
                            "sent_at": datetime.now(timezone.utc).isoformat(),
                            "subject": subject,
                            "channel": "slack",
                        })
                    )
                    return True
                else:
                    log(f"Slack chat.postMessage failed: {msg_result.get('error')}")
                    raise Exception(msg_result.get("error", "unknown"))

        except Exception as e:
            log(f"Slack attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                time.sleep(5 * attempt)

    log("All Slack DM attempts failed")
    return False


def notify(to_email, subject, body_text, body_html=None):
    """
    Notification cascade: Email → ntfy → Slack → file fallback.
    Tries each channel. Returns True if ANY channel succeeded.
    """
    channels_tried = []
    any_sent = False

    # 1. Email (primary)
    log("Notification cascade: trying email...")
    if send_email(to_email, subject, body_text, body_html, max_retries=3):
        channels_tried.append("email:OK")
        any_sent = True
    else:
        channels_tried.append("email:FAILED")

    # 2. ntfy (always send as backup — it's instant push)
    log("Notification cascade: trying ntfy...")
    if send_ntfy(subject, body_text, max_retries=3):
        channels_tried.append("ntfy:OK")
        any_sent = True
    else:
        channels_tried.append("ntfy:FAILED")

    # 3. Slack DM (if email failed, try Slack as another route)
    if not any_sent:
        log("Notification cascade: email + ntfy both failed, trying Slack...")
        if send_slack_dm(subject, body_text, max_retries=3):
            channels_tried.append("slack:OK")
            any_sent = True
        else:
            channels_tried.append("slack:FAILED")

    # 4. File fallback (always write as safety net)
    Path("/tmp/autonomous_result.txt").write_text(body_text)
    log(f"Result also saved to /tmp/autonomous_result.txt")

    log(f"Notification cascade result: {', '.join(channels_tried)} | any_sent={any_sent}")
    return any_sent


# ── Main runner ────────────────────────────────────────────────────────────────

def run_autonomous_task(prompt, to_email, max_retries=5, timeout=600):
    """
    Main loop: run Claude until success, then email result.
    On final failure, email the failure report.
    """
    log("=" * 70)
    log("AUTONOMOUS TASK RUNNER STARTED")
    log(f"Max retries: {max_retries}")
    log(f"Timeout per attempt: {timeout}s")
    log(f"Email to: {to_email}")
    log(f"Working dir: {WORK_DIR}")
    log(f"Full prompt:\n{prompt}")
    log("=" * 70)

    # Write active marker
    Path(MARKER_FILE).write_text(
        json.dumps({
            "started_at": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt[:500],
            "email": to_email,
            "pid": os.getpid(),
        })
    )

    attempts = []
    success = False
    final_output = ""

    for attempt in range(1, max_retries + 1):
        log(f"\n--- Attempt {attempt}/{max_retries} ---")

        ok, output, duration = run_claude(prompt, timeout_seconds=timeout)
        attempts.append({
            "attempt": attempt,
            "success": ok,
            "duration": duration,
            "output_len": len(output),
            "output_preview": output[:300],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if ok:
            success = True
            final_output = output
            log(f"Task completed successfully on attempt {attempt}")
            break
        else:
            log(f"Attempt {attempt} failed. Output: {output[:200]}")
            if attempt < max_retries:
                backoff = min(30 * (2 ** (attempt - 1)), 300)  # 30s, 60s, 120s, 240s, 300s
                log(f"Retrying in {backoff}s...")
                time.sleep(backoff)

    # ── Build email ────────────────────────────────────────────────────────

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if success:
        subject = f"Autonomous Task Complete — {ts}"
        body_text = f"""Your autonomous task has completed successfully.

PROMPT:
{prompt}

RESULT:
{final_output}

---
Completed on attempt {len(attempts)}/{max_retries}
Total duration: {sum(a['duration'] for a in attempts):.0f}s
Log: {LOG_FILE}
"""
    else:
        subject = f"Autonomous Task FAILED after {max_retries} attempts — {ts}"
        attempt_log = "\n\n".join(
            f"--- Attempt {a['attempt']} ({a['duration']:.0f}s) ---\n{a['output_preview']}"
            for a in attempts
        )
        body_text = f"""Your autonomous task failed after {max_retries} attempts.

PROMPT:
{prompt}

ATTEMPT LOG:
{attempt_log}

---
All {max_retries} attempts failed.
Full log: {LOG_FILE}
"""

    # ── Send notification (cascade: email → ntfy → slack → file) ───────────

    notified = notify(to_email, subject, body_text)

    if not notified:
        log("CRITICAL: ALL notification channels failed. Result saved to /tmp/autonomous_result.txt")

    # Clean up marker
    try:
        Path(MARKER_FILE).unlink(missing_ok=True)
    except Exception:
        pass

    log("AUTONOMOUS TASK RUNNER FINISHED")
    return success and notified


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Task Runner for Claude Code")
    parser.add_argument("--prompt", required=True, help="The task prompt for Claude")
    parser.add_argument("--email", default="timtrailor@gmail.com", help="Email address for results")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retry attempts (default: 5)")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per attempt in seconds (default: 600)")
    args = parser.parse_args()

    try:
        run_autonomous_task(args.prompt, args.email, args.max_retries, args.timeout)
    except Exception as e:
        log(f"FATAL ERROR: {e}\n{traceback.format_exc()}")
        # Last-ditch notification via cascade
        crash_body = f"The runner itself crashed.\n\nError: {e}\n\nPrompt was:\n{args.prompt}\n\nLog: {LOG_FILE}"
        try:
            notify(args.email, "Autonomous Task Runner CRASHED", crash_body)
        except Exception:
            Path("/tmp/autonomous_crash.txt").write_text(crash_body)
