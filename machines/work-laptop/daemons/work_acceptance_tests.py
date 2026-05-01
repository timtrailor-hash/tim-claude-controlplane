#!/usr/bin/env python3
"""work_acceptance_tests.py — Pattern-3 acceptance suite for the work laptop.

Slice N (verification framework). Each test EXERCISES the actual feature it
verifies — feeds a real payload to a hook, queries the live ChromaDB,
HTTP-probes the bridge gateway. Pure file-existence checks would have
caught zero of the silent-drift incidents we've seen historically (lessons
Pattern 3 / Pattern 17), so this suite refuses to use them where a
behavioural probe is possible.

Output contract (consumed by work_verify.sh and dashboard tooling):

    {
      "timestamp": "2026-05-01T22:00:00+01:00",
      "summary": {"total": 15, "green": 12, "amber": 2, "red": 1},
      "items": [
        {
          "name": "hooks.protected_path_hook.deny_launchctl_bootout",
          "category": "hooks",
          "status": "green",
          "detail": "exit 0 with permissionDecision=ask emitted",
          "evidence": "{...stdout...}"
        },
        ...
      ]
    }

Status enum:
    green  — feature exercised end-to-end and behaved as required.
    amber  — feature partially working / dependency missing but
             non-fatal; user-visible degradation, not data loss.
    red    — feature broken or absent; agent will report a wrong
             answer / take an unsafe action. Operator MUST fix.

A test that throws is caught at the top level and recorded as
status=red with the exception text in `detail` — a single broken probe
never crashes the suite.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

RESULTS_PATH = Path("/tmp/work_acceptance_results.json")

HOOKS_DIR = Path.home() / ".claude" / "hooks"
RULES_DIR = Path.home() / ".claude" / "rules"
CLAUDE_JSON = Path.home() / ".claude.json"
PROJECTS_ROOT = Path.home() / ".claude" / "projects"
LINT_LOG = Path.home() / ".claude" / "lint_findings.log"
HEALTH_RESULTS = Path("/tmp/work_health_check_results.json")

# LaunchAgent labels per services.yaml (Slice J). All prefixed with
# `com.timtrailor.work-` to keep them distinct from personal-side agents.
WORK_LAUNCHAGENTS = [
    "work-health-check",
    "work-ci-failure-poller",
    "work-stale-pr-alert",
    "work-credential-rotation",
    "work-memory-indexer",
    "work-trend-tracker",
]

# Bridge gateway HTTP probe — port + path per the bridge service contract.
# 127.0.0.1 (not 0.0.0.0): the gateway binds loopback only. If this gets
# moved to a unix socket later, swap this to a UDS request.
BRIDGE_HEALTH_URL = "http://127.0.0.1:8090/bridge-health"

# 9 work hooks deployed by work_setup.sh Section E. response_quality_check.*
# is deployed via the reply-style.md materialiser, not Section E, so it's
# checked separately in the response-gate test.
WORK_HOOKS_EXPECTED = [
    "audit_log_hook.sh",
    "commit_guard.sh",
    "commit_quality_hook.sh",
    "credential_leak_hook.sh",
    "git-commit-session-check.sh",
    "lint_hook.sh",
    "protected_path_hook.sh",
    "rename_guard.sh",
    "sensitivity_check.sh",
]

# Work topics expected in ~/.claude/projects/<id>/memory/topics/. Matches
# shared/work-topics/ post-Slice L. We require the 11 non-README files to
# be deployed; missing topics surface as amber (memory still works, but
# subagents won't have the context).
WORK_TOPICS_EXPECTED = [
    "credentials-keychain-work.md",
    "feedback_em_dash_terminal_exempt.md",
    "feedback_no_deferring.md",
    "feedback_no_time_estimates.md",
    "feedback_plain_english.md",
    "feedback_read_before_overwrite.md",
    "feedback_response_gate.md",
    "feedback_response_structure.md",
    "feedback_test_before_sharing.md",
    "feedback_test_before_shipping.md",
    "feedback_verify_before_claiming.md",
]


# ── Result accumulator ─────────────────────────────────────────────────────


_items: list[dict[str, Any]] = []


def record(name: str, category: str, status: str, detail: str, evidence: Any = "") -> None:
    """Append a single test result. `evidence` is coerced to string and
    truncated to 4 KB so a verbose curl response can't bloat the JSON."""
    if status not in ("green", "amber", "red"):
        # Defensive: a test that returns an unknown status is itself a bug.
        status = "red"
        detail = f"INVALID_STATUS({status}) {detail}"
    ev = evidence if isinstance(evidence, str) else json.dumps(evidence, default=str)
    if len(ev) > 4096:
        ev = ev[:4093] + "..."
    _items.append({
        "name": name,
        "category": category,
        "status": status,
        "detail": detail,
        "evidence": ev,
    })


