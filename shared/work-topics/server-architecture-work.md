---
name: server-architecture-work
description: Server-side architecture patterns for work-side code
type: project
scope: shared
---
# Server-Side Architecture Patterns

## Design Principles

### Daemon Architecture
- Services run as LaunchAgents (KeepAlive) for automatic restart on crash.
- Each daemon has: a plist, a shell wrapper (sets PYTHONPATH, env), and a Python entry point.
- Adaptive polling: fast cycle when active (30s), slow cycle when idle (5min).
- Graceful SIGTERM handling and error backoff on every daemon.

### Shared Utilities
- shared_utils.py provides safe env for Claude CLI, portable paths, canonical working directory.
- NEVER build env for Claude CLI subprocess manually. Always use the shared utility.

### Credential Handling
- Credentials in gitignored files or macOS Keychain.
- Claude CLI env strips direct keys to force subscription auth.
- Public code uses [REDACTED] placeholders.

### Auto-Start on Reboot
- LaunchAgents (KeepAlive) handle all persistent services.
- PYTHONPATH set in each daemon script, not globally.

## Key Patterns

### Atomic Writes
- JSONL appends: flush + fsync.
- JSON overwrites: write to temp file, then atomic rename.
- Prevents corruption on crash or power loss.

### Notification Cascade
- Email then ntfy then Slack DM then file fallback.
- Each channel retries independently.
- On work side, email routes via bridge outbox instead of direct SMTP.

### Health Checking
- Test the actual feature, not just process liveness.
- Never trust "process is running" as health signal.

### Disaster Recovery
- Daily automated backup to cloud storage.
- Recovery runbook maintained as living document.

## Daemon Management
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
launchctl list | grep <label>
```

### Python Runtime
- Use Homebrew Python for all services.
- macOS TCC blocks access to ~/Documents/ from non-GUI contexts.
- Symlinks do NOT bypass TCC.
