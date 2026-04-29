# Security Rules

## Code & Repos
- NEVER put private info in code, commits, or public repos
- Memory files are private (fine for secrets). Code files may be pushed public.
- All printer secrets live in `credentials.py` (gitignored). Public code uses `[REDACTED]` placeholders.

## Public GitHub Repos
- `timtrailor-hash/sv08-print-tools` — speed tools ONLY
- `timtrailor-hash/claude-mobile` — conversation server (credentials.py gitignored)
- `timtrailor-hash/castle-ofsted-agent` — governors Streamlit app
- `timtrailor-hash/TerminalApp` — native SSH terminal iOS app (NO hardcoded IPs or secrets)
- `timtrailor-hash/GovernorsApp` — governors iOS app (NO hardcoded IPs or secrets)
- `timtrailor-hash/ClaudeCode` — DEAD as of 2026-04-11. Repo retained for archive. Hard rule: do not push or update. See feedback_claudecode_deprecated.md.

## API Key Rules
- Use `shared_utils.env_for_claude_cli()` when spawning Claude CLI — strips API key, forces subscription auth
- Use `shared_utils.get_api_key()` for direct API calls (Haiku, ofsted-agent)
- NEVER manually build env for Claude CLI — always use the shared utility
- Only Haiku/Sonnet/Gemini Flash should use API credits — never Opus via API
- Always set a spending limit on console.anthropic.com before adding credits

## Auth Helper Scripts — Token Backup Location
- Any ad-hoc script that rewrites an OAuth token file (scope expansion,
  re-auth, rescope, etc.) MUST write its pre-change backup under `/tmp`,
  never next to the live token file inside a project folder.
- Rationale: Claude-written helper at 2026-04-21 22:00 BST wrote
  `google_token.json.pre-rescope` into `~/code/claude-mobile/`. The
  backup contained a live refresh token and was untracked for ~12h
  before being caught. It had not been added to the ignore list because
  the suffix was new.
- Pattern to use in new helpers:
  ```python
  from pathlib import Path
  backup = Path("/tmp") / f"{TOKEN_PATH.name}.pre-rescope.{int(time.time())}"
  backup.write_bytes(TOKEN_PATH.read_bytes())
  ```
- Claude-mobile's ignore list also uses a broadened pattern (`google_token*`,
  `google_credentials*`) so any forgotten backup under those prefixes is
  not uploaded even if this rule is violated. That is defence-in-depth,
  not an excuse to bypass the `/tmp` rule.