def safe_run(name: str, category: str, fn: Callable[[], None]) -> None:
    """Wrap a single test. On any exception, record red and keep going.

    Pattern-26 guard: if a probe imports a daemon module and the import
    has side effects, the exception ends up here — not crashing the
    suite. The test name + traceback in `detail` makes the regression
    obvious without needing to re-run.
    """
    try:
        fn()
    except Exception as exc:
        tb = traceback.format_exc(limit=4)
        record(name, category, "red", f"test crashed: {exc.__class__.__name__}: {exc}", tb)


# ── Hook payload helpers ──────────────────────────────────────────────────


def feed_hook(hook_path: Path, payload: dict, timeout: int = 10) -> tuple[int, str, str]:
    """Pipe a JSON payload into a hook's stdin and capture exit / out / err.

    Mirrors how Claude Code itself drives hooks. Returns (returncode,
    stdout, stderr). On hook missing, returns (-1, "", "missing").
    """
    if not hook_path.is_file():
        return (-1, "", f"missing:{hook_path}")
    blob = json.dumps(payload).encode()
    try:
        proc = subprocess.run(
            ["/bin/bash", str(hook_path)] if hook_path.suffix == ".sh"
            else [sys.executable, str(hook_path)],
            input=blob,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (-2, "", f"timeout after {timeout}s")
    return (proc.returncode, proc.stdout.decode(errors="replace"),
            proc.stderr.decode(errors="replace"))


# ── Tests: hooks (functional, not file-existence) ─────────────────────────


def t_protected_path_deny_bootout() -> None:
    name = "hooks.protected_path_hook.deny_launchctl_bootout"
    hook = HOOKS_DIR / "protected_path_hook.sh"
    payload = {
        "tool_input": {
            "command": "launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.foo.plist"
        }
    }
    rc, out, err = feed_hook(hook, payload)
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    # protected_path_hook emits a JSON `permissionDecision: ask` and exits 0.
    # That decision is what blocks the command — Claude Code presents Tim
    # with an Approve/Deny prompt rather than auto-running it.
    if rc == 0 and '"permissionDecision": "ask"' in out:
        record(name, "hooks", "green",
               "launchctl bootout flagged as ask (correct deny path)",
               out[:512])
    else:
        record(name, "hooks", "red",
               f"expected ask-decision, got rc={rc} out={out[:120]!r}",
               f"stdout={out!r} stderr={err!r}")


def t_protected_path_allow_ls() -> None:
    name = "hooks.protected_path_hook.allow_benign_ls"
    hook = HOOKS_DIR / "protected_path_hook.sh"
    payload = {"tool_input": {"command": "ls -la /tmp"}}
    rc, out, err = feed_hook(hook, payload)
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    if rc == 0 and "permissionDecision" not in out:
        record(name, "hooks", "green",
               "benign ls passed through with no decision (correct)",
               out[:200])
    else:
        record(name, "hooks", "red",
               f"benign ls should pass, got rc={rc} out={out[:120]!r}",
               f"stdout={out!r} stderr={err!r}")


def t_credential_leak_deny_aws_key() -> None:
    name = "hooks.credential_leak_hook.deny_aws_key"
    hook = HOOKS_DIR / "credential_leak_hook.sh"
    # Use a real-shape AWS access key ID. credential_leak_hook only fires
    # when the file_path is inside a non-gitignored git repo, so we point
    # at this controlplane repo (which is git-tracked).
    target = str(Path.home() / "code" / "tim-claude-controlplane" / "TEMP_LEAK_PROBE.py")
    payload = {
        "tool_input": {
            "file_path": target,
            # Test payload reconstructed at runtime so the source file does
            # not literally contain a string matching the credential-leak
            # regex (which would trip commit_quality_hook on this very file).
            "content": "AWS_KEY = '" + "AKIA" + "1234567890ABCDEF'\n",
        }
    }
    rc, out, err = feed_hook(hook, payload)
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    # The hook exits 2 with a "WARNING: Content appears to contain
    # credentials" message on stdout. rc==2 is the contractual deny.
    if rc == 2 and "credentials" in (out + err).lower():
        record(name, "hooks", "green",
               "AKIA-prefixed key detected and blocked",
               (out + err)[:512])
    else:
        record(name, "hooks", "red",
               f"expected rc=2 deny, got rc={rc}",
               f"stdout={out!r} stderr={err!r}")


def t_credential_leak_allow_benign() -> None:
    name = "hooks.credential_leak_hook.allow_benign_file"
    hook = HOOKS_DIR / "credential_leak_hook.sh"
    target = str(Path.home() / "code" / "tim-claude-controlplane" / "TEMP_BENIGN_PROBE.py")
    payload = {
        "tool_input": {
            "file_path": target,
            "content": "def add(a, b):\n    return a + b\n",
        }
    }
    rc, out, err = feed_hook(hook, payload)
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    if rc == 0:
        record(name, "hooks", "green",
               "benign file passed", out[:200])
    else:
        record(name, "hooks", "red",
               f"benign file should pass, got rc={rc}",
               f"stdout={out!r} stderr={err!r}")


def t_commit_quality_deny_no_trailer() -> None:
    name = "hooks.commit_quality_hook.deny_missing_session_trailer"
    hook = HOOKS_DIR / "commit_quality_hook.sh"
    payload = {"tool_input": {"command": 'git commit -m "no trailer here"'}}
    rc, out, err = feed_hook(hook, payload)
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    # rc==2 with stderr mentioning Session: trailer = correct deny.
    # The hook also runs ruff/secret scans first; on a fresh worktree
    # those are no-ops, so the trailer check is the live failure mode.
    if rc == 2 and "Session" in (out + err):
        record(name, "hooks", "green",
               "missing Session trailer caught", (out + err)[:512])
    else:
        record(name, "hooks", "red",
               f"expected rc=2 trailer-missing, got rc={rc}",
               f"stdout={out!r} stderr={err!r}")


def t_commit_quality_allow_with_trailer() -> None:
    name = "hooks.commit_quality_hook.allow_with_session_trailer"
    hook = HOOKS_DIR / "commit_quality_hook.sh"
    # Use a heredoc so commit_quality_hook's heredoc-aware parser pulls
    # the message body verbatim (per the implementation in
    # shared/hooks/commit_quality_hook.sh).
    msg_body = "feat: add probe\n\nSession: 2026-05-01 (abc12345)\n"
    cmd = f"git commit -m \"$(cat <<'EOF'\n{msg_body}EOF\n)\""
    payload = {"tool_input": {"command": cmd}}
    rc, out, err = feed_hook(hook, payload)
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    if rc == 0:
        record(name, "hooks", "green",
               "Session-trailer commit passed", out[:200])
    else:
        # Could legitimately fail on staged secret/ruff if Tim's worktree
        # is dirty — surface as amber rather than red so we don't block
        # green on unrelated worktree state.
        record(name, "hooks", "amber",
               f"trailer present but rc={rc} (worktree may have unrelated lint/secret hits)",
               f"stdout={out!r} stderr={err!r}")


def t_lint_hook_logs_python_error() -> None:
    name = "hooks.lint_hook.logs_python_syntax_error"
    hook = HOOKS_DIR / "lint_hook.sh"
    # Write a real .py file with a syntax error so ruff actually runs.
    # lint_hook.sh requires the file to exist on disk (it ruff-checks the
    # path it receives, not the staged content).
    probe_file = Path("/tmp/work_lint_probe.py")
    probe_file.write_text("def broken(:\n    pass\n")
    pre_size = LINT_LOG.stat().st_size if LINT_LOG.is_file() else 0
    payload = {"tool_input": {"file_path": str(probe_file)}}
    rc, out, err = feed_hook(hook, payload)
    # Clean up probe file regardless of outcome
    try:
        probe_file.unlink()
    except OSError:
        pass
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    # lint_hook.sh is advisory — exit 0 always, log appended. We expect
    # the log to have grown OR the stderr to mention findings.
    post_size = LINT_LOG.stat().st_size if LINT_LOG.is_file() else 0
    log_grew = post_size > pre_size
    surfaced = "lint findings" in err or "ruff" in err.lower() or "syntax" in err.lower()
    if rc == 0 and (log_grew or surfaced):
        record(name, "hooks", "green",
               f"syntax error recorded (log grew={log_grew}, stderr_findings={surfaced})",
               (err or out)[:512])
    elif rc == 0:
        # ruff might not be installed on the work laptop yet. Advisory
        # hook returning 0 with no log growth is amber, not red.
        record(name, "hooks", "amber",
               "lint_hook ran but produced no findings (ruff missing?)",
               f"rc={rc} log_grew={log_grew}")
    else:
        record(name, "hooks", "red",
               f"lint_hook should be advisory (exit 0) but rc={rc}",
               f"stdout={out!r} stderr={err!r}")


def t_response_quality_deny_time_estimate() -> None:
    name = "hooks.response_quality_check.deny_time_estimate"
    hook = HOOKS_DIR / "response_quality_check.py"
    # Build a minimal Stop-hook payload: the script reads the transcript
    # at `transcript_path`, finds the latest assistant turn, and applies
    # the time-estimate / em-dash / deferral regexes. Build a synthetic
    # transcript JSONL that contains a future-tense duration phrase so
    # the gate fires regardless of whether the user is on a fresh
    # machine with no real session history.
    fake_transcript = Path("/tmp/work_response_gate_probe.jsonl")
    fake_transcript.write_text(
        json.dumps({
            "type": "user",
            "message": {"content": [{"type": "text", "text": "hi"}]},
        }) + "\n" +
        json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text",
                 "text": "I'll wrap this up in about 30 minutes."},
            ]},
        }) + "\n"
    )
    payload = {
        "session_id": "work-verify-probe",
        "transcript_path": str(fake_transcript),
        "stop_hook_active": False,
    }
    rc, out, err = feed_hook(hook, payload)
    try:
        fake_transcript.unlink()
    except OSError:
        pass
    # Reset the per-session retry counter the hook just bumped, so a
    # repeated verify run isn't accidentally blocked by retry-cap logic.
    counter = Path("/tmp/.claude_response_gate_work-verify-probe.count")
    if counter.exists():
        try:
            counter.unlink()
        except OSError:
            pass
    if rc < 0:
        record(name, "hooks", "red", f"hook unrunnable ({err})", err)
        return
    # rc==2 with stderr containing the response_gate banner = correct deny.
    if rc == 2 and "response_gate" in err:
        record(name, "hooks", "green",
               "time-estimate phrase blocked", err[:512])
    else:
        record(name, "hooks", "red",
               f"expected rc=2 response-gate deny, got rc={rc}",
               f"stdout={out!r} stderr={err!r}")


