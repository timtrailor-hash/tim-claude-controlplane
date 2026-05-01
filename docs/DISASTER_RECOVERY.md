# Disaster Recovery Runbook

Last updated: 2026-04-19. Maintained in `~/code/DISASTER_RECOVERY.md` on the
Mac Mini; backed up daily to Google Drive by `backup_to_drive.py` under
"Claude Code Backups".

---

## 1. System at a glance

| Layer | Where (live) | Source of truth |
|-------|--------------|-----------------|
| Mac Mini daemon plists | `~/Library/LaunchAgents/*.plist` | `tim-claude-controlplane/machines/mac-mini/launchagents/` |
| Mac Mini daemon scripts | `~/code/<x>.py` symlinks | `tim-claude-controlplane/machines/mac-mini/daemons/` (since 2026-05-01) |
| Service manifest | `machines/mac-mini/services.yaml` | tim-claude-controlplane repo |
| Host policy | `hosts/mac-mini.yaml` | tim-claude-controlplane repo |
| Hooks / agents / skills / rules | `~/.claude/{hooks,agents,skills,rules}/` | tim-claude-controlplane repo (symlinks installed by `deploy.sh`) |
| Memory tree | `~/.claude/projects/-Users-timtrailor-code/memory/` | `tim-memory` repo (private GitHub) |
| Mobile conversation server | `~/code/claude-mobile/` | `claude-mobile` repo |
| Printer tools | `~/code/sv08-print-tools/` | `sv08-print-tools` repo |
| Governors app | `~/code/ofsted-agent/` | `castle-ofsted-agent` repo |
| iOS app source | `~/code/{TerminalApp,ClaudeControl,GovernorsApp,PrinterPilot,TimSharedKit}/` | Matching GitHub repos |
| School docs | `~/Desktop/school docs/` | **GovernorHub** (re-downloaded weekly by `governorhub_sync.py`) |
| Secrets (runtime) | macOS login keychain | Seeded at rebuild from printed backup codes (see §3.1) |
| Secrets (file) | `~/code/credentials.py` (chmod 600) | Google Drive backup set (Drive access itself gated by Google 2FA) |
| Backup history | Google Drive "Claude Code Backups" | `backup_to_drive.py` manifest |

### Active LaunchAgents (Mac Mini, 2026-04-19)

`conversation-server`, `governors`, `backup-to-drive`, `printer-snapshots`,
`token-refresh`, `governorhub-sync`, `unlock-keychain`, `health-check`,
`streamlit-https`, `ttyd-tunnel`, `bgt-date-monitor`, `ci-failure-poller`,
`acceptance-tests`, `trend-tracker`, `credential-rotation`.
(Plus `memory-indexer` and `system-monitor` declared in `services.yaml` but not
always loaded — `launchctl list | grep timtrailor` is the ground truth.)

Declared in `machines/mac-mini/services.yaml`. If the list there and the
`launchctl list` output drift, `verify.sh` fails.

### Daemon scripts are now symlinks (2026-05-01)

The 10 daemon scripts in `~/code/` (`acceptance_tests.py`, `backup_to_drive.py`,
`bgt_date_monitor.py`, `ci_failure_poller.py`, `credential_rotation.py`,
`health_check.py`, `stale_pr_alert.py`, `token_refresh.py`,
`trend_tracker.py`, `ttyd_tunnel.sh`) are SYMLINKS into
`tim-claude-controlplane/machines/mac-mini/daemons/`. The deployed path
stayed at `~/code/<x>.py` so plists, `services.yaml`, and cross-script
imports keep working without edits.

**Load-bearing consequence**: `tim-claude-controlplane` is now a runtime
dependency. If the controlplane checkout is deleted, moved, or has its
`machines/mac-mini/daemons/` directory removed, every migrated daemon
breaks at the next scheduled run. Recovery: `git clone
git@github.com:timtrailor-hash/tim-claude-controlplane ~/code/tim-claude-controlplane`
followed by `bash deploy.sh` re-creates the symlinks from the canonical
copies.

