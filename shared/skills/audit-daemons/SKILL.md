---
name: audit-daemons
description: "Audit every LaunchAgent on the Mac Mini — verify referenced paths exist, the script runs, the service is healthy, and the kill switch is documented. Catches Pattern 3 (silent failures) before they bite."
user-invocable: true
disable-model-invocation: false
---

# /audit-daemons — silent-failure hunt

Pattern 3 in lessons.md: silent failures unnoticed for weeks (OAuth tokens, broken backups, daemon paths pointing nowhere). This skill is the periodic check that surfaces them.

## Steps

### 1. Inventory

SSH to Mac Mini and list every LaunchAgent + every running daemon:

```bash
ssh timtrailor@192.168.0.172 'ls -la ~/Library/LaunchAgents/com.timtrailor.*.plist 2>/dev/null'
ssh timtrailor@192.168.0.172 'launchctl list | grep timtrailor'
ssh timtrailor@192.168.0.172 'ps -ef | grep -E "(daemon|monitor|server)" | grep -v grep'
```

Build a table: name, plist path, script path, PID (if running), exit status.

### 2. Path existence check

For each LaunchAgent, parse the plist and check that:
- The `Program` or `ProgramArguments[0]` path exists
- Any working directory exists
- Any referenced log file is writeable

Use:
```bash
ssh timtrailor@192.168.0.172 'plutil -convert json -o - ~/Library/LaunchAgents/<name>.plist'
```

Flag any path that doesn't exist. This is the most common silent failure.

### 3. Functional health check

For each daemon, don't just check that the process is running — check that it WORKS. Examples:
- conversation_server.py → curl the WebSocket endpoint
- printer_snapshot_daemon.sh → check for a recent snapshot file (`find /tmp/printer_status -mmin -10`)
- backup_to_drive.py → check Drive API responds + last backup timestamp
- memory indexer → query the SQLite FTS5 DB and check row count vs file count
- health_check.py → check the last alert email timestamp

If you can't verify functionality, say so explicitly — never assume "process running" = "feature working".

### 4. Kill switch check

For each daemon, document how to stop it:
- LaunchAgent: `launchctl unload ~/Library/LaunchAgents/<name>.plist`
- pkill: only if NOT KeepAlive
- Custom: documented in topic file?

If a daemon has `KeepAlive: true` and no documented stop procedure, flag it (lessons.md Pattern 1).

### 5. Cross-reference with documentation

Read `~/.claude/projects/-Users-timtrailor-Documents-Claude-code/memory/topics/server-architecture.md` and compare. Every running daemon should be documented; every documented daemon should be running.

### 6. Report

```
=== Daemon Audit ===
Date: <date>

Inventory: <N> LaunchAgents, <N> daemons running

HEALTHY:
- name → ✓ path exists, ✓ functional, kill switch: <command>

BROKEN:
- name → ✗ <reason>
  Fix: <action>

UNDOCUMENTED:
- <running but not in server-architecture.md>

ORPHANED (in docs but not running):
- <documented but not running>

Action items:
1. ...
```

If autonomous mode: send the report by email to timtrailor@gmail.com.