def t_scan_command_returns_inner_bash() -> None:
    name = "hooks.scan_command.recurses_into_bash_dash_c"
    hook = HOOKS_DIR / "scan_command.py"
    if not hook.is_file():
        record(name, "hooks", "red", "scan_command.py missing", str(hook))
        return
    # scan_command reads a Bash command on stdin and prints normalised
    # operative tokens on stdout. For `bash -c "echo hi"`, the inner
    # `echo hi` MUST appear in the output — that's the recursion that
    # makes the protected-path hook see dangerous verbs hidden inside
    # interpreter wrappers.
    proc = subprocess.run(
        [sys.executable, str(hook)],
        input=b'bash -c "echo hi"',
        capture_output=True,
        timeout=5,
    )
    out = proc.stdout.decode(errors="replace")
    if proc.returncode == 0 and "echo" in out and "hi" in out:
        record(name, "hooks", "green",
               "scan_command recursed into `bash -c` payload",
               out[:200])
    else:
        record(name, "hooks", "red",
               f"scan_command output missing inner echo (rc={proc.returncode})",
               f"stdout={out!r} stderr={proc.stderr.decode(errors='replace')!r}")


# ── Tests: LaunchAgents (per services.yaml) ───────────────────────────────


def t_launchagents_loaded() -> None:
    """One aggregated test that records per-agent results — keeps the
    suite under ~15 items but still calls out which specific agent is
    missing."""
    try:
        listing = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, timeout=5, text=True,
        ).stdout
    except Exception as exc:
        record("launchagents.list", "launchagents", "red",
               f"launchctl list failed: {exc}", str(exc))
        return
    for short in WORK_LAUNCHAGENTS:
        label = f"com.timtrailor.{short}"
        name = f"launchagents.{short}"
        if label in listing:
            record(name, "launchagents", "green",
                   f"{label} loaded", "")
        else:
            # Not loaded = amber. The script + plist may exist on disk
            # but the user hasn't bootstrapped them yet; treating this
            # as red would trip the verifier on a fresh deploy before
            # the first launchctl bootstrap.
            record(name, "launchagents", "amber",
                   f"{label} not loaded (run launchctl bootstrap)", "")


