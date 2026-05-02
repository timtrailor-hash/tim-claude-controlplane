"""Permission-tier classifier — single source of truth for the four-tier
permission model.

Tim is the CEO, not the engineer. Asking him to approve every "potentially
dangerous" Bash call is theatre — most of those are technical judgements he
can't usefully make. The four-tier model fixes this:

  T1 — auto-allow.  Reads, idempotent local ops, safe-verb chains.
  T2 — auto-deny.   Catastrophic shapes (rm -rf /, force-push to main,
                    hook self-modification). No human, no reviewer.
  T3 — reviewer.    Ambiguous cases. A subscription-Claude reviewer judges,
                    APPROVE/BLOCK/ASK. Tim only sees ASK.
  T4 — Tim's tap.   Irreversible AND visible-to-others actions: launchctl
                    state changes, plist writes, force-push (any branch),
                    repo visibility flips, paid spend, message sends to
                    non-Tim recipients.

Lessons.md Pattern 36 documents the reframe.

This module is pure logic — no subprocess, no network, no I/O. It consumes
the tool name + tool_input dict (the same shape Claude Code's PreToolUse
hook receives) and returns a TierVerdict. The hook (protected_path_hook.sh)
calls in via `python3 tier_classifier.py` and dispatches.
"""

from __future__ import annotations

import os as _hook_os
if _hook_os.environ.get("CLAUDE_HOOKS_BYPASS") == "server_internal":
    import sys as _hook_sys
    _hook_sys.stdin.read()  # drain stdin so caller doesnt block
    _hook_sys.exit(0)

import json
import re
import sys
from dataclasses import dataclass, asdict


@dataclass
class TierVerdict:
    tier: str  # "T1" | "T2" | "T3" | "T4"
    reason: str  # short human-readable explanation


# ── Tier 2 (auto-deny) ────────────────────────────────────────────────────
# Catastrophic shapes that are ALWAYS wrong. No reviewer, no Tim, no override.
# Override paths exist outside the hook (e.g. terminal session with the hook
# disabled). Inside an automated session, these never run.
T2_PATTERNS = [
    (re.compile(r"\brm\s+(-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\s+/(\s|$|\\)"), "rm -rf at filesystem root"),
    (re.compile(r"\brm\s+(-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\s+/\*"), "rm -rf /* — wildcard root deletion"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|disk)\d"), "redirect to raw block device"),
    (re.compile(r"\bdd\s+.*\bof=/dev/(sd|nvme|hd|disk)\d"), "dd to raw block device"),
    # Force-push to the canonical main branches: catastrophic for shared history.
    (
        re.compile(
            r"\bgit\s+push\s+(?:[^\n]*\s)?(?:--force\b|-f\b|--force-with-lease\b)"
            r"[^\n]*\s+(?:origin\s+)?(?:main|master|HEAD)(?:\s|$)"
        ),
        "force-push to main/master",
    ),
    # Hook self-modification: a tool that edits the safety hooks themselves
    # would defang the system. Sed/perl-in-place rewrites get caught here.
    (
        re.compile(r"(?:>|>>)\s*(?:[^\s]+/)?\.claude/hooks/[^\s]+\.(sh|py)\b"),
        "redirect into ~/.claude/hooks/ (would defang safety net)",
    ),
    (
        re.compile(r"\b(sed\s+-i|perl\s+-pi)[^\n]*\.claude/hooks/"),
        "in-place edit of ~/.claude/hooks/ (would defang safety net)",
    ),
]


# ── Tier 4 (Tim's tap) ─────────────────────────────────────────────────────
# Irreversible AND visible-to-others actions. Tim is the only correct gate
# because these are product/judgement decisions, not technical ones. The hook
# emits permissionDecision: "ask" with the reason as the prompt label.
T4_PATTERNS = [
    # launchctl state changes (Pattern 13, narrowed in operational.md).
    (
        re.compile(r"\blaunchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable)\b"),
        "launchctl state-change command",
    ),
    # System reboot / shutdown.
    (re.compile(r"\bsudo\s+(?:-n\s+)?(?:reboot|shutdown|halt|init)\b"), "system reboot/shutdown"),
    # Force-push to ANY non-main branch (T2 catches main; T4 catches the rest).
    (
        re.compile(r"\bgit\s+push\s+(?:[^\n]*\s)?(?:--force\b|-f\b|--force-with-lease\b)"),
        "force-push (rewrites remote history)",
    ),
    # plutil -extract without -o overwrites the source (Pattern 12).
    (re.compile(r"\bplutil\s+-extract\b(?![^\n]*-o\s)"), "plutil -extract without -o (overwrites source)"),
    # chflags immutability changes (Pattern 6 in lessons).
    (re.compile(r"\bchflags\s+(?:no)?(?:uchg|schg)\b"), "filesystem immutability flag"),
    # Repo visibility flips and creation as public.
    (
        re.compile(r"\bgh\s+repo\s+(?:create|edit)\b[^\n]*(?:--public|--visibility\s+public)"),
        "GitHub repo visibility flip to public / public-create",
    ),
    # Package installs that pull external code into the trust boundary.
    (re.compile(r"\bbrew\s+install\b"), "brew install (external dependency)"),
    (re.compile(r"\bpip3?\s+install\b"), "pip install (external dependency)"),
    (re.compile(r"\bnpm\s+install\b"), "npm install (external dependency)"),
    # Paid services / API tier upgrades — anything that costs money.
    (re.compile(r"\bgh\s+billing\b"), "GitHub billing change"),
    (re.compile(r"\bgh\s+api[^\n]*billing"), "GitHub billing API call"),
    # Message sends to non-Tim recipients. Heuristic: looking for SMTP/Slack
    # webhook calls inline; the conv_server's email helpers are the canonical
    # path and they call shared SMTP utils — those callers are inside conv,
    # so a Bash send is a script doing a one-off send.
    (re.compile(r"\bsmtplib\.SMTP[^\n]*send_message\b"), "SMTP send (script)"),
    (re.compile(r"\bcurl[^\n]*hooks\.slack\.com"), "Slack webhook send"),
]


