# Mac Mini watchdog (laptop-side)

## What it does

Runs on the laptop (not the Mac Mini), polls the Mac Mini every 60s on a
fast path and 15 min on a full sweep, auto-recovers known failure modes per
an approved-action list, pages Tim once per condition.

Pattern 36 prevention layer: lives on a separate host so it does not share
the Mac Mini's failure mode. Pure shell + Python — **never** invokes any
LLM (LLM calls were the root cause of the cascade it's preventing).

## Verdicts

| State | Trigger | Action |
|---|---|---|
| GREEN | All checks pass | log only |
| AMBER | One soft threshold over | push once per 30 min, no auto-fix |
| RED-RECOVERABLE | load > 30, procs > 1000, /tmux-windows > 5s, /health down | auto-fix + push |
| RED-UNRECOVERABLE | SSH + HTTP both unreachable for 3 ticks | push manual-intervention alert |

## Approved auto-fix list

The watchdog **only** auto-fixes these. Everything else pages Tim.

- `probe:conversation-server` unreachable → `launchctl kickstart -k com.timtrailor.conversation-server`
- `memory:chromadb` slow → kickstart `com.timtrailor.memory-search`
- Mac Mini load > 30 (Pattern 36) → reap orphaned `claude --print`, hook smoke-test, tier3-reviewer, scan_command, protected_path_hook procs (PPID=1 only, age > 60s)
- Mac Mini procs > 1000 → same proc reap
- Last resort: `sudo /sbin/reboot` (requires passwordless sudoers entry — see Setup)

## Guard-rails (non-negotiable)

- **Single-instance lock** via flock on `~/.watchdog/watchdog.lock`
- **Action budget**: 1 reboot/hour, 3 kickstarts/hour, 4 proc-reaps/hour
- **Killswitch**: `~/.watchdog-disabled` on EITHER the laptop OR the Mac Mini pauses everything
- **Outbound rate limit**: 1 push per condition per 30 min
- **No LLM calls** — pure shell + Python by design

## Setup

```bash
# 1. Place watchdog.py
mkdir -p ~/code/watchdog ~/.watchdog
cp watchdog.py ~/code/watchdog/watchdog.py
chmod +x ~/code/watchdog/watchdog.py

# 2. Smoke test (one tick, full sweep, no daemon)
python3 ~/code/watchdog/watchdog.py --once --full

# 3. Install LaunchAgent
cp com.timtrailor.watchdog.plist ~/Library/LaunchAgents/
sed -i '' "s|__WATCHDOG_PATH__|$HOME/code/watchdog/watchdog.py|" \
    ~/Library/LaunchAgents/com.timtrailor.watchdog.plist
sed -i '' "s|__HOME__|$HOME|g" \
    ~/Library/LaunchAgents/com.timtrailor.watchdog.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.timtrailor.watchdog.plist

# 4. Optional — passwordless reboot (last-resort action)
# Edit /etc/sudoers via `sudo visudo` and add:
#   timtrailor ALL=(ALL) NOPASSWD: /sbin/reboot
# (Mac Mini sudoers, NOT laptop. Watchdog SSHes in to trigger reboot.)
```

## Operating

- Pause for 1 hour: `touch ~/.watchdog-disabled`. Resume: `rm ~/.watchdog-disabled`.
- Force tick now: `python3 ~/code/watchdog/watchdog.py --once`
- Full sweep now: `python3 ~/code/watchdog/watchdog.py --once --full`
- Last verdict: `cat ~/.watchdog/state.json | jq .last_verdict`
- Recent actions: `cat ~/.watchdog/state.json | jq '.actions[-10:]'`
- Live log: `tail -f ~/.watchdog/watchdog.log`

## What it does NOT do

- No LLM calls
- No printer-touching actions (printers always page Tim)
- No git/commit/push/merge actions
- No credential edits
- No auto-fixes outside the approved-list
- Does NOT replace `health_check.py` on the Mac Mini — that's the on-host monitor; this is the cross-host one
