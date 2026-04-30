#!/usr/bin/env python3.11
"""response_quality_check.py — Stop hook that gates Claude's responses against
the feedback rules in memory.

Reads the Claude Code Stop-hook JSON payload on stdin (`session_id`,
`transcript_path`, `stop_hook_active`). Loads the most recent assistant
message from the JSONL transcript, runs three mechanical checks:

  1. No em-dash (— U+2014) in user-facing text.
  2. No human time-duration estimates ("30 minutes", "a few hours",
     "in a day", "this week", etc.) applied to Claude's own work.
  3. No deferral phrases ("logged for later", "TODO", "follow-up",
     "when there's bandwidth") — see feedback_no_deferring.md.

On violation: print a "[response_gate]" message to stderr and exit 2,
which Claude Code interprets as "block the stop and feed this back to
Claude as continuation." Claude self-corrects on the retry.

Anti-loop: a counter at /tmp/.claude_response_gate_<session>.count tracks
retries per session. After 3 retries on the same turn, the hook logs the
violation and exits 0 (allow). Tim sees the message AND the rules
violated — that's a signal the rule needs tuning, not a stuck loop.

Anti-self-trigger: if `stop_hook_active` is true in the payload, the hook
exits 0 immediately. This prevents the loop where Claude's continuation
is itself examined and re-blocked.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LOG_PATH = Path("/tmp/claude_response_gate.log")
VIOLATIONS_LOG = Path.home() / ".claude" / "response_gate_violations.log"
MAX_RETRIES = 3


# ── Rules ──────────────────────────────────────────────────────────────────

EM_DASH = "\u2014"  # — character

# A "duration phrase" — number + unit, with up to two adjective words
# between (so "5 more minutes" / "10 additional days" match). Common
# abbreviations (min, hr, sec) are accepted alongside full words.
# Matched ONLY when paired with a future-tense indicator (within 80 chars).
# Past-tense / factual statements like "the printer ran for 30 minutes
# yesterday" are not flagged because they lack future-tense indicators.
_DURATION = (
    r"\b(?:\d+|a\s+few|several|a\s+couple\s+of|a)"
    r"\s+(?:\w+\s+){0,2}"
    r"(?:second|minute|hour|day|week|month|min|hr|sec)s?\b"
)
# Estimate / forecast / first-person-future language. Anything from this set
# next to a duration phrase counts as a time estimate. Past-tense factual
# statements ("ran for 30 minutes yesterday") don't contain these words and
# pass.
# Estimate / forecast / first-person-future language. Anything from this set
# next to a duration phrase counts as a time estimate. Past-tense factual
# statements ("ran for 30 minutes yesterday") don't contain these words and
# pass.
#
# IMPORTANT: forms that "look ahead" to a digit (about, roughly, ~, need)
# use a `(?=\d)` lookahead instead of `\s+\d` so the digit is left for the
# DURATION arm to consume. Otherwise we'd eat the number twice.
_FUTURE = (
    r"(?:"
    r"i'?ll|i\s+will|i'?m\s+going\s+to|we'?ll|we\s+will|"
    r"will\s+(?:be|do|build|ship|deploy|finish|take|need|land)|"
    r"(?:should|could|may|might)\s+(?:take|land|ship|finish|be\s+done)|"
    r"takes?\s+about|in\s+about|in\s+roughly|"
    r"estimat(?:e|ed|ing)\s*(?:at|to|of|d)?|"
    r"eta\b|"
    r"about\s*(?=\d)|approximately\s*(?=\d)|roughly\s*(?=\d)|~\s*(?=\d)|"
    r"need(?:s|ed)?\s+(?=\d)|need(?:s|ed)?\s+(?:about|roughly|approximately|another)|"
    r"done\s+in|finish(?:es|ed)?\s+in|wrap(?:s|ped)?\s+up\s+in"
    r")"
)

TIME_ESTIMATE_RE = re.compile(
    rf"""
    # Pattern A: future-tense / estimate indicator, then duration within 80 chars
    {_FUTURE}[^.!?\n]{{0,80}}{_DURATION}
    |
    # Pattern B: duration, then future-tense / estimate indicator within 80 chars
    {_DURATION}[^.!?\n]{{0,80}}{_FUTURE}
    |
    # Pattern C: scheduling words paired with future-tense intent
    \b(?:overnight|tonight|tomorrow|next\s+week|this\s+week|this\s+evening)\b
    (?=[^.!?\n]{{0,80}}\b(?:i'?ll|i\s+will|will\s+(?:be|do|build|ship|deploy|finish))\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)

DEFERRAL_RE = re.compile(
    r"""
    \b(?:
        logged\s+for\s+later
        | track(?:ed|ing)?\s+for\s+later
        | follow[-\s]?up\s+(?:slice|task|session|pr|item)
        | for\s+a\s+future\s+session
        | when\s+there'?s\s+bandwidth
        | TODO\s+for\s+later
        | filed\s+for\s+later
        | queued\s+for\s+(?:later|next)
        | will\s+(?:patch|fix|address)\s+later
        | proper\s+fix\s+is\s+logged
        | parked\s+(?:for|until)
        | revisit\s+later
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def load_last_assistant_text(transcript_path: str) -> str:
    """Return concatenated text of the most recent assistant message."""
    path = Path(transcript_path)
    if not path.is_file():
        return ""
    last_assistant_text: list[str] = []
    try:
        with path.open() as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
    except Exception:
        return ""
    # Walk from the end for the most recent assistant turn. A turn may have
    # multiple text blocks; collect contiguous trailing assistant entries.
    for entry in reversed(entries):
        if entry.get("type") != "assistant":
            if last_assistant_text:
                break
            continue
        msg = entry.get("message", {}) or {}
        content = msg.get("content", [])
        if isinstance(content, str):
            last_assistant_text.insert(0, content)
            continue
        for block in content or []:
            if isinstance(block, dict) and block.get("type") == "text":
                last_assistant_text.insert(0, block.get("text", ""))
    return "\n".join(last_assistant_text)


def find_violations(text: str) -> list[str]:
    issues: list[str] = []
    if EM_DASH in text:
        issues.append(
            "em-dash (U+2014) in response. Replace with comma, full stop, "
            "or rephrase. See feedback_response_structure.md."
        )
    m = TIME_ESTIMATE_RE.search(text)
    if m:
        issues.append(
            f"human time-estimate phrase ({m.group(0)!r}). Clock durations "
            f"don't apply to Claude's own work. See feedback_no_time_estimates.md."
        )
    m = DEFERRAL_RE.search(text)
    if m:
        issues.append(
            f"deferral phrase ({m.group(0)!r}). Fix now or explain why "
            f"fix-now is unsafe. See feedback_no_deferring.md."
        )
    return issues


def retry_count_path(session_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id or "unknown")
    return Path(f"/tmp/.claude_response_gate_{safe}.count")


def bump_retry(session_id: str) -> int:
    p = retry_count_path(session_id)
    n = 0
    if p.exists():
        try:
            n = int(p.read_text().strip())
        except Exception:
            n = 0
    n += 1
    try:
        p.write_text(str(n))
    except Exception:
        pass
    return n


def reset_retry(session_id: str) -> None:
    p = retry_count_path(session_id)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


def log(msg: str) -> None:
    try:
        with LOG_PATH.open("a") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def log_violation(session_id: str, retry: int, issues: list[str], excerpt: str) -> None:
    """Append a JSONL record to ~/.claude/response_gate_violations.log so
    every flagged response is auditable, not just the ones that exceed the
    retry cap. Tim can `tail -f` this to see what the gate is catching and
    tune the rules."""
    try:
        VIOLATIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "retry": retry,
            "issues": issues,
            # First 240 chars of the assistant text — enough to identify the
            # turn without bloating the log.
            "excerpt": excerpt[:240],
        }
        with VIOLATIONS_LOG.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input — never block on parse failure

    if payload.get("stop_hook_active"):
        # We're in a Stop continuation triggered by ourselves or another
        # hook. Don't re-examine, or we loop.
        return 0

    transcript_path = payload.get("transcript_path", "")
    session_id = payload.get("session_id", "")
    text = load_last_assistant_text(transcript_path)
    if not text:
        return 0

    issues = find_violations(text)
    if not issues:
        reset_retry(session_id)
        return 0

    n = bump_retry(session_id)
    log_violation(session_id, n, issues, text)
    if n > MAX_RETRIES:
        log(f"[response_gate] session={session_id} max-retries hit, allowing.")
        log(f"  issues: {issues}")
        reset_retry(session_id)
        return 0

    body = "\n  - ".join(issues)
    msg = (
        f"[response_gate] response violates the feedback rules:\n"
        f"  - {body}\n"
        f"\n"
        f"Re-do the response with: shorter, plainer English, no em-dashes, "
        f"no human time-duration estimates, no deferral phrases. Address Tim "
        f"directly. Retry {n}/{MAX_RETRIES}."
    )
    print(msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