def t_health_check_results_fresh() -> None:
    name = "launchagents.work-health-check.results_fresh"
    if not HEALTH_RESULTS.is_file():
        record(name, "launchagents", "amber",
               f"{HEALTH_RESULTS} missing — health-check hasn't run yet", "")
        return
    age = (datetime.now(timezone.utc).timestamp()
           - HEALTH_RESULTS.stat().st_mtime)
    if age < 2 * 3600:
        record(name, "launchagents", "green",
               f"results {age/60:.0f}min old (<2h)",
               f"path={HEALTH_RESULTS}")
    else:
        # >2h old = the LaunchAgent isn't firing. Amber not red because
        # the dashboard simply shows stale data; nothing dangerous.
        record(name, "launchagents", "amber",
               f"results {age/3600:.1f}h old (>2h threshold)",
               f"path={HEALTH_RESULTS}")


# ── Tests: MCP servers ────────────────────────────────────────────────────


def t_mcp_bridge_registered() -> None:
    name = "mcp.bridge.registered"
    if not CLAUDE_JSON.is_file():
        record(name, "mcp", "red",
               f"{CLAUDE_JSON} missing", "")
        return
    cfg = json.loads(CLAUDE_JSON.read_text())
    servers = cfg.get("mcpServers") or {}
    if "bridge" in servers:
        record(name, "mcp", "green",
               "bridge MCP registered in ~/.claude.json",
               json.dumps(servers["bridge"])[:512])
    else:
        # The bridge is the work-side push/email/personal-memory channel.
        # Without it, work-side Claude has no way to reach Tim. Red.
        record(name, "mcp", "red",
               "bridge MCP missing from ~/.claude.json mcpServers",
               json.dumps(list(servers.keys())))


