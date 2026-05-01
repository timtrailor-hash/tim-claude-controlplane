#!/usr/bin/env python3
"""scan_command.py — bashlex-based command normaliser for protected_path_hook.

Reads a Bash command on stdin, prints a "scan string" on stdout that contains
ONLY the operative shell tokens — the tokens that are actually executed.
Tokens that are pure data (the value of `-m`/`--message` or `-F`/`--file`
arguments to git commit/tag/notes/stash, or single-quoted heredoc bodies) are
dropped. Command-substitutions `$(...)`, backticks, process-substitutions
`<(...)` `>(...)`, and the inner scripts of `bash -c` / `eval` / `python -c`
ARE kept, because the shell evaluates them as real code.

Pattern-28 fix: replaces the previous content-grep-on-raw-command approach,
which produced spurious "user doesn't want to proceed" rejections whenever a
commit body mentioned launchctl/sudo/Library/LaunchAgents.

Failure mode: the walker NEVER silently drops a node it doesn't recognise.
Unknown node types fall through to a generic recursion that emits the node's
slice of the original command. On any parse exception the whole command falls
back to raw. False positives are acceptable; missing a real launchctl/sudo
call is not.
"""
from __future__ import annotations

import re
import sys

RAW = sys.stdin.read()


# ── Pre-process: strip QUOTED-heredoc bodies. ──────────────────────────────
# Bash treats `<<'EOF'`, `<<"EOF"`, `<<\EOF`, and the `<<-` variants of all
# three as "no-expansion" heredocs: the body is literal data, no
# substitutions are evaluated. Stripping the body is semantically equivalent
# to leaving it in. We do this BEFORE bashlex parses because bashlex 0.18
# fails to parse some quoted heredoc forms.
#
# UNQUOTED heredocs (`<<EOF` and `<<-EOF`) are NOT stripped — their bodies
# CAN contain executable substitutions, so bashlex must see them.
#
# Known residual false-positive case: an UNQUOTED heredoc whose body
# contains a dangerous-verb plaintext (no actual `$(...)` substitution) WILL
# surface that plaintext in the scan and may trip a pattern check. This is
# a deliberate trade-off — we cannot statically tell whether an unquoted
# heredoc body is "data Tim wrote about a verb" or "code that the shell
# will expand." The Pattern-28 fix protects QUOTED heredocs (the common
# case in commit message bodies); the unquoted form remains a paraphrase
# situation. See lessons.md Pattern 28.
_QUOTED_HEREDOC = re.compile(
    r"""
    (<<-?\s*)              # opening redirect, possibly tab-strip variant (`<<-`)
    (\\|['"])              # quote/escape char (any of \, ', ")
    ([A-Z_][A-Z0-9_]*)     # terminator identifier
    (\2)?                  # optional closing quote (matches opener)
    (\r?\n)                # newline after opener
    .*?                    # body (lazy, multiline)
    (\r?\n[\t ]*)          # newline + optional whitespace before terminator
                           # (bash `<<-` strips leading tabs; we permit them)
    \3                     # exact terminator
    (\r?\n|$)              # newline or EOF after terminator
    """,
    re.DOTALL | re.VERBOSE,
)


def _strip_quoted_heredocs(cmd: str) -> str:
    def _repl(m: re.Match) -> str:
        op, q, ident = m.group(1), m.group(2), m.group(3)
        nl1, nl2 = m.group(5), m.group(7)
        return f"{op}{q}{ident}{q}{nl1}{ident}{nl2}"
    return _QUOTED_HEREDOC.sub(_repl, cmd)


# ── Interpreter awareness. ─────────────────────────────────────────────────
# These commands take a script as a string argument. We must recurse into
# that script as Bash code so dangerous verbs inside don't slip through.
BASH_INTERPRETERS = {"bash", "sh", "zsh", "ash", "dash", "ksh"}
# These commands also take a script string but it's NOT bash. We cannot
# parse them with bashlex; emit the script verbatim so regex pattern matchers
# in the calling hook see the dangerous-verb plaintext (e.g. `python -c
# 'os.system("launchctl kickstart -k foo")'` still emits "launchctl
# kickstart" into the scan).
NON_BASH_INTERPRETERS = {
    "python", "python2", "python3", "python3.11", "python3.12",
    "perl", "ruby", "node", "deno", "osascript", "swift", "lua",
    "tclsh", "awk", "gawk",
}
# `eval` takes Bash code as args — concatenate args and recurse-as-bash.
EVAL_VERBS = {"eval"}

