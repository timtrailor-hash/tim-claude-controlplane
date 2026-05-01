---
name: Credentials in macOS Keychain (work-laptop side)
description: How the work-laptop Claude reads secrets. macOS Keychain via `security` CLI. Service "tim-credentials" matches the personal-side scheme so shared resolvers work on either side. Work-only keys live here; personal keys never leak in.
type: project
scope: shared
---

The work-laptop Claude reads every secret from macOS Keychain via the `security` CLI. Service is `"tim-credentials"`, account is the key name. Plain-text secrets in `credentials.py` are not used on the work side; the work laptop has no `credentials.py` file by design.

Why: a leaked secret on the work laptop must be revocable without touching personal-side usage. Keychain entries are also the audit boundary — the work laptop has its own keychain, sees only work keys, and a corporate forensics tool can confirm scope cleanly.

## How to add a new secret

```bash
security add-generic-password \
  -a "WORK_<NAME>" \
  -s "tim-credentials" \
  -w "<paste-value>" \
  -A ~/Library/Keychains/login.keychain-db
```

Read back to verify (only the first 12 chars; never log the whole secret):
```bash
security find-generic-password -a "WORK_<NAME>" -s "tim-credentials" -w | head -c 12; echo
```

## Resolver pattern (work side)

Skills and tools resolve a secret in this order:

1. Environment variable (`WORK_<NAME>` or, for shared third-party keys, the unprefixed name).
2. macOS Keychain entry under service `tim-credentials`, account `WORK_<NAME>` first, then the unprefixed name as a fallback.
3. No third fallback. The work side never reads `credentials.py` from disk.

Skills like `chatgpt`, `gemini`, `debate`, `second-opinion` use this resolver. The shared shim is at `shared/skills/<name>/SKILL.md`.

## Work-only key inventory

| Account | Source dashboard | Used by |
|---|---|---|
| `WORK_OPENAI_API_KEY` | https://platform.openai.com/api-keys (set spending limit BEFORE adding credits) | work-laptop `/chatgpt`, `/debate`, `/review` |
| `WORK_GEMINI_API_KEY` | https://aistudio.google.com/apikey | work-laptop `/gemini`, `/debate`, `/review` |
| `WORK_GITHUB_TOKEN` | github.com personal access token (work account, scopes: repo, workflow, read:org) | work-laptop GitHub MCP, `gh` CLI |
| `WORK_PERPLEXITY_API_KEY` | https://perplexity.ai dashboard | work-laptop `perplexity` MCP |
| `WORK_FIRECRAWL_API_KEY` | https://firecrawl.dev | work-laptop `firecrawl` MCP |
| `BRIDGE_AGE_PRIVKEY_WORK` | generated locally; pubkey ships in claude-bridge repo | work-side bridge MCP wrapper (decrypts inbound responses) |

## Spending caps (mandatory before adding credits)

- OpenAI: set a hard monthly limit on console.openai.com BEFORE adding credit. See `shared/rules/security.md`.
- Gemini: per-key quota in AI Studio if available.
- Perplexity, Firecrawl: keep within free tier; if upgrading, set the per-month cap in the dashboard.

## Rotation

If a key is suspected leaked:
1. Revoke at the source dashboard.
2. Generate a new key.
3. Re-run `security add-generic-password` (it overwrites the existing entry).
4. No daemon restart needed; resolvers read on demand.

`BRIDGE_AGE_PRIVKEY_WORK` rotation is heavier: see `claude-bridge/docs/key-rotation.md`. Both ends must be cycled together.

## What this file is NOT

- Not the personal-side keychain inventory. Personal keys live in the personal-side `credentials-keychain.md` (scope: personal) and never appear here.
- Not a place to paste actual key values. Only key names, sources, and scopes.
- Not a list of MCP launchers. Work side does not use the controlplane's `shared/mcp-launchers/` (denied in `WORK_ALLOWLIST.yaml`); the work-side bootstrap inlines MCP installs.