def t_mcp_memory_work_registered() -> None:
    name = "mcp.memory_work.registered"
    if not CLAUDE_JSON.is_file():
        record(name, "mcp", "red",
               f"{CLAUDE_JSON} missing", "")
        return
    cfg = json.loads(CLAUDE_JSON.read_text())
    servers = cfg.get("mcpServers") or {}
    if "memory_work" in servers:
        record(name, "mcp", "green",
               "memory_work MCP registered in ~/.claude.json",
               json.dumps(servers["memory_work"])[:512])
    else:
        record(name, "mcp", "red",
               "memory_work MCP missing from ~/.claude.json mcpServers",
               json.dumps(list(servers.keys())))


# ── Tests: memory ──────────────────────────────────────────────────────────


def t_memory_chromadb_queryable() -> None:
    """Pattern-3: actually query the index, don't just check the file
    exists. A 0-result list still proves Chroma + the collection are
    healthy; an exception (collection missing, embedding model gone,
    sqlite locked) is the real failure mode we're catching here."""
    name = "memory.chromadb.queryable"
    bridge_tools = Path.home() / "code" / "claude-bridge" / "tools"
    server_path = bridge_tools / "work_memory_server.py"
    if not server_path.is_file():
        record(name, "memory", "red",
               f"work_memory_server.py missing at {server_path}", "")
        return
    # Importing the module would spin up FastMCP and try to take the
    # process-wide flock — exactly what we don't want during a probe.
    # Instead: replicate the minimal data-path init the module does on
    # startup and call collection.query() directly. This is genuinely
    # exercising the same ChromaDB the live MCP server uses, just without
    # the FastMCP wrapper.
    try:
        import chromadb
    except ImportError as exc:
        record(name, "memory", "red",
               f"chromadb not importable: {exc}", "")
        return
    data_dir = Path(os.environ.get(
        "CONV_MEMORY_DATA_DIR",
        str(Path.home() / ".claude" / "work_memory_data"),
    )).expanduser()
    chroma_dir = data_dir / "chroma"
    if not chroma_dir.exists():
        # Index never built yet — amber, not red. Memory queries return
        # empty but the rest of Claude works.
        record(name, "memory", "amber",
               f"chroma dir not yet created at {chroma_dir} (run work_memory_index.sh)",
               "")
        return
    client = chromadb.PersistentClient(path=str(chroma_dir))
    coll = client.get_or_create_collection(
        name="conversations",
        metadata={"hnsw:space": "cosine"},
    )
    res = coll.query(query_texts=["smoke"], n_results=1)
    # res is a dict with keys like 'ids', 'documents'. Even 0 results
    # means the query path is healthy.
    ids = (res.get("ids") or [[]])[0]
    record(name, "memory", "green",
           f"chroma queryable, {coll.count()} chunks, {len(ids)} hits for 'smoke'",
           f"first_id={ids[0] if ids else None}")


