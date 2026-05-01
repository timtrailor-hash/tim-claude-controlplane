---
name: secure-delete
description: "Securely wipe the current Claude session's artefacts — JSONL transcript, manifest-tracked files, tmp artefacts, shell snapshots, per-topic memory entries (prompted), then VACUUM ChromaDB+FTS5. Run manually at end of sensitive sessions."
user-invocable: true
disable-model-invocation: false
---

# /secure-delete — wipe session artefacts

Purpose: make THIS session's content unrecoverable after you're done. Defence against seizure of the Mac Mini (or its backups) revealing what was discussed here. Does NOT hide the fact that a session happened — OS-level timestamps and LaunchAgent logs remain. Content is gone.

## What gets wiped (no user confirmation for these)

1. **Current session JSONL** at `~/.claude/projects/<project>/<session-id>.jsonl`.
2. **Session manifest** at `/tmp/session-<session-id>-manifest.txt` (if present).
3. **/tmp artefacts** created this session (`/tmp/debate_*`, `/tmp/secure-delete-*`, `/tmp/*-<session-id>*`).
4. **Shell snapshots** for this session under `~/.claude/projects/<project>/shell-snapshots/`.
5. **Memory chunks** in ChromaDB and FTS5 tagged with this session's `conv_id`.
6. **VACUUM** with `PRAGMA secure_delete=ON` on the FTS5 DB. ChromaDB SQLite compacted.

## What prompts per-item (user chooses)

7. **Topic files** in the memory repo that were created or modified since session start — per-topic prompt: keep / delete / keep-but-don't-push.
8. **MEMORY.md index lines** referring to deleted topic files — removed automatically once the topic is confirmed for deletion.

## Invocation

```
/secure-delete            # interactive, prompts for topic choices
/secure-delete --yes-all  # delete every detected topic change; no prompt
/secure-delete --dry-run  # show what would be deleted, don't touch anything
```

## Claude's job when this skill runs

1. Run `python3 ~/code/tim-claude-controlplane/shared/skills/secure-delete/secure_delete.py --dry-run` and surface the plan to the user.
2. If user confirms (or passed `--yes-all`), run `python3 ~/code/tim-claude-controlplane/shared/skills/secure-delete/secure_delete.py` in interactive mode and relay its per-topic prompts to the user. Answer per topic as the user directs.
3. After completion, confirm the deletion report to the user and recommend they end the session immediately (don't continue writing — anything written now becomes the NEW session's residue).

## Hard rules

- NEVER run against a session that isn't the current one. The script refuses if `CLAUDE_SESSION_ID` or detected current-session JSONL can't be identified.
- NEVER deletes topic files without explicit confirmation (unless `--yes-all`).
- NEVER deletes MEMORY.md itself. Only removes index lines referencing files being deleted.
- NEVER deletes files outside the known-safe set (JSONL, tmp, manifest, snapshots, topic files the user approves).
- Outputs a deletion report to stdout. The report itself is NOT persisted — once you've read it, it's gone.

## Limits (known, documented)

- **Google Drive backup** may have already copied files. Separately run `backup_to_drive.py --prune-session <conv_id>` if you want to also scrub the Drive copies.
- **OS-level timestamps, LaunchAgent logs, Spotlight index metadata** persist. This skill makes content unrecoverable, not existence unrecoverable.
- **Memory git repo history** will show deletions if topic files were previously committed. For truly sensitive topic files, don't commit them in the first place — create + use + delete within one session so they never hit git.
