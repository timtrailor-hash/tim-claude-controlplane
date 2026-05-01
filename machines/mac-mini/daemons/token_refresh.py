#!/usr/bin/env python3
"""
Unified OAuth token refresher — keeps all tokens alive automatically.

Refreshes:
  1. Claude Code OAuth (subscription auth for CLI)
  2. Google OAuth (Docs, Gmail, Calendar APIs)

Runs via launchd every 30 minutes. Emails Tim on failure (after retries exhausted).

Usage:
  python3 token_refresh.py          # Run once
  python3 token_refresh.py --check  # Just report status, don't refresh

Logs: /tmp/token_refresh.log
"""

import argparse
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
import random

sys.path.insert(0, "/Users/timtrailor/code")
from shared_utils import configure_logging

LOG_FILE = "/tmp/token_refresh.log"
logger = configure_logging("token_refresh", LOG_FILE)

# ── Paths ────────────────────────────────────────────────────────────
CLAUDE_CREDS = os.path.expanduser("~/.claude/.credentials.json")
GOOGLE_TOKEN = os.path.expanduser("~/code/claude-mobile/google_token.json")
CREDENTIALS_PY = os.path.expanduser("~/code/credentials.py")

# ── Claude Code OAuth ────────────────────────────────────────────────
CLAUDE_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# ── Thresholds ───────────────────────────────────────────────────────
# Refresh if token expires within this many minutes
REFRESH_THRESHOLD_MINUTES = 45

# ── Retry config ─────────────────────────────────────────────────────
MAX_RETRIES = 3
# Backoff delays in seconds: ~60s, ~300s, ~900s (with jitter)
BACKOFF_BASE_SECONDS = [60, 300, 900]

# ── Rate limit state file (persists across runs) ────────────────────
RATE_LIMIT_STATE = os.path.expanduser("~/code/.token_refresh_state.json")


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def send_alert(subject, body):
    """Send email alert on failure."""
    try:
        from credentials import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
        msg = MIMEText(body)
        msg["From"] = SMTP_USER
        msg["To"] = "timtrailor@gmail.com"
        msg["Subject"] = f"[Token Refresh] {subject}"
        with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT)) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, "timtrailor@gmail.com", msg.as_string())
        log(f"Alert sent: {subject}")
    except Exception as e:
        log(f"Alert email failed: {e}")


# ── Rate limit backoff state ────────────────────────────────────────