# git verbs whose -m/-F values are message data (not code).
GIT_MSG_VERBS = {"commit", "tag", "notes", "stash"}
MSG_FLAGS = {"-m", "-F", "--message", "--file"}

# ── Pattern-28 second-order fix (2026-05-01). ─────────────────────────────
# The first Pattern-28 fix (this scanner) successfully stripped commit-body
# data from the scan, but the calling hook's Pattern 1 still ran a
# match-anywhere regex for "Library/LaunchAgents" against the operative
# tokens. That meant a benign `cp ~/Library/LaunchAgents/x.plist /tmp/y` —
# a pure file copy with NO state change — still matched, because the path
# substring appeared in a positional argument to `cp`.
#
# Fix: classify each operative argument by its semantic role. When a path
# inside `Library/LaunchAgents` or `Library/LaunchDaemons` appears in a
# WRITE-TARGET position (a destination of cp/mv/rsync/install/ln, an arg
# to tee/rm/unlink/chmod/chown/chflags/touch, or the target of `>`/`>>`/
# `&>` redirects), emit the sentinel `__LA_WRITE__` into the scan. The
# calling hook's Pattern 1 then matches the sentinel rather than the raw
# substring, so paths in read or source positions don't trip the alert.
#
# Anti-pattern recorded for future hooks: NEVER match a load-bearing path
# substring (a path that can appear in an arg as data) anywhere in a
# command. Always classify by argument role (write target vs source vs
# verb token) at AST level. See lessons.md Pattern 28 addendum.
LA_PATH_RE = re.compile(r"Library/(LaunchAgents|LaunchDaemons)")

# System-path write targets: paths that BEGIN with /etc/ or /Library/.
# Note the leading slash — `~/Library/` (user home) and any other prefix
# does NOT qualify as a system path. The previous Pattern-5 regex matched
# `/Library/` anywhere in the line, so a `cp /Users/x/Library/... /tmp/...`
# (a path containing the substring `/Library/`) tripped it. We now anchor
# at the start of the literal path arg.
SYS_PATH_RE = re.compile(r"^(/etc/|/Library/)")

# Commands where the LAST non-flag positional argument is a write target
# (the destination), and earlier non-flag args are sources / read-only.
LAST_ARG_IS_DEST = {"cp", "mv", "rsync", "install", "ln"}

# Commands where ALL non-flag positional args are write targets.
ALL_ARGS_ARE_WRITES = {
    "tee", "rm", "unlink", "rmdir", "chmod", "chown", "chflags",
    "touch", "mkdir",
}

# Redirect operators that WRITE to their target.
WRITE_REDIRECT_TYPES = {">", ">>", "&>", "&>>", ">|", "1>", "2>", "1>>", "2>>"}


def _is_flag(word: str) -> bool:
    """Return True for `-x` / `--long-opt` style argv tokens."""
    return word.startswith("-") and word != "-"


def _word_literal(node) -> str:
    """Best-effort literal text of a `word` node, with leading `~` expanded.
    Used only for path classification (LA_PATH_RE). Quote-stripping is OK
    because we're checking for a substring match, not re-executing."""
    w = getattr(node, "word", "") or ""
    if len(w) >= 2 and w[0] == w[-1] and w[0] in ("'", '"'):
        w = w[1:-1]
    return w


def _emit_la_marker_if_match(node, out: list[str]) -> None:
    """If the given word node's literal text contains a Library/LaunchAgents
    (or LaunchDaemons) path, emit the __LA_WRITE__ sentinel.

    Also emit __SYS_WRITE__ if the path begins with /etc/ or /Library/
    (system path), so Pattern 5 of the calling hook can match without
    using the previous overly broad regex that hit user paths under
    /Users/<x>/Library/.
    """
    txt = _word_literal(node)
    if LA_PATH_RE.search(txt):
        out.append("__LA_WRITE__")
    if SYS_PATH_RE.match(txt):
        out.append("__SYS_WRITE__")


def _node_text(node, raw: str) -> str:
    """Return the slice of `raw` that this node spans, or its textual repr."""
    pos = getattr(node, "pos", None)
    if pos and isinstance(pos, tuple) and len(pos) == 2:
        a, b = pos
        try:
            return raw[a:b]
        except Exception:
            pass
    w = getattr(node, "word", None)
    if w:
        return w
    return ""


