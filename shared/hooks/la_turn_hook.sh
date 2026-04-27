#!/bin/bash
# la_turn_hook.sh — Claude Code Stop / UserPromptSubmit hook.
#
# Posts turn-boundary events to the conversation server so it can drive the
# per-tab Live Activity (Dynamic Island) on the iPhone.
#
# Hard rules:
#   - Always exit 0. The hook MUST NOT block Claude Code.
#   - No shell interpolation of user-controlled fields. The full Claude Code
#     JSON is piped to a single Python process via stdin; payload assembly
#     happens entirely inside Python.
#   - Curl is fire-and-forget with a 2 s timeout.
#
# Source of truth: tim-claude-controlplane/shared/hooks/la_turn_hook.sh.
# Deployed to /Users/timtrailor/.claude/hooks/la_turn_hook.sh on Mac Mini.

set +e

PY=/opt/homebrew/bin/python3
[ -x "$PY" ] || PY=/usr/bin/python3

INPUT=$(cat)

PY_SCRIPT=$(cat <<'PYEOF'
import json, os, shutil, subprocess, sys

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

def s(key):
    v = data.get(key, '')
    if isinstance(v, (dict, list)):
        v = json.dumps(v)
    return str(v) if v is not None else ''

pane_pid = pane_id = window_index = tmux_session = ''
if os.environ.get('TMUX') and os.environ.get('TMUX_PANE'):
    # Resolve tmux: Homebrew → /usr/local → /usr/bin → PATH. If none found,
    # pane fields stay empty and the server's guard rejects the event because
    # tmux_session != 'mobile'. Cleaner failure than a hardcoded path.
    tmux_bin = next((p for p in (
        '/opt/homebrew/bin/tmux',
        '/usr/local/bin/tmux',
        '/usr/bin/tmux',
    ) if os.access(p, os.X_OK)), shutil.which('tmux'))
    if tmux_bin:
        try:
            out = subprocess.run(
                [tmux_bin, 'display-message', '-p',
                 '-t', os.environ['TMUX_PANE'],
                 '#{pane_pid}|#{pane_id}|#{window_index}|#{session_name}'],
                capture_output=True, text=True, timeout=1,
            ).stdout.strip()
            parts = out.split('|') if out else []
            if len(parts) == 4:
                pane_pid, pane_id, window_index, tmux_session = parts
        except Exception:
            pass

prompt = s('prompt')
if len(prompt) > 200:
    prompt = prompt[:200]

payload = {
    'event': s('hook_event_name'),
    'session_id': s('session_id'),
    'transcript_path': s('transcript_path'),
    'cwd': s('cwd'),
    'pane_pid': pane_pid,
    'pane_id': pane_id,
    'window_index': window_index,
    'tmux_session': tmux_session,
    'prompt_preview': prompt,
}
sys.stdout.write(json.dumps(payload))
PYEOF
)

PAYLOAD=$(printf '%s' "$INPUT" | "$PY" -c "$PY_SCRIPT" 2>/dev/null)

# Empty payload (Python crashed pre-print) — give up silently.
[ -n "$PAYLOAD" ] || exit 0

# Fire-and-forget; max 2 s. stderr suppressed — Claude Code reads stderr as a signal.
( curl -sm 2 -X POST http://localhost:8081/internal/claude-hook \
       -H "Content-Type: application/json" \
       --data-binary "$PAYLOAD" >/dev/null 2>&1 ) &
disown 2>/dev/null

exit 0