# ── Tier 3 sentinels ───────────────────────────────────────────────────────
# scan_command.py emits sentinels for protected-path operations. When present
# but no T4 pattern fires, the change is structural — load-bearing path
# write, system path write, etc. Reviewer should judge.
T3_SENTINELS = [
    "__LA_WRITE__",  # write to LaunchAgent/LaunchDaemon plist
    "__SYS_WRITE__",  # write to /etc or /Library
]


# ── Load-bearing paths (Edit/Write tier-3 trigger) ────────────────────────
# Edits to these paths are technical decisions — reviewer should check.
# Edits to other project paths are T1 (auto-allow; existing reviewer pipeline
# at /review time catches issues at commit boundary).
LOAD_BEARING_PATH_PATTERNS = [
    re.compile(r"\.claude/hooks/.*\.(sh|py)$"),
    re.compile(r"\.claude/settings.*\.json$"),
    re.compile(r"shared/hooks/.*\.(sh|py)$"),
    re.compile(r"shared/lib/.*\.py$"),
    re.compile(r"machines/[^/]+/daemons/.*\.py$"),
    re.compile(r"machines/[^/]+/launchagents/.*\.plist$"),
    re.compile(r"machines/[^/]+/services\.yaml$"),
    re.compile(r"machines/[^/]+/system_map\.yaml$"),
    re.compile(r"\.github/workflows/.*\.ya?ml$"),
]


def _is_load_bearing(path: str) -> bool:
    """True if the file is one whose edits should hit the reviewer."""
    return any(pat.search(path) for pat in LOAD_BEARING_PATH_PATTERNS)


def classify(tool_name: str, tool_input: dict, scan_sentinels: str = "") -> TierVerdict:
    """Classify a tool call into permission tier.

    Args:
        tool_name: e.g. "Bash", "Edit", "Write", "Read".
        tool_input: dict matching Claude Code's PreToolUse contract.
        scan_sentinels: optional output of scan_command.py (sentinels that
            indicate __LA_WRITE__ / __SYS_WRITE__ etc.).

    Returns:
        TierVerdict with .tier in {T1, T2, T3, T4} and a short reason.
    """
    # Read tools always pass — Read tool itself has no side effects.
    if tool_name in ("Read", "Glob", "Grep", "NotebookRead"):
        return TierVerdict("T1", f"{tool_name} is read-only")

    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        if not cmd.strip():
            return TierVerdict("T1", "empty command")

        # T1 carve-outs: known-safe read-only shapes that the bash safe-verb
        # bypass already permits but might not match every real shape (e.g.
        # `launchctl list`, `launchctl print` — the bash bypass list ends at
        # the first verb so it doesn't recognise these as a unit).
        if re.search(r"^\s*launchctl\s+(list|print)\b", cmd) and not re.search(
            r"\blaunchctl\s+(bootstrap|bootout|kickstart|load|unload|enable|disable|setenv|unsetenv)\b",
            cmd,
        ):
            return TierVerdict("T1", "launchctl read-only (list/print)")

        # T2: catastrophic — instant deny.
        for pat, reason in T2_PATTERNS:
            if pat.search(cmd):
                return TierVerdict("T2", reason)

        # T4: irreversible-and-visible — Tim's tap.
        for pat, reason in T4_PATTERNS:
            if pat.search(cmd):
                return TierVerdict("T4", reason)

        # T3 sentinel: scan_command found a protected-path write.
        if any(s in scan_sentinels for s in T3_SENTINELS):
            return TierVerdict("T3", "protected-path write — reviewer should judge")

        # No T2/T4/sentinel match. The hook's existing safe-verb bypass
        # already exited 0 before reaching us, so anything that lands here
        # is by definition NOT a safe-verb chain. Hand off to the reviewer.
        return TierVerdict("T3", "ambiguous Bash — reviewer judgement required")

    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if _is_load_bearing(path):
            return TierVerdict("T3", f"write to load-bearing path: {path}")
        return TierVerdict("T1", "write to project file in non-load-bearing path")

    # WebFetch / WebSearch / MCP tools etc. Default conservatively to T3 so
    # the reviewer judges; better to add ~3s latency than miss a side effect.
    return TierVerdict("T3", f"unclassified tool: {tool_name}")


def main() -> int:
    """CLI: read PreToolUse JSON from stdin, write JSON verdict to stdout.

    Input shape (Claude Code PreToolUse contract):
        {"tool_name": "Bash", "tool_input": {"command": "..."}}

    Optional: pass --scan to forward the command through scan_command.py
    first and pass its sentinel output to classify().

    Output shape:
        {"tier": "T2", "reason": "rm -rf at filesystem root"}
    """
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"tier": "T3", "reason": f"could not parse PreToolUse JSON: {e}"}))
        return 0

    tool_name = payload.get("tool_name", "") or ""
    tool_input = payload.get("tool_input") or {}
    scan_sentinels = payload.get("scan_sentinels", "") or ""

    verdict = classify(tool_name, tool_input, scan_sentinels)
    print(json.dumps(asdict(verdict)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
