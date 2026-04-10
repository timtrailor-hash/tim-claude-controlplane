---
name: chatgpt
description: "Independent OpenAI GPT-5.4 reviewer — challenges Claude's work with a second opinion. Uses gpt-5.4-mini for small reviews (<200 changed lines or short context), gpt-5.4 full for larger / architectural reviews."
user-invocable: true
disable-model-invocation: false
---

# ChatGPT Independent Reviewer

You are invoking OpenAI's GPT-5.4 family as an independent reviewer to challenge Claude's own work.

## Model selection — auto

| Scope | Model | When |
|---|---|---|
| Small / focused | `gpt-5.4-mini` | <200 changed lines AND not sensitive |
| Big / architectural | `gpt-5.4` | ≥200 changed lines OR architectural decision |
| **Sensitive** | `gpt-5.4` | Any size — overrides line count when the change touches printer/credentials/hooks/CI/etc |
| Override | `+full` or `+mini` flag in `$ARGUMENTS` | Force a specific model |

**Sensitivity classifier**: Run `git diff --name-only HEAD | bash ~/.claude/hooks/sensitivity_check.sh` to check. If it outputs `tier=full reason=...`, force full tier and surface the reason. The classifier matches:
1. File paths: printer/Klipper/Moonraker/credentials.py/settings.json/hooks/agents/LaunchAgents/plists/mcp-launchers/.github/workflows/daemons/firmware
2. Diff content: dangerous gcode (FIRMWARE_RESTART, SAVE_CONFIG, G28, etc.), sudo, rm -rf, DROP TABLE, KeepAlive, launchctl, hardcoded keys
3. File count ≥10

Cost rough order of magnitude: gpt-5.4-mini ~$0.005 per review, gpt-5.4 ~$0.05. Tim's API key is in `credentials.py`.

## Arguments

`$ARGUMENTS` is the review scope. Examples:
- `/chatgpt my last commit` — review the most recent git diff with auto model
- `/chatgpt the printer hook changes` — review specific scope
- `/chatgpt +full this whole RCA` — force gpt-5.4 (full)
- `/chatgpt +mini this one-line fix` — force gpt-5.4-mini

## Steps

### 1. Determine scope and pick model

Parse `$ARGUMENTS` for `+full` / `+mini`. Then collect the relevant context (files, diffs, decisions). Count rough size (lines or chars). If no override:
- `<200` changed lines → `gpt-5.4-mini`
- `≥200` changed lines or scope says "architecture/RCA/design" → `gpt-5.4`

### 2. Build the prompt

```
You are an independent code reviewer challenging another AI's work.
Be skeptical. Find what it missed. Disagree where warranted.

CONTEXT:
<what the work is, why it exists>

THE WORK:
<the diff / code / decision>

CLAUDE'S POSITION:
<what Claude concluded or wants to do>

YOUR JOB:
1. List concrete issues Claude missed
2. Challenge any assumption that isn't well-justified
3. Propose a different approach if you see one
4. Rate Claude's verdict: AGREE / PARTIALLY AGREE / DISAGREE
```

### 3. Call the OpenAI API

```python
import json, urllib.request, sys, os

# Machine-aware credentials path
for cand in ("~/code", "~/Documents/Claude code"):
    p = os.path.expanduser(cand)
    if os.path.exists(os.path.join(p, "credentials.py")):
        sys.path.insert(0, p)
        break
from credentials import OPENAI_API_KEY

def ask_chatgpt(prompt, model="gpt-5.4-mini"):
    """Send a review request to GPT-5.4. Falls back to mini if full is unavailable."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an independent technical reviewer. Be skeptical, specific, and concise."},
            {"role": "user", "content": prompt}
        ],
        "max_completion_tokens": 4096,
    }

    fallback_chain = [model]
    if model == "gpt-5.4":
        fallback_chain.append("gpt-5.4-mini")  # Fall back to mini if full unavailable

    for m in fallback_chain:
        payload["model"] = m
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read())
                text = result["choices"][0]["message"]["content"]
                return f"[Reviewed by {m}]\n\n{text}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:500]
            if e.code in (404, 429) and m == "gpt-5.4":
                continue  # Try mini
            return f"OpenAI API error: {e.code} {e.reason}: {body}"
        except Exception as e:
            return f"OpenAI API error: {e}"
    return "All ChatGPT model fallbacks failed"
```

Write the prompt to `/tmp/chatgpt_prompt.txt` then run the Python with the right model passed in.

### 4. Present results

Show GPT's full response. Then add your own assessment:
- Where you agree
- Where you disagree and why
- Action items

## Important notes

- The `OPENAI_API_KEY` is in `credentials.py` (laptop: `~/Documents/Claude code/`, Mac Mini: `~/code/`). The Python loader is machine-aware.
- gpt-5.4-mini is ~10x cheaper than gpt-5.4 — use it for small/routine checks. Reserve full for high-stakes.
- For TRIPLE review (Claude + Gemini + ChatGPT), use `/review` (which calls both reviewer skills).
- Rate limits: gpt-5.4 has tier-1 limits — if you hit a 429, the script falls back to mini automatically.