def load_rate_limit_state():
    """Load persistent rate limit state."""
    if os.path.exists(RATE_LIMIT_STATE):
        try:
            with open(RATE_LIMIT_STATE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_rate_limit_state(state):
    """Save rate limit state to disk."""
    try:
        with open(RATE_LIMIT_STATE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log(f"WARNING: Could not save rate limit state: {e}")


def is_backed_off(service):
    """Check if we should skip this service due to recent rate limiting."""
    state = load_rate_limit_state()
    info = state.get(service)
    if not info:
        return False
    
    backoff_until = info.get("backoff_until", 0)
    if time.time() < backoff_until:
        remaining = (backoff_until - time.time()) / 60
        last_429 = info.get("last_429_time", "unknown")
        log(f"{service}: Still backing off for {remaining:.0f} more minutes (rate limited at {last_429})")
        return True
    return False


def record_rate_limit(service, attempt):
    """Record a rate limit hit and set backoff."""
    state = load_rate_limit_state()
    consecutive = state.get(service, {}).get("consecutive_429s", 0) + 1
    
    # Exponential backoff: 5min, 15min, 30min, capped at 60min
    backoff_minutes = min(60, 5 * (2 ** (consecutive - 1)))
    jitter = random.uniform(0, backoff_minutes * 0.2)  # 20% jitter
    backoff_seconds = (backoff_minutes + jitter) * 60
    
    state[service] = {
        "consecutive_429s": consecutive,
        "last_429_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "backoff_until": time.time() + backoff_seconds,
    }
    save_rate_limit_state(state)
    log(
        f"{service}: Rate limited (attempt {attempt}) — backing off "
        f"{backoff_minutes:.0f}min (consecutive: {consecutive})"
    )


def clear_rate_limit(service):
    """Clear rate limit state on success."""
    state = load_rate_limit_state()
    if service in state:
        del state[service]
        save_rate_limit_state(state)


def retry_with_backoff(func, service):
    """
    Run func with up to MAX_RETRIES attempts and exponential backoff.
    Only sends alert email after all retries exhausted.
    Returns (success, error_msg).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        success, error_msg, is_rate_limit = func(suppress_alert=True)
        
        if success:
            clear_rate_limit(service)
            return True

        if is_rate_limit:
            record_rate_limit(service, attempt)
            if attempt < MAX_RETRIES:
                # Within-run backoff: shorter delays for retries within a single run
                delay = BACKOFF_BASE_SECONDS[min(attempt - 1, len(BACKOFF_BASE_SECONDS) - 1)]
                jitter = random.uniform(0, delay * 0.3)
                total_delay = delay + jitter
                log(f"{service}: Retry {attempt}/{MAX_RETRIES} after {total_delay:.0f}s backoff...")
                time.sleep(total_delay)
            continue
        
        # Non-rate-limit error — retry with shorter delay
        if attempt < MAX_RETRIES:
            delay = 10 * attempt + random.uniform(0, 5)
            log(f"{service}: Retry {attempt}/{MAX_RETRIES} after {delay:.0f}s...")
            time.sleep(delay)
    
    # All retries exhausted — NOW send alert
    log(f"{service}: All {MAX_RETRIES} retries exhausted")
    return False


# ── Claude Code OAuth Refresh ────────────────────────────────────────

def get_claude_token_status():
    """Return (access_token, refresh_token, expires_at_ms, remaining_minutes)."""
    if not os.path.exists(CLAUDE_CREDS):
        return None, None, None, None
    with open(CLAUDE_CREDS) as f:
        creds = json.load(f)
    oauth = creds.get("claudeAiOauth", {})
    expires_at = oauth.get("expiresAt", 0)
    remaining = (expires_at / 1000 - time.time()) / 60
    return oauth.get("accessToken"), oauth.get("refreshToken"), expires_at, remaining


def _refresh_claude_once(suppress_alert=False):
    """
    Single attempt to refresh Claude token.
    Returns (success, error_msg, is_rate_limit).
    """
    import urllib.request
    import urllib.error

    _, refresh_token, _, remaining = get_claude_token_status()
    if not refresh_token:
        log("ERROR: No Claude refresh token found")
        return False, "No refresh token", False

    log(f"Claude token expires in {remaining:.0f} min — refreshing...")

    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLAUDE_CLIENT_ID,
    }).encode()

    req = urllib.request.Request(
        CLAUDE_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-code/2.1.52",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        is_rate_limit = e.code == 429
        error_msg = f"HTTP {e.code}: {body}"
        log(f"ERROR: Claude refresh failed {error_msg}")
        if not suppress_alert:
            send_alert("Claude OAuth REFRESH FAILED", f"{error_msg}\n\nYou may need to run: claude /login")
        return False, error_msg, is_rate_limit
    except Exception as e:
        log(f"ERROR: Claude refresh failed: {e}")
        if not suppress_alert:
            send_alert("Claude OAuth REFRESH FAILED", f"{e}\n\nYou may need to run: claude /login")
        return False, str(e), False

    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)

    if not new_access:
        error_msg = f"No access_token in response: {data}"
        log(f"ERROR: {error_msg}")
        if not suppress_alert:
            send_alert("Claude OAuth REFRESH FAILED", f"Unexpected response: {data}")
        return False, error_msg, False

    new_expires_at = int(time.time() * 1000) + expires_in * 1000

    # Update .credentials.json
    with open(CLAUDE_CREDS) as f:
        creds = json.load(f)
    creds["claudeAiOauth"]["accessToken"] = new_access
    if new_refresh:
        creds["claudeAiOauth"]["refreshToken"] = new_refresh
    creds["claudeAiOauth"]["expiresAt"] = new_expires_at
    with open(CLAUDE_CREDS, "w") as f:
        json.dump(creds, f)

    # Update credentials.py CLAUDE_CODE_OAUTH_TOKEN
    update_credentials_py("CLAUDE_CODE_OAUTH_TOKEN", new_access)

    remaining_new = (new_expires_at / 1000 - time.time()) / 60
    log(f"Claude token refreshed — new expiry in {remaining_new:.0f} min")
    return True, None, False


def refresh_claude_token():
    """Refresh Claude token with retry and backoff."""
    if is_backed_off("claude"):
        return True  # Don't count as failure — we're intentionally waiting

    success = retry_with_backoff(_refresh_claude_once, "claude")
    if not success:
        send_alert("Claude OAuth REFRESH FAILED",
                    f"All {MAX_RETRIES} retry attempts failed.\n\n"
                    f"The script will back off automatically before trying again.\n\n"
                    f"You may need to run: claude /login")
    return success


# ── Google OAuth Refresh ─────────────────────────────────────────────

def get_google_token_status():
    """Return (access_token, refresh_token, expiry_str, remaining_minutes)."""
    if not os.path.exists(GOOGLE_TOKEN):
        return None, None, None, None
    with open(GOOGLE_TOKEN) as f:
        data = json.load(f)
    expiry = data.get("expiry", "")
    remaining = None
    if expiry:
        try:
            # Parse ISO format
            exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            remaining = (exp_dt.timestamp() - time.time()) / 60
        except Exception:
            pass
    return data.get("token"), data.get("refresh_token"), expiry, remaining


def _refresh_google_once(suppress_alert=False):
    """
    Single attempt to refresh Google token.
    Returns (success, error_msg, is_rate_limit).
    """
    import urllib.request
    import urllib.error

    with open(GOOGLE_TOKEN) as f:
        token_data = json.load(f)

    refresh_token = token_data.get("refresh_token")
    client_id = token_data.get("client_id")
    client_secret = token_data.get("client_secret")
    token_uri = token_data.get("token_uri", "https://oauth2.googleapis.com/token")

    if not all([refresh_token, client_id, client_secret]):
        log("ERROR: Missing Google OAuth credentials in token file")
        return False, "Missing credentials", False

    _, _, _, remaining = get_google_token_status()
    if remaining is None:
        log("Google token has null expiry — refreshing...")
    else:
        log(f"Google token expired/expiring ({remaining:.0f} min remaining) — refreshing...")

    import urllib.parse
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(token_uri, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        is_rate_limit = e.code == 429
        error_msg = f"HTTP {e.code}: {body}"
        log(f"ERROR: Google refresh failed {error_msg}")
        if not suppress_alert:
            send_alert("Google OAuth REFRESH FAILED", f"{error_msg}")
        return False, error_msg, is_rate_limit
    except Exception as e:
        log(f"ERROR: Google refresh failed: {e}")
        if not suppress_alert:
            send_alert("Google OAuth REFRESH FAILED", str(e))
        return False, str(e), False

    new_access = data.get("access_token")
    expires_in = data.get("expires_in", 3600)

    if not new_access:
        error_msg = f"No access_token in Google response: {data}"
        log(f"ERROR: {error_msg}")
        return False, error_msg, False

    # Update token file
    token_data["token"] = new_access
    if "refresh_token" in data:
        token_data["refresh_token"] = data["refresh_token"]
    from datetime import timedelta
    new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    token_data["expiry"] = new_expiry.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    with open(GOOGLE_TOKEN, "w") as f:
        json.dump(token_data, f, indent=2)

    log(f"Google token refreshed — new expiry in {expires_in // 60} min")
    return True, None, False


def refresh_google_token():
    """Refresh Google token with retry and backoff."""
    if is_backed_off("google"):
        return True  # Don't count as failure — we're intentionally waiting

    success = retry_with_backoff(_refresh_google_once, "google")
    if not success:
        send_alert("Google OAuth REFRESH FAILED",
                    f"All {MAX_RETRIES} retry attempts failed.\n\n"
                    f"The script will back off automatically before trying again.")
    return success


# ── credentials.py updater ───────────────────────────────────────────

def update_credentials_py(var_name, new_value):
    """Update a token variable in credentials.py."""
    if not os.path.exists(CREDENTIALS_PY):
        log(f"WARNING: {CREDENTIALS_PY} not found, skipping update")
        return

    with open(CREDENTIALS_PY) as f:
        content = f.read()

    # Match: VAR_NAME = "old_value"
    pattern = rf'^({var_name}\s*=\s*")[^"]*(")'
    new_content, count = re.subn(pattern, rf'\g<1>{new_value}\2', content, flags=re.MULTILINE)

    if count == 0:
        log(f"WARNING: Could not find {var_name} in credentials.py")
        return

    with open(CREDENTIALS_PY, "w") as f:
        f.write(new_content)
    log(f"Updated {var_name} in credentials.py")


# ── Main ─────────────────────────────────────────────────────────────

def check_status():
    """Report token status without refreshing."""
    log("=== Token Status ===")

    _, _, _, claude_remaining = get_claude_token_status()
    if claude_remaining is not None:
        if claude_remaining > REFRESH_THRESHOLD_MINUTES:
            status = "OK"
        elif claude_remaining > 0:
            status = "EXPIRING SOON"
        else:
            status = "EXPIRED"
        log(f"Claude OAuth: {status} ({claude_remaining:.0f} min remaining)")
    else:
        log("Claude OAuth: NOT FOUND")

    _, _, _, google_remaining = get_google_token_status()
    if google_remaining is not None:
        if google_remaining > REFRESH_THRESHOLD_MINUTES:
            status = "OK"
        elif google_remaining > 0:
            status = "EXPIRING SOON"
        else:
            status = "EXPIRED"
        log(f"Google OAuth: {status} ({google_remaining:.0f} min remaining)")
    else:
        log("Google OAuth: NOT FOUND")

    # Show backoff state
    state = load_rate_limit_state()
    if state:
        log("=== Rate Limit Backoff State ===")
        for service, info in state.items():
            backoff_until = info.get("backoff_until", 0)
            consec = info.get("consecutive_429s", "?")
            if time.time() < backoff_until:
                remaining = (backoff_until - time.time()) / 60
                log(f"{service}: Backing off for {remaining:.0f} more min (consecutive 429s: {consec})")
            else:
                log(f"{service}: Backoff expired (was {consec} consecutive 429s)")


def refresh_all(force=False):
    """Refresh all tokens that are expiring soon."""
    log(f"=== Token Refresh Run {'(FORCED)' if force else ''} ===")
    results = {}

    # Claude — DISABLED: Claude CLI manages its own OAuth token refresh.
    # External refresh caused 429 rate-limit storm (48 attempts/day vs Anthropic limits).
    _, _, _, claude_remaining = get_claude_token_status()
    if claude_remaining is not None:
        log(f"Claude token: {claude_remaining:.0f} min remaining (CLI-managed, not refreshing)")
    else:
        log("Claude credentials not found (created on next CLI login)")
    results["claude"] = True

    # Google
    _, google_refresh_token, _, google_remaining = get_google_token_status()
    if not os.path.exists(GOOGLE_TOKEN):
        log("Google token not found — skipping")
    elif google_remaining is None and google_refresh_token:
        # Null-expiry + refresh_token present = post-rescope state (google-auth
        # writes expiry=null until first refresh). Refresh now so mtime advances
        # and downstream consumers see a concrete expiry.
        log("Google token has null expiry but refresh_token present — refreshing")
        results["google"] = refresh_google_token()
    elif google_remaining is not None:
        if force or google_remaining < REFRESH_THRESHOLD_MINUTES:
            results["google"] = refresh_google_token()
        else:
            log(f"Google token OK ({google_remaining:.0f} min remaining) — skipping")
            results["google"] = True
    else:
        log("Google token file present but missing refresh_token — skipping")

    # Summary
    failures = [k for k, v in results.items() if not v]
    if failures:
        log(f"FAILURES: {', '.join(failures)}")
    else:
        log("All tokens healthy")

    return len(failures) == 0


def main():
    parser = argparse.ArgumentParser(description="Refresh OAuth tokens")
    parser.add_argument("--check", action="store_true", help="Check status only")
    parser.add_argument("--force", action="store_true", help="Force refresh even if not expiring")
    args = parser.parse_args()

    if args.check:
        check_status()
    else:
        success = refresh_all(force=args.force)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