`deploy.sh` snapshots the original files to `/tmp/deploy_snapshot_<ts>/daemons/`
before symlinkifying, so a recent deploy can be rolled back by hand if
needed: `cp -RP /tmp/deploy_snapshot_<ts>/daemons/<x> ~/code/<x>`.

---

## 2. Backup posture

### 2.1 What is backed up to Google Drive
Managed by `~/code/backup_to_drive.py`, LaunchAgent
`com.timtrailor.backup-to-drive`, fires 03:00 daily, differential by mtime+md5.
Manifest at `~/code/.backup_manifest.json`. Log at `/tmp/backup_to_drive.log`.

Drive folder `Claude Code Backups` (owner: timtrailor@gmail.com, not shared).

Backup sets (glob-based — new files are captured automatically):

| Set | Root | What |
|-----|------|------|
| `code` | `~/code/` | All top-level `.py` / `.sh` / `.yaml` / `.json` / `.md` / `.toml` / `.p8`, plus `prompts/`, `reminders/`, `certs/`, `sv08_tools/`, `memory_server/`, and local-only config in `claude-mobile/` + `ofsted-agent/` (OAuth tokens, encrypted context, Streamlit secrets). |
| `controlplane` | `~/code/tim-claude-controlplane/` | Full repo contents (`**/*.{py,sh,yaml,md,plist,json,toml}`). Redundant with GitHub — present on Drive so a simultaneous GitHub-account + Mac Mini loss is still recoverable. |
| `daemon` | `~/.local/lib/` and `~/.local/bin/` | Symlink-deployed daemon scripts. |
| `claude-config` | `~/.claude/` | `settings.json`, `keybindings.json`. |
| `launchd` | `~/Library/LaunchAgents/` | All `com.timtrailor.*.plist` (glob — new plists picked up automatically). |
| `memory-topics` | `~/.claude/projects/-Users-timtrailor-Documents-Claude-code/memory/` | Topic files for the laptop-view project. **The Mac Mini project's memory (`-Users-timtrailor-code`) is a git repo (`tim-memory`) and is backed up independently via `git push`.** |

### 2.2 Intentional exclusions
These are listed here so future-you doesn't think something is missing.

- **School docs (`~/Desktop/school docs/`)** — **GovernorHub is the source of truth.** `governorhub-sync` LaunchAgent (weekly, Monday 06:00) re-downloads the full set. Backing them up to Drive was 94% of the byte volume and added no recovery capability, so they were removed from the Drive set on 2026-04-19. See §3.8 for the recovery path.
- **iOS app source trees** — `TerminalApp/`, `ClaudeControl/`, `GovernorsApp/`, `PrinterPilot/`, `TimSharedKit/`. All in GitHub. Recovery is `git clone`. The Xcode build artefacts (`DerivedData/`, `.build/`) are regenerable and excluded.
- **`memory_server_data/` (ChromaDB, ~500 MB)** — derivable from JSONL transcripts via `rebuild_index.sh`.
- **JSONL session transcripts** (`~/.claude/projects/*/*.jsonl`) — conversation history. Not backed up today. Memory facts survive via the `tim-memory` git repo (topic files); conversation-search history would be lost on disk failure. Accepted gap.
- **iOS app signing certs** — re-downloaded from developer.apple.com.
- **Time-series monitoring** — Grafana/Prometheus not deployed.
- **Printer camera footage** — real-time only.
- **`ngrok` / `cloudflared` tunnel tokens** — removed 2026-03 in favour of Tailscale.

