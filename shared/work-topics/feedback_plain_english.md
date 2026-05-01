---
name: Plain-English explanations for non-technical choices
description: When asking Tim to make a decision that touches code/infra he doesn't live in daily, use plain-English glossary + short summary of what each change actually is + outcome-based choices (not technical ones). Confirmed working 2026-04-22.
type: feedback
scope: shared
---
When Tim has to decide something that touches code, git, or infrastructure, default to this shape:

1. **One-line glossary** of any jargon the decision requires (e.g. "commit = press Save and label what changed; push = upload that save to GitHub"). Keep to one sentence per term.
2. **Very short summary** per item — what the change actually is in plain English, not the filenames or diff stats. Tell him whether it looks like real work or an experiment.
3. **Outcome-based choices** — "keep it / throw it away / set aside" — not "commit / stash / reset --hard".
4. **A one-line recommendation** + "type yes to proceed".

**Why:** Tim confirmed on 2026-04-22 with "Much better explanations!" after Claude rewrote a decision prompt as "worth keeping" summaries with a glossary, instead of showing diffs and git terminology. Before that, the dashboard wording ("diverged", "uncommitted") and Claude's git-native framing were blocking him from deciding.

**How to apply:** Any time the dashboard, monitor, or diagnostic output is feeding a decision to Tim, translate before presenting. If a term appears in a choice he has to make, glossary it inline. Don't assume he's read the diff; tell him what it is.

**Specific banned/translated vocabulary (Tim called this out a second time on 2026-04-22):**

| Instead of | Write |
|---|---|
| commit | save / saved change |
| push | upload to GitHub / sent to GitHub |
| pull | download from GitHub / pulled from GitHub |
| fetch | check GitHub for updates |
| gitignore / ignore pattern | ignore list / files we don't track |
| glob | pattern that matches lots of filenames |
| diff | what changed |
| branch / HEAD / upstream | avoid — say "the shared version" / "your version" |
| repo | project folder |
| stash | set aside |
| merge / rebase | combine / combine with history rewrite |
| ratchet / baseline | size limit / threshold |
| env var | setting |
| PR / pull request | proposed change on GitHub |
| OAuth | login with Google/GitHub |
| refresh token | long-lasting login key |
| scope (OAuth) | permission the login is allowed to do |

Before sending any reply, scan for these terms and rewrite. This applies to BODY text and explanations. It is OK to use technical vocabulary in code blocks, file paths, and direct command output that Tim is expected to run — those are quoting, not speaking.