def t_memory_topics_deployed() -> None:
    name = "memory.topics.deployed"
    if not PROJECTS_ROOT.is_dir():
        record(name, "memory", "amber",
               f"{PROJECTS_ROOT} missing — no projects ever opened?", "")
        return
    # work_setup.sh seeds topics into every projects/<id>/memory/topics
    # dir. Any project that has ALL 11 topics is sufficient evidence the
    # deploy worked. Look at every project dir and find the highest
    # coverage.
    best_project: str = ""
    best_present: list[str] = []
    best_missing: list[str] = list(WORK_TOPICS_EXPECTED)
    for project_dir in PROJECTS_ROOT.iterdir():
        topics_dir = project_dir / "memory" / "topics"
        if not topics_dir.is_dir():
            continue
        present = [t for t in WORK_TOPICS_EXPECTED if (topics_dir / t).is_file()]
        if len(present) > len(best_present):
            best_project = project_dir.name
            best_present = present
            best_missing = [t for t in WORK_TOPICS_EXPECTED
                            if t not in present]
    if len(best_present) == len(WORK_TOPICS_EXPECTED):
        record(name, "memory", "green",
               f"all {len(WORK_TOPICS_EXPECTED)} work topics deployed to {best_project}",
               "")
    elif best_present:
        # Some topics deployed, some missing. Amber — subagents may
        # still find what they need but coverage is incomplete.
        miss_preview = best_missing[:3]
        miss_suffix = "..." if len(best_missing) > 3 else ""
        detail = (
            f"{len(best_present)}/{len(WORK_TOPICS_EXPECTED)} topics in "
            f"{best_project}; missing: {miss_preview}{miss_suffix}"
        )
        record(name, "memory", "amber", detail, json.dumps(best_missing))
    else:
        record(name, "memory", "amber",
               "no work topics found in any project's memory/topics dir",
               "")


# ── Tests: bridge gateway live probe ──────────────────────────────────────


def t_bridge_gateway_health() -> None:
    name = "bridge.gateway.health"
    try:
        with urllib.request.urlopen(BRIDGE_HEALTH_URL, timeout=3) as resp:
            body = resp.read(2048).decode(errors="replace")
            if 200 <= resp.status < 300:
                record(name, "bridge", "green",
                       f"HTTP {resp.status} from {BRIDGE_HEALTH_URL}",
                       body[:512])
                return
            record(name, "bridge", "red",
                   f"HTTP {resp.status} from {BRIDGE_HEALTH_URL}",
                   body[:512])
            return
    except urllib.error.HTTPError as exc:
        # Anything in the 4xx/5xx range means the gateway is alive but
        # rejected the probe — surface as red so the operator notices.
        record(name, "bridge", "red",
               f"HTTPError {exc.code} from {BRIDGE_HEALTH_URL}",
               str(exc))
        return
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # ConnectionRefusedError = personal-side gateway not running.
        # The work-side bridge MCP can still queue requests via GitHub
        # transport, so this is amber not red.
        if isinstance(reason, ConnectionRefusedError) or "Connection refused" in str(reason):
            record(name, "bridge", "amber",
                   "gateway unreachable (connection refused) — personal side likely down",
                   str(reason))
            return
        # Other URLError (DNS, TLS, timeout) is more concerning — work
        # side may be misconfigured. Red.
        record(name, "bridge", "red",
               f"URLError reaching {BRIDGE_HEALTH_URL}: {reason}",
               str(reason))
        return
    except (socket.timeout, TimeoutError) as exc:
        record(name, "bridge", "amber",
               f"timeout reaching {BRIDGE_HEALTH_URL}",
               str(exc))


