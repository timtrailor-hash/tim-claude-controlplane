---
name: architecture-plan-work
description: Shared architecture improvement phases and patterns relevant to work-side development
type: project
scope: shared
---
# Architecture Improvement Plan (Work-Relevant Subset)

## Completed Phases

### Phase 0: Foundation (DONE)
- Central shared_utils.py for safe CLI env, portable paths, canonical work dir.
- Incremental backup to cloud storage.
- Disaster recovery runbook.

### Phase 1: Eliminate Recurring Failures (DONE)
- File-copy sync replaced with symlinks (eliminates drift).
- Virtualenv for all daemon scripts (correct Python, isolated deps).
- Structured logging in all services (no more bare excepts).
- Auto-calibration for runtime parameters from historical data.

### Phase 3: Native iOS App (DONE)
- WebSocket endpoint for bidirectional streaming.
- SwiftUI app replaces web wrapper.
- Local notifications for background completion.

### Phase 4: Hardening (ONGOING)
- Atomic JSONL writes (flush+fsync for appends, temp+rename for overwrites).
- Health check daemon (20+ checks, weekly cron).
- Memory search auto-indexing (6h schedule).

## Active Architecture Decisions

### Session Resilience (Phase 5)
- Long-running tasks delegated to background runner (autonomous_runner.py).
- Task survives session disconnects.
- Email notification cascade on completion or failure.
- Retry with exponential backoff (up to 5 attempts).

### Auto-Alert Responder (Phase 7)
- Persistent health alerts spawn headless investigation.
- 5-layer RCA + proposal output.
- Phase 1 (live): proposal-only mode, manual apply required.
- Phase 2 (not enabled): narrow allowlist auto-apply with hard-deny on destructive commands.

## Risk Patterns

| Risk | Mitigation |
|------|------------|
| OAuth token expires | Auto-refresh + alert in logs |
| Daemon crashes | LaunchAgent KeepAlive auto-restarts |
| Session drops mid-task | Autonomous runner continues independently |
| Credential leak | env_for_claude_cli() structurally prevents it |

## Dependency Ordering

```
Foundation (shared_utils, backup, DR)
  -> Failure elimination (symlinks, venv, logging)
    -> Native app (WebSocket, SwiftUI)
      -> Hardening (atomic writes, health checks)
        -> Session resilience (background runner)
```

Each phase is independently useful. Later phases build on earlier ones but don't require them all.
