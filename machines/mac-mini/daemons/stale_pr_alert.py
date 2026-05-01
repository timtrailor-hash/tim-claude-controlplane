#!/usr/bin/env python3.11
"""Stale PR alert + auto-merge. Runs via LaunchAgent.

The rule behind this: PRs are a transient state. They auto-merge on
green CI or they surface as specific failures. A PR that sits for
more than a day has slipped the net and needs action.

Detection path: every run lists open PRs in REPOS, marks any older than
STALE_THRESHOLD_HOURS as a candidate, and tries `_try_auto_merge` on
each. PRs that pass the safety gate are squash-merged. Anything that
fails the gate (or any partial discovery failure) is emailed to Tim.

Safety gate (`_can_auto_merge`): state=OPEN, not draft, mergeable=
MERGEABLE, mergeStateStatus=CLEAN, every check COMPLETED+SUCCESS (or
no checks at all), author=timtrailor-hash, every commit body contains
"Co-Authored-By: Claude".

CRITICAL DEPENDENCY: the trailer check is the single guard against a
non-Claude PR self-qualifying for auto-merge. Its strength equals the
strength of `commit_quality_hook` (in tim-claude-controlplane/shared/
hooks/), which enforces the trailer on every Claude-driven commit. If
that hook is ever disabled, removed, or bypassed (--no-verify, web-UI
edit, direct push), any commit from timtrailor-hash can pass the gate.
Treat the hook as a load-bearing single point of failure for this
daemon and audit it as part of any controlplane deploy.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/timtrailor/code")
from credentials import SMTP_USER, SMTP_PASS  # noqa

# Repos under stale-PR detection + auto-merge.
# claude-code-starter has no CI workflow: PRs there hit auto-merge with an
# empty statusCheckRollup (Pattern 29) and merge once they're 24h old + CLEAN.
# claude-mobile, tim-claude-controlplane, mac-mini-infra all have CI. Their
# PRs auto-merge only after every check completes with SUCCESS.
REPOS = [
    "timtrailor-hash/claude-mobile",
    "timtrailor-hash/tim-claude-controlplane",
    "timtrailor-hash/mac-mini-infra",
    "timtrailor-hash/claude-code-starter",
]

STALE_THRESHOLD_HOURS = 24
LOG_PATH = Path.home() / "code" / "stale_pr_log.jsonl"
STATE_PATH = Path.home() / "code" / ".stale_pr_state.json"


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception as e:
            _log({"event": "state_load_failed", "path": str(STATE_PATH), "error": str(e)})
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


def _log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _hours_since(iso: str) -> float:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception as e:
        _log({"event": "iso_parse_failed", "iso": iso, "error": str(e)})
        return 999.0  # sentinel: surface as stale, never as fresh
    now = datetime.now(timezone.utc)
    return (now - t).total_seconds() / 3600


class _DiscoveryFailed(Exception):
    """Raised when gh pr list fails. Surfaces to main() so a network blip
    does not cause every repo to silently appear PR-empty (Pattern 3)."""


def _list_prs(repo: str) -> list[dict]:
    try:
        out = subprocess.run(
            ["/opt/homebrew/bin/gh", "pr", "list", "--repo", repo,
             "--state", "open",
             "--json", "number,title,createdAt,updatedAt,mergeStateStatus,url,isDraft,author"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        _log({"event": "list_prs_subprocess_failed", "repo": repo, "error": str(e)})
        raise _DiscoveryFailed(f"{repo}: subprocess: {e}") from e
    if out.returncode != 0:
        _log({"event": "list_prs_nonzero", "repo": repo,
              "rc": out.returncode, "stderr": (out.stderr or "")[:500]})
        raise _DiscoveryFailed(f"{repo}: gh exited {out.returncode}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        _log({"event": "list_prs_parse_failed", "repo": repo, "error": str(e)})
        raise _DiscoveryFailed(f"{repo}: json: {e}") from e


def _find_stale() -> tuple[list[dict], list[str]]:
    """Return (stale_candidates, per_repo_discovery_failures).

    Discovery failures are surfaced to main() so a total gh outage does not
    silently look like 'all repos clean' and suppress the alert email.
    """
    stale: list[dict] = []
    failed: list[str] = []
    for repo in REPOS:
        try:
            prs = _list_prs(repo)
        except _DiscoveryFailed as e:
            failed.append(str(e))
            continue
        for pr in prs:
            age_h = _hours_since(pr.get("createdAt", ""))
            if age_h >= STALE_THRESHOLD_HOURS and not pr.get("isDraft"):
                stale.append({
                    "repo": repo,
                    "number": pr.get("number"),
                    "title": pr.get("title"),
                    "url": pr.get("url"),
                    "age_hours": round(age_h, 1),
                    "mergeState": pr.get("mergeStateStatus"),
                    "author": (pr.get("author") or {}).get("login", "?"),
                })
    return stale, failed


def _pr_detail(repo: str, number: int) -> dict | None:
    """Fetch the fields needed to evaluate auto-merge eligibility.

    Returns None on any gh failure so the caller falls back to email rather
    than silently auto-merging an under-determined PR.
    """
    try:
        out = subprocess.run(
            ["/opt/homebrew/bin/gh", "pr", "view", str(number), "--repo", repo,
             "--json", "mergeable,mergeStateStatus,statusCheckRollup,commits,author,isDraft,state"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        _log({"event": "pr_detail_subprocess_failed", "repo": repo,
              "number": number, "error": str(e)})
        return None
    if out.returncode != 0:
        _log({"event": "pr_detail_failed", "repo": repo, "number": number,
              "stderr": (out.stderr or "")[:500]})
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        _log({"event": "pr_detail_parse_failed", "repo": repo, "number": number,
              "error": str(e)})
        return None


def _is_claude_authored(detail: dict) -> bool:
    """True iff every commit on the PR has a 'Co-Authored-By: Claude' trailer.

    The trailer is the strong, deliberate signal — commit_quality_hook
    enforces it on every Claude-driven commit. Bare author-name matches
    are rejected here to avoid coincidental 'Claude' name collisions
    passing the gate.
    """
    commits = detail.get("commits") or []
    if not commits:
        return False
    for c in commits:
        body = c.get("messageBody", "") or ""
        if "co-authored-by: claude" not in body.lower():
            return False
    return True


def _can_auto_merge(detail: dict) -> tuple[bool, str]:
    """Gate every condition that must hold before we run gh pr merge."""
    if detail.get("state") != "OPEN":
        return False, f"state={detail.get('state')}"
    if detail.get("isDraft"):
        return False, "isDraft"
    if detail.get("mergeable") != "MERGEABLE":
        return False, f"mergeable={detail.get('mergeable')}"
    if detail.get("mergeStateStatus") != "CLEAN":
        return False, f"mergeStateStatus={detail.get('mergeStateStatus')}"
    rollup = detail.get("statusCheckRollup") or []
    # Empty rollup = repo has no required checks (e.g. claude-code-starter,
    # Pattern 29). CLEAN + MERGEABLE is sufficient; the loop below is a no-op.
    for ch in rollup:
        status = ch.get("status") or ""
        conclusion = ch.get("conclusion") or ""
        if status != "COMPLETED":
            return False, f"check {ch.get('name')} status={status or '(unknown)'}"
        if conclusion != "SUCCESS":
            return False, f"check {ch.get('name')} conclusion={conclusion or '(none)'}"
    if (detail.get("author") or {}).get("login") != "timtrailor-hash":
        return False, f"author={(detail.get('author') or {}).get('login')}"
    if not _is_claude_authored(detail):
        return False, "no Claude co-author trailer on every commit"
    return True, "ok"


def _try_auto_merge(s: dict) -> tuple[bool, str]:
    """Attempt to squash-merge a stale PR. Returns (merged, reason)."""
    detail = _pr_detail(s["repo"], s["number"])
    if detail is None:
        return False, "pr_detail unavailable"
    ok, reason = _can_auto_merge(detail)
    if not ok:
        return False, reason
    try:
        out = subprocess.run(
            ["/opt/homebrew/bin/gh", "pr", "merge", str(s["number"]),
             "--repo", s["repo"], "--squash", "--delete-branch"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        _log({"event": "auto_merge_subprocess_failed", "repo": s["repo"],
              "number": s["number"], "error": str(e)})
        return False, f"subprocess: {e}"
    if out.returncode != 0:
        _log({"event": "auto_merge_failed", "repo": s["repo"], "number": s["number"],
              "stderr": (out.stderr or "")[:500]})
        return False, f"gh pr merge exited {out.returncode}"
    _log({"event": "auto_merged", "repo": s["repo"], "number": s["number"],
          "title": s["title"], "age_hours": s["age_hours"]})
    return True, "merged"


def _send_email(stale: list[dict], merged: list[dict],
                discovery_failures: list[str] | None = None,
                carry_forward_ids: list[str] | None = None) -> None:
    discovery_failures = discovery_failures or []
    carry_forward_ids = carry_forward_ids or []
    subject = f"Stale PR alert: {len(stale)} PR(s) older than {STALE_THRESHOLD_HOURS}h"
    if merged and not stale:
        subject = f"Stale PR alerter: auto-merged {len(merged)} PR(s)"
    lines = [
        "Hi Tim,",
        "",
    ]
    if merged:
        lines.append(f"Auto-merged {len(merged)} PR(s) that met the safety gate "
                     "(CLEAN, MERGEABLE, all checks SUCCESS or none, author=timtrailor-hash, "
                     "Claude co-author trailer on every commit):")
        for m in sorted(merged, key=lambda x: -x["age_hours"]):
            lines.append(f"  [{m['age_hours']:.0f}h] {m['repo']} #{m['number']} {m['title']}")
            lines.append(f"         {m['url']}")
        lines.append("")
    if carry_forward_ids:
        lines.append("The previous run also auto-merged these PR(s) but the "
                     "notification email failed at the time. Listing them now:")
        for cid in carry_forward_ids:
            lines.append(f"  - {cid}")
        lines.append("")
    if stale:
        lines.append(f"The following pull requests have been open for more than "
                     f"{STALE_THRESHOLD_HOURS} hours and could NOT be auto-merged.")
        lines.append("PRs should auto-merge on green CI or surface as specific failures; "
                     "anything sitting")
        lines.append("longer than a day is drifting outside the intended workflow.")
        lines.append("")
        for s in sorted(stale, key=lambda x: -x["age_hours"]):
            lines.append(f"  [{s['age_hours']:.0f}h] {s['repo']} #{s['number']} {s['title']}")
            lines.append(f"         state={s['mergeState']}  author={s['author']}  "
                         f"reason={s.get('skip_reason', '?')}")
            lines.append(f"         {s['url']}")
            lines.append("")
    if discovery_failures:
        lines.append("Discovery FAILED for these repos (gh pr list errored). "
                     "These were skipped this run:")
        for f in discovery_failures:
            lines.append(f"  - {f}")
        lines.append("")
    lines.append("Tim,")
    lines.append("Claude (stale-pr alerter)")
    body = "\n".join(lines)

    _smtp_send(subject, body)


def _send_discovery_error(failures: list[str]) -> None:
    subject = "Stale PR alerter: TOTAL DISCOVERY FAILURE"
    lines = [
        "Hi Tim,",
        "",
        "The stale-PR alerter could not list PRs in ANY tracked repo this run.",
        "No auto-merge was attempted. No staleness signal is reliable until this is fixed.",
        "",
        "Likely causes: gh auth expired, network drop, rate limit, gh binary moved.",
        "",
        "Per-repo errors:",
    ]
    for f in failures:
        lines.append(f"  - {f}")
    lines += ["", "Tim,", "Claude (stale-pr alerter)"]
    _smtp_send(subject, "\n".join(lines))


def _smtp_send(subject: str, body: str) -> None:
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = "timtrailor@gmail.com"
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def main():
    candidates, discovery_failures = _find_stale()

    # If EVERY repo failed discovery, we have no signal. Email an error
    # instead of silently emitting "0 stale PRs" and looking healthy.
    if discovery_failures and len(discovery_failures) == len(REPOS):
        _log({"event": "discovery_total_failure", "failures": discovery_failures})
        _send_discovery_error(discovery_failures)
        print(json.dumps({"event": "discovery_total_failure",
                          "failures": discovery_failures}, indent=2))
        return

    merged: list[dict] = []
    remaining: list[dict] = []
    for s in candidates:
        ok, reason = _try_auto_merge(s)
        if ok:
            merged.append(s)
        else:
            s["skip_reason"] = reason
            remaining.append(s)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = _load_state()

    merged_ids_now = [f"{m['repo']}#{m['number']}" for m in merged]
    pending_merged_ids = state.get("pending_merged_ids", [])
    # If a previous run merged PRs but the SMTP send failed, the merged
    # context was lost. Carry those forward so Tim still gets one
    # notification when the next run can email.
    carry_forward = list(pending_merged_ids)

    # Dedup: email if the set of remaining stale PR ids has changed, OR no
    # email in the last 24 hours, OR we just auto-merged something, OR a
    # prior auto-merge notification was never delivered, OR partial
    # discovery failed.
    ids_today = sorted(f"{s['repo']}#{s['number']}" for s in remaining)
    last_ids = state.get("last_alert_ids", [])
    last_sent_iso = state.get("last_sent", "")
    last_sent_hours_ago = _hours_since(last_sent_iso) if last_sent_iso else 999

    should_email = False
    reason = ""
    if merged:
        should_email = True
        reason = f"auto-merged {len(merged)}"
    elif carry_forward:
        should_email = True
        reason = f"undelivered auto-merge notice ({len(carry_forward)})"
    elif remaining and ids_today != last_ids:
        should_email = True
        reason = "stale-pr set changed"
    elif remaining and last_sent_hours_ago >= 24:
        should_email = True
        reason = "24h since last alert"
    elif discovery_failures:
        should_email = True
        reason = f"partial discovery failure ({len(discovery_failures)}/{len(REPOS)})"

    entry = {
        "timestamp": now_iso,
        "merged_count": len(merged),
        "merged_ids": merged_ids_now,
        "carry_forward_merged_ids": carry_forward,
        "stale_count": len(remaining),
        "stale_ids": ids_today,
        "discovery_failures": discovery_failures,
        "emailed": should_email,
        "reason": reason if should_email else "dedup",
    }
    _log(entry)

    if should_email:
        # Mark this run's merges as pending-notification BEFORE attempting
        # SMTP. If SMTP succeeds we clear the pending list afterward; if it
        # raises, the next run sees the pending ids and re-tries the email.
        state["last_alert_ids"] = ids_today
        state["last_sent"] = now_iso
        state["pending_merged_ids"] = sorted(set(carry_forward + merged_ids_now))
        _save_state(state)
        try:
            _send_email(remaining, merged, discovery_failures,
                        carry_forward_ids=carry_forward)
        except Exception as e:
            _log({"event": "smtp_failed", "error": str(e),
                  "pending_merged_ids": state["pending_merged_ids"]})
            raise
        # SMTP succeeded — clear the pending-notification queue.
        state["pending_merged_ids"] = []
        _save_state(state)

    print(json.dumps(entry, indent=2))


if __name__ == "__main__":
    main()