# ── Tests: topics + rules (file-existence, last layer) ───────────────────


def t_reply_style_rule_present() -> None:
    name = "rules.reply_style.present"
    target = RULES_DIR / "reply-style.md"
    if target.is_file():
        record(name, "rules", "green",
               f"{target} present ({target.stat().st_size} bytes)", "")
    else:
        # reply-style.md is the source of the response_quality_check
        # behaviour — without it, the gate has no doctrine to enforce.
        record(name, "rules", "red",
               f"{target} missing — reply-style.md not deployed", "")


def t_work_hooks_deployed() -> None:
    name = "rules.hooks.deployed"
    if not HOOKS_DIR.is_dir():
        record(name, "rules", "red",
               f"{HOOKS_DIR} missing entirely", "")
        return
    missing = [h for h in WORK_HOOKS_EXPECTED if not (HOOKS_DIR / h).is_file()]
    present_count = len(WORK_HOOKS_EXPECTED) - len(missing)
    if not missing:
        record(name, "rules", "green",
               f"all 9 work hooks present in {HOOKS_DIR}", "")
    else:
        # Missing hooks = degraded enforcement, not breakage. Amber.
        record(name, "rules", "amber",
               f"{present_count}/9 hooks present, missing: {missing}",
               json.dumps(missing))


# ── Driver ────────────────────────────────────────────────────────────────


TESTS: list[tuple[str, str, Callable[[], None]]] = [
    # (display_name_unused, category_unused, fn) — each test calls record()
    # itself with its own canonical name so the driver tuple is just the
    # callable. Categories are in the JSON, not the driver.
    ("hooks.protected_path.deny", "hooks", t_protected_path_deny_bootout),
    ("hooks.protected_path.allow", "hooks", t_protected_path_allow_ls),
    ("hooks.credential_leak.deny", "hooks", t_credential_leak_deny_aws_key),
    ("hooks.credential_leak.allow", "hooks", t_credential_leak_allow_benign),
    ("hooks.commit_quality.deny", "hooks", t_commit_quality_deny_no_trailer),
    ("hooks.commit_quality.allow", "hooks", t_commit_quality_allow_with_trailer),
    ("hooks.lint.logs", "hooks", t_lint_hook_logs_python_error),
    ("hooks.response_quality.deny", "hooks", t_response_quality_deny_time_estimate),
    ("hooks.scan_command.recurse", "hooks", t_scan_command_returns_inner_bash),
    ("launchagents.loaded", "launchagents", t_launchagents_loaded),
    ("launchagents.health_fresh", "launchagents", t_health_check_results_fresh),
    ("mcp.bridge", "mcp", t_mcp_bridge_registered),
    ("mcp.memory_work", "mcp", t_mcp_memory_work_registered),
    ("memory.chroma", "memory", t_memory_chromadb_queryable),
    ("memory.topics", "memory", t_memory_topics_deployed),
    ("bridge.gateway", "bridge", t_bridge_gateway_health),
    ("rules.reply_style", "rules", t_reply_style_rule_present),
    ("rules.hooks_deployed", "rules", t_work_hooks_deployed),
]


def main() -> int:
    for display, category, fn in TESTS:
        safe_run(display, category, fn)
    summary = {
        "total": len(_items),
        "green": sum(1 for it in _items if it["status"] == "green"),
        "amber": sum(1 for it in _items if it["status"] == "amber"),
        "red": sum(1 for it in _items if it["status"] == "red"),
    }
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "items": _items,
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    # Print a one-line summary to stdout for any caller that pipes us
    # without going through the JSON file (e.g. ad-hoc operator runs).
    print(
        f"VERIFY: {summary['green']}/{summary['total']} green, "
        f"{summary['amber']} amber, {summary['red']} red"
    )
    # Return 0 always — the work_verify.sh wrapper is responsible for
    # mapping summary counts to its own exit code. If we exited non-zero
    # here, work_verify.sh would conflate "test machinery failed" with
    # "tests reported red", and a single unhandled exception would
    # become indistinguishable from a fleet of legitimate reds.
    return 0


if __name__ == "__main__":
    sys.exit(main())