### 2.3 Security model for Google Drive backup
- **Account**: timtrailor@gmail.com, 2FA enabled.
- **Account recovery**: via Tim's wife's email or Tim's phone. Both under Tim's control and outside the Drive trust boundary. Treated as sufficient to defend the backup.
- **At-rest encryption**: Google-managed keys. Nothing is encrypted with a user-held key before upload. `credentials.py`, OAuth tokens, and the TLS private key (`certs/streamlit.key`) sit in Drive as plaintext — protected only by the Google account. **Accepted risk** given the 2FA + phone/wife recovery path.
- **Sharing**: folder is owned by timtrailor@gmail.com, not shared with anyone. Enforced by the backup script (it doesn't create any sharing grants).
- **Drive storage quota (2026-04-19)**: 15 GB total, 13.1 GB used across the whole account. After removing school_docs from the backup set and pruning Drive trash, Drive headroom is ~2 GB. Re-check quarterly.

### 2.4 Known backup quirks
- OAuth client secret `google_credentials.json` is uploaded as plaintext; a compromised Drive = compromised Drive token. Mitigated by Google 2FA.
- `apns_key.p8` (APNs signing key) is on Drive plaintext. chmod 600 locally (enforced 2026-04-19).
- The legacy "Disaster Recovery Runbook" Google Doc (DocId `1atwWjCdFdbrB3Ms6ma1PBBfD_LF4WFngDQwE1swr_Oo`) is **no longer auto-updated**. Treat this markdown file as the source of truth. The Google Doc is kept for historical continuity only.

---

## 3. Blast radius by failure class

### 3.1 Mac Mini disk loss — full rebuild (RTO ~1h)
1. Reinstall macOS + Homebrew + Xcode CLT.
2. Install Tailscale and re-join the tailnet. LAN IP: `192.168.0.172`.
   Tailscale IP: `100.126.253.40`.
3. Generate a new SSH keypair; add the public key to `timtrailor-hash`
   GitHub account (enables clone of private repos).
4. Clone the control plane first (it's the install orchestrator):
   `git clone https://github.com/timtrailor-hash/tim-claude-controlplane.git ~/code/tim-claude-controlplane`
5. Clone the application repos:
   - `git clone https://github.com/timtrailor-hash/claude-mobile.git ~/code/claude-mobile`
   - `git clone https://github.com/timtrailor-hash/sv08-print-tools.git ~/code/sv08-print-tools`
   - `git clone https://github.com/timtrailor-hash/castle-ofsted-agent.git ~/code/ofsted-agent`
   - `git clone https://github.com/timtrailor-hash/TerminalApp.git ~/code/TerminalApp`
   - `git clone https://github.com/timtrailor-hash/ClaudeControl.git ~/code/ClaudeControl`
   - `git clone https://github.com/timtrailor-hash/GovernorsApp.git ~/code/GovernorsApp`
   - `git clone https://github.com/timtrailor-hash/PrinterPilot.git ~/code/PrinterPilot`
   - `git clone https://github.com/timtrailor-hash/TimSharedKit.git ~/code/TimSharedKit`
6. Restore memory (tim-memory is a private repo; the repo URL is in
   `~/code/tim-claude-controlplane/shared/mcp-launchers/` configs):
   `git clone git@github-memory:timtrailor-hash/tim-memory.git ~/.claude/projects/-Users-timtrailor-code/memory`
7. Restore non-source artefacts from Google Drive (`backup_to_drive.py --restore`):
   - `~/code/credentials.py`
   - `~/code/claude-mobile/google_token.json` + `google_credentials.json`
   - `~/code/claude-mobile/slack_config.json`, `mcp-approval.json`
   - `~/code/ofsted-agent/combined_context.md.enc` + `gdrive_token.json` + `.streamlit/secrets.toml`
   - `~/code/apns_key.p8`
   - `~/code/certs/streamlit.{crt,key}`
8. Seed macOS Keychain:
   - `security add-generic-password -a timtrailor -s ANTHROPIC_API_KEY -w <key>`
   - `security add-generic-password -a timtrailor -s ttyd-auth -w <pass>`
   - `security add-generic-password -a timtrailor -s GEMINI_API_KEY -w <key>`
   - Other entries per `machines/mac-mini/services.yaml` `secrets:` blocks.
   Keychain entries are NOT in the Drive backup — they must be seeded from
   printed backup codes (kept offline) or regenerated from the upstream
   services.
9. Seed `~/.keychain_pass` with the login keychain password (required by
   `unlock-keychain.sh` to run headless at boot). This is intentional and
   documented in `services.yaml` — chmod 600, accepted risk for a headless
   Mac Mini with physical security.
10. Re-download school docs:
    `python3 ~/code/ofsted-agent/governorhub_sync.py` (see §3.8).
11. From the control plane repo: `./deploy.sh` (atomic: installs plists,
    symlinks hooks/rules/agents/skills into `~/.claude/`, runs `./verify.sh`,
    auto-rolls-back via `./rollback.sh` on failure).
12. Reboot. Confirm:
    - `bash ~/code/tim-claude-controlplane/verify.sh` — expect `14 passed, 0 failed` on hook checks and pytest scenarios green.
    - `python3 ~/code/health_check.py --once` — expect overall status OK.
    - `python3 ~/code/acceptance_tests.py` — expect ≥90% compliance (today: 100%).

### 3.2 Google account compromise / lockout
- Recovery path: **Tim's wife's email and Tim's phone** are both configured as
  account recovery channels. Either one restores access.
- Phone is under Tim's daily control; wife's email is an out-of-band channel
  that survives even loss of the phone.
- **Accepted risk statement**: because those two recovery channels are sound,
  plaintext credentials on Drive are treated as acceptable. If either channel
  is lost, revisit at-rest encryption for the Drive backup.
- On recovery: rotate `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, APNs key,
  `google_credentials.json`, `ttyd-auth`, and SMTP app password. All OAuth
  tokens auto-rotate via `token_refresh.py`.

### 3.3 GitHub account compromise
- Primary code locations hold: Mac Mini (`~/code/`) + Drive (`controlplane`,
  `claude-mobile` configs/tokens) + laptop (partial mirror).
- Recovery: create new GitHub account, add SSH key, `git push` from Mac Mini
  to re-populate repos.
- Secrets to rotate: none directly — GitHub tokens live in keychain, not in
  the repo.

### 3.4 Google OAuth token loss (not full account loss)
Backup and governors both use the same token at
`~/code/claude-mobile/google_token.json`, refreshed by `token_refresh.py`
(LaunchAgent `com.timtrailor.token-refresh`).
1. If expired: run `python3 ~/code/claude-mobile/google_auth_setup.py` in a
   browser session and re-authorise all scopes (drive, gmail, calendar,
   sheets, docs).
2. Ensure `credentials.py → GOOGLE_OAUTH_SCOPES` matches what
   `google_auth_setup.py` requests — mismatched scopes cause silent
   downgrade (lessons.md Pattern 9).

### 3.5 Printer in unsafe state
If a destructive command was sent mid-print:
1. **Do not** send `FIRMWARE_RESTART` or `RESTART` — those are the actions
   that historically killed multi-hour prints.
2. Check `print_stats.state` via Moonraker:
   `curl -s http://192.168.0.108:7125/printer/objects/query?print_stats`
3. If state is `paused` or `printing`, the Klipper macro-level
   `SAVE_CONFIG` guard will block config rewrites. Manually
   `CANCEL_PRINT_CONFIRMED` only on Tim's explicit request.
4. The `~/.claude/hooks/printer-safety-check.sh` PreToolUse hook denies any
   command not on the allowlist while state is `printing` or `paused` —
   don't bypass it, investigate why a command was blocked.

### 3.6 Memory loss or corruption
Memory is a git repo (`tim-memory`, remote
`git@github-memory:timtrailor-hash/tim-memory.git`). Recovery:
1. Delete `~/.claude/projects/-Users-timtrailor-code/memory/chroma_db/`.
2. `git reset --hard origin/main` in the memory tree.
3. From the Mac Mini: `bash ~/code/rebuild_index.sh` — re-derives ChromaDB +
   FTS5 from the canonical JSONL transcripts (if transcripts intact) or from
   the topic files alone (degraded — loses conversation-search, preserves
   facts).

### 3.7 Conversation-server outage
Mobile iOS apps (`TerminalApp`, `ClaudeControl`) and the claude-mobile web
wrapper depend on this. The daemon auto-restarts under `KeepAlive`. If it
fails to come back:
1. `tail /tmp/conversation_server.stderr.log` for the Python traceback.
2. Common causes: stale Google token (see §3.4), missing APNs key
   (`~/code/apns_key.p8`), or port 8081 already bound
   (`lsof -iTCP:8081 -sTCP:LISTEN`).

### 3.8 School docs loss — GovernorHub recovery
School docs (`~/Desktop/school docs/`) are deliberately **not** in the
Google Drive backup set. **GovernorHub is the source of truth.** The weekly
LaunchAgent `com.timtrailor.governorhub-sync` re-downloads them.

If the local copy is wiped:
1. Verify GovernorHub credentials are live:
   `security find-generic-password -a timtrailor -s GOVERNORHUB_SESSION -w`
   (stored in keychain; seeded from a fresh login if missing).
2. Run on-demand: `python3 ~/code/ofsted-agent/governorhub_sync.py`.
3. This re-populates `~/Desktop/school docs/` and rebuilds the combined
   context used by the governors app (`combined_context.md.enc`).
4. Expected wall-clock: ~5–10 minutes for the full set.

### 3.9 UPS / power event
> Full details: `memory/topics/ups-power-protection.md`.
- CyberPower CP1600EPFCLCD UPS, USB HID → Mac Mini.
- `pmset -u`: macOS auto-shuts-down at 15% battery or 5 min remaining.
- Klipper PLR (`plr_autosave.py`) is the only print-side defence — saves
  position every 60 s. Recovery via `POWER_RESUME`.
- **No `ups_watchdog` daemon.** It was permanently deleted 2026-03-12 after
  causing more print failures than real power cuts. Do not recreate.

---

## 4. Emergency access

- **SSH from laptop**: `ssh timtrailor@100.126.253.40` (Tailscale) or
  `ssh timtrailor@192.168.0.172` (LAN).
- **SSH from phone**: Mosh or Tailscale SSH via the iOS Tailscale app.
- **Browser terminal**: `https://100.126.253.40:7681` (ttyd with basic-auth
  `tim:<ttyd-auth keychain entry>`).
- **Emergency printer pause**: from any device on Tailscale:
  `curl -X POST http://192.168.0.108:7125/printer/print/pause`.

---

## 5. Verifying a recovery

Ordered smoke tests — each must PASS before declaring recovery complete:

1. `bash ~/code/tim-claude-controlplane/verify.sh` — expect hook checks green
   and pytest scenarios green (count grows over time; trust the "0 failed"
   line, not a fixed pass count).
2. `python3 ~/code/health_check.py --once` — expect overall status OK and
   every individual check green.
3. `python3 ~/code/acceptance_tests.py` — expect ≥90% compliance. Current
   baseline: 41/41 = 100%.
4. From iPhone: open ClaudeCode / TerminalApp → home tab shows printer
   status and all health checks green.
5. Send a test print job (small calibration cube) to SV08 Max — arrives
   without needing a firmware restart.
6. `python3 ~/code/backup_to_drive.py --dry-run` — expect a short upload
   list (only the files modified since last real run).

---

## 6. Deploy & rollback

The control plane uses **atomic deploys with auto-rollback**. Do not edit
`~/.claude/hooks/`, `~/Library/LaunchAgents/com.timtrailor.*.plist`, or the
deployed files directly — change them in `~/code/tim-claude-controlplane/`
and run `./deploy.sh`.

- `./deploy.sh` — snapshots current state, installs new state, runs
  `./verify.sh`, and if verify fails, invokes `./rollback.sh` automatically.
- `./rollback.sh` — manually restore the last-known-good snapshot.
- `./diff-live.sh` — show what the deployed state differs from the repo
  (drift detection; runs as part of `verify.sh`).
- `~/code/tim-claude-controlplane/.conv_server_baseline` — the current
  approved baseline line count for `conversation_server.py`. Monolith size
  ratchet fails if the file grows without a baseline bump.

---

## 7. Last-resort single-machine fallback

If the Mac Mini is gone and there is no time to rebuild:
1. The laptop has a partial mirror of `~/code/` and can run
   `memory_server.py` + `health_check.py` locally. It is the "thin client"
   by convention but is functionally capable.
2. iOS apps point at the Mac Mini's Tailscale IP (`100.126.253.40`).
   Editing each app's `Secrets.swift` / config to the laptop's Tailscale IP
   (`100.112.125.42`) shifts the conversation server to the laptop for a
   transition period.
3. Printer tooling is independent of the Mac Mini — it queries Moonraker
   on `192.168.0.108` directly. Any machine on the LAN/Tailnet can drive it.
4. GovernorHub sync can run from any machine with the GovernorHub session
   cookie and Python 3.11+.