def _word_text_only(node) -> str:
    """Return the literal word text of a `word` node, with surrounding quotes
    stripped if it's a single quoted-string token. Used when we need the
    contents-as-string for interpreter recursion."""
    w = getattr(node, "word", "") or ""
    # If word is wrapped in quotes, peel them. bashlex preserves the original
    # source's quotes inside .word. We want the unquoted contents because
    # we're going to re-parse it as Bash.
    if len(w) >= 2 and w[0] == w[-1] and w[0] in ("'", '"'):
        w = w[1:-1]
    return w


def _emit(node, out: list[str], raw: str, in_msg_arg: bool = False) -> None:
    kind = getattr(node, "kind", None)

    if kind == "commandsubstitution" or kind == "processsubstitution":
        # Real execution context. Always scan, regardless of in_msg_arg.
        inner = getattr(node, "command", None)
        if inner is not None:
            _emit(inner, out, raw, in_msg_arg=False)
        return

    if kind == "word":
        # Recurse into any commandsubstitution/processsubstitution children.
        had_subst = False
        for p in getattr(node, "parts", None) or []:
            pk = getattr(p, "kind", None)
            if pk in ("commandsubstitution", "processsubstitution"):
                had_subst = True
                _emit(p, out, raw, in_msg_arg=False)
            elif pk == "parameter":
                # Parameter expansion. Recurse generically — it may contain
                # cmd-substs in `${var:?$(...)}` forms.
                _emit_generic(p, out, raw)
        if not had_subst and not in_msg_arg:
            w = getattr(node, "word", None)
            if w:
                out.append(w)
        return

    if kind == "redirect":
        # Redirect target is real execution context (`> /Library/...` writes).
        rtype = getattr(node, "type", "") or ""
        out.append(rtype)
        outp = getattr(node, "output", None)
        if outp is not None:
            # If this redirect WRITES to its target and the target path is
            # under Library/LaunchAgents, emit the __LA_WRITE__ sentinel so
            # the hook's Pattern 1 can match an actual write — not just any
            # mention of the LA path in a read-only context.
            if rtype in WRITE_REDIRECT_TYPES and getattr(outp, "kind", None) == "word":
                _emit_la_marker_if_match(outp, out)
            _emit(outp, out, raw, in_msg_arg=False)
        # heredoc body (UNQUOTED): inner substitutions are real; descend.
        hd = getattr(node, "heredoc", None)
        if hd is not None:
            _emit_generic(hd, out, raw)
        return

    if kind == "command":
        parts = getattr(node, "parts", None) or []
        words = [c for c in parts if getattr(c, "kind", None) == "word"]
        first = (getattr(words[0], "word", "") if words else "") or ""
        second = (getattr(words[1], "word", "") if len(words) >= 2 else "") or ""

        # ── Interpreter recursion: bash -c / sh -c / zsh -c / eval ─────
        if first in BASH_INTERPRETERS and second == "-c" and len(words) >= 3:
            # `bash -c "<script>"` — the third word is a script in Bash.
            script = _word_text_only(words[2])
            _recurse_into_string(script, out)
            # Emit the bash/sh tokens themselves so a `bash` invocation is
            # still visible to top-level pattern matchers.
            out.append(first)
            out.append("-c")
            return

        if first in NON_BASH_INTERPRETERS and any(
            getattr(w, "word", "") in ("-c", "-e", "--command", "--eval")
            for w in words[1:]
        ):
            # python -c '...' / perl -e '...' etc. We can't parse those, but
            # we can include the script string VERBATIM so the hook regexes
            # match plaintext occurrences of dangerous verbs.
            for i, w in enumerate(words):
                wtxt = getattr(w, "word", "") or ""
                if wtxt in ("-c", "-e", "--command", "--eval") and i + 1 < len(words):
                    script = _word_text_only(words[i + 1])
                    out.append(script)
            out.append(first)
            return

        if first in EVAL_VERBS and len(words) >= 2:
            # `eval "code"` — concatenate args (after `eval`) and recurse.
            script = " ".join(_word_text_only(w) for w in words[1:])
            _recurse_into_string(script, out)
            out.append("eval")
            return

        # ── git commit/tag/notes/stash: -m/-F values are data ───────────
        is_git_msg = (
            len(words) >= 2
            and first == "git"
            and second in GIT_MSG_VERBS
        )
        msg_arg_ids: set[int] = set()
        if is_git_msg:
            for i, w in enumerate(words):
                wtxt = getattr(w, "word", "") or ""
                if wtxt in MSG_FLAGS and i + 1 < len(words):
                    msg_arg_ids.add(id(words[i + 1]))

        # ── Pattern-28-2nd-order: classify write-target args by command. ─
        # If this command writes to a path under Library/LaunchAgents, emit
        # __LA_WRITE__ once for each such write target. Read-only and source
        # args are NOT marked.
        non_flag_word_indices = [
            i for i, w in enumerate(words)
            if i > 0 and not _is_flag(getattr(w, "word", "") or "")
        ]
        if first in LAST_ARG_IS_DEST and non_flag_word_indices:
            # cp / mv / rsync / install / ln: only the last positional arg
            # is the destination (where new content lands).
            dest_idx = non_flag_word_indices[-1]
            _emit_la_marker_if_match(words[dest_idx], out)
        elif first in ALL_ARGS_ARE_WRITES:
            # tee / rm / chmod / etc: every positional arg is a write target.
            for i in non_flag_word_indices:
                _emit_la_marker_if_match(words[i], out)

        for p in parts:
            if getattr(p, "kind", None) == "word" and id(p) in msg_arg_ids:
                _emit(p, out, raw, in_msg_arg=True)
            else:
                _emit(p, out, raw, in_msg_arg=in_msg_arg)
        return

    # ── Compound forms: list, pipeline, compound (if/while/for/case),
    #    function, group, subshell, … ───────────────────────────────────
    if kind in ("list", "pipeline", "compound", "function", "group", "subshell"):
        # Walk every part. For function/compound, the body lives under either
        # .parts, .list, or .command depending on bashlex version.
        for p in getattr(node, "parts", None) or []:
            _emit(p, out, raw, in_msg_arg=False)
        for b in getattr(node, "list", None) or []:
            _emit(b, out, raw, in_msg_arg=False)
        body = getattr(node, "command", None)
        if body is not None:
            _emit(body, out, raw, in_msg_arg=False)
        return

    # ── Unknown node type: GENERIC recurse + raw-text fallback. ────────
    _emit_generic(node, out, raw)


