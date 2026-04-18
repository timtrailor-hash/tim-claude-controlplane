---
name: rotate-token
description: Rotate a compromised or expired secret (GitHub PAT, Anthropic API key, SMTP password, etc.) end-to-end from Tim's phone with no desktop action. Updates macOS keychain on Mac Mini (and laptop when reachable), verifies the new value, logs the rotation event.
---

# Rotate Token — mobile-first secret rotation

Triggered when a token is expired, leaked, or scheduled for rotation. Designed so Tim can run it from `/autonomous`, a Claude tab, or an ad-hoc chat on his phone.

## Arguments
- `$1` (required) — secret name. One of: `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `SMTP_PASSWORD`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `APPLE_APP_STORE_CONNECT_API_KEY`.
- `$2` (optional) — new value. If omitted, the skill prints the URL where to create/retrieve a new one and waits for Tim to paste it in the next message.

## Flow
1. **Prompt Tim** — print the exact URL to open on his phone Safari to create/retrieve a new token (e.g. `https://github.com/settings/tokens` for GitHub, `https://console.anthropic.com/settings/keys` for Anthropic).
2. **Receive the value** — Tim pastes it into the chat. The skill never writes it to disk as plaintext, never echoes it back, and strips it from terminal scrollback.
3. **Write to Mac Mini keychain** — `security add-generic-password -a <NAME> -s tim-credentials -w '<VALUE>' -U` on Mac Mini via SSH. Uses `~/.keychain_pass` to unlock first.
4. **Propagate to laptop** — if laptop is reachable (Tailscale ping), rsync-free SSH: `security add-generic-password` on the laptop too. If not reachable, queue in `/tmp/pending-rotations.jsonl` and print a notice — the laptop picks it up on next session via a SessionStart hook (not implemented here; item for the rotation daemon).
5. **Verify** — call the service's `whoami`/`/user`/`/auth` endpoint with the new value. For GITHUB_TOKEN: `curl https://api.github.com/user` must return 200 AND `x-oauth-scopes` must include `repo, workflow`.
6. **Log** — append to `~/code/credential_rotations.jsonl`: `{"ts": ..., "name": "GITHUB_TOKEN", "rotated_from_host": "phone-via-macmini", "verified": true}`. Do NOT log the value.
7. **Revoke old** — if the token provider has an API for it (GitHub does via `DELETE /user/tokens/<id>`), revoke the previous token. For GitHub we can't because we don't track the old token's id; skip revocation and remind Tim to manually revoke in the UI if he rotated due to compromise.

## Scope boundaries
- Does NOT rotate Apple Developer signing certificates (separate `rotate-apple-cert` skill if that's ever needed — it needs keychain access + Xcode).
- Does NOT rotate SSH deploy keys (use `rotate-deploy-keys` — generates new keypairs, swaps them via `gh api`).
- Does NOT touch `.keychain_pass` itself — that's the root-of-trust and must be rotated out-of-band (physical access or recovery flow).

## Prerequisites
- `GITHUB_TOKEN` in keychain under `tim-credentials` for `gh` CLI calls.
- `~/.config/git/credential-keychain.sh` installed (git pushes after GITHUB_TOKEN rotation keep working without config change).
- `~/.keychain_pass` present (auto-unlock from SSH sessions).

## Example usage

```
/rotate-token GITHUB_TOKEN
```
→ skill prints: "Open https://github.com/settings/tokens on your phone, create a classic token with scopes `repo, workflow, read:org`, paste the value in your next message."

```
/rotate-token GITHUB_TOKEN ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```
→ inline form; skill writes, verifies, logs.

## Implementation
See `rotate_token.sh` in this skill directory. Calls into `security` + `curl` + `ssh`. No Python dependencies.