def _emit_generic(node, out: list[str], raw: str | None = None) -> None:
    """Generic fallback: recurse over child-node-like attributes, AND emit
    the node's raw text slice if available. False-positive direction — we
    never silently drop unknown structure."""
    raw = raw if raw is not None else RAW
    # Emit the raw slice so pattern-matchers see ANY plaintext dangerous verb.
    txt = _node_text(node, raw)
    if txt:
        out.append(txt)
    # Recurse over standard child-node containers.
    for attr in ("parts", "list", "command", "output", "input", "heredoc"):
        v = getattr(node, attr, None)
        if v is None:
            continue
        if isinstance(v, list):
            for item in v:
                if hasattr(item, "kind"):
                    _emit(item, out, raw, in_msg_arg=False)
        elif hasattr(v, "kind"):
            _emit(v, out, raw, in_msg_arg=False)


def _recurse_into_string(script: str, out: list[str]) -> None:
    """Treat `script` as a Bash command and emit its scan tokens into `out`.
    On parse failure, append the raw string (false-positive safe)."""
    if not script:
        return
    pre = _strip_quoted_heredocs(script)
    try:
        import bashlex
        trees = bashlex.parse(pre)
    except Exception:
        out.append(script)
        return
    for t in trees:
        _emit(t, out, pre, in_msg_arg=False)


# ── Top-level scan ──────────────────────────────────────────────────────────


def _scan(cmd: str) -> str:
    try:
        import bashlex
    except ImportError:
        return cmd
    pre = _strip_quoted_heredocs(cmd)
    try:
        trees = bashlex.parse(pre)
    except Exception:
        # Parse failure: return the heredoc-stripped version, NOT the raw
        # command. The stripped version has guaranteed-literal heredoc bodies
        # removed (those don't execute), but still preserves operative tokens
        # AND any substitutions inside `$(...)` (which DO execute). This is
        # the right conservative direction: false positives over false
        # negatives.
        return pre
    out: list[str] = []
    for t in trees:
        _emit(t, out, pre)
    return " ".join(out) if out else pre


def main() -> None:
    result = _scan(RAW)
    if not result.strip():
        result = RAW
    sys.stdout.write(result)


if __name__ == "__main__":
    main()
