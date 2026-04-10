---
name: gemini
description: "Independent Gemini 2.5 Pro reviewer — challenges Claude's work with a second opinion from Google's model."
user-invocable: true
disable-model-invocation: false
---

# Gemini Independent Reviewer

You are invoking Google's Gemini 2.5 Pro as an independent reviewer to challenge your own work.

## When to Use
- After completing any significant implementation or configuration change
- When Tim asks for an independent review
- As part of any system audit
- When you want a second opinion on a design decision

## How It Works

1. **Collect context** — gather the relevant files, changes, or decisions to review
2. **Send to Gemini** — call the API with a review prompt
3. **Report findings** — present Gemini's assessment honestly, including disagreements

## Arguments

`$ARGUMENTS` is the review scope. Examples:
- `/gemini` — review the overall Claude Code configuration (runs system-audit.sh)
- `/gemini printer hook changes` — review recent printer safety hook changes
- `/gemini my last commit` — review the most recent git diff

## Instructions

### Step 1: Determine Scope

If `$ARGUMENTS` is empty or "system", run the full system audit:
```bash
bash ~/.claude/system-audit.sh --gemini-review
```
Present the results and stop.

If `$ARGUMENTS` specifies something else, collect the relevant context (read files, git diffs, etc.).

### Step 2: Build the Review Prompt

For custom reviews, construct a prompt for Gemini with:
- What was done and why
- The actual code/config/changes
- What you (Claude) think is correct
- Ask Gemini to challenge it — find flaws, contradictions, missing edge cases

### Step 3: Call Gemini 2.5 Pro API

```python
import json, urllib.request, os, sys

# Machine-aware credentials path: try Mac Mini layout first, then laptop
for cand in ("~/code", "~/Documents/Claude code"):
    p = os.path.expanduser(cand)
    if os.path.exists(os.path.join(p, "credentials.py")):
        sys.path.insert(0, p)
        break
from credentials import GEMINI_API_KEY

def ask_gemini(prompt, context=""):
    """Send a review request to Gemini 2.5 Pro. Falls back to Flash if Pro unavailable."""
    full_prompt = prompt + "\n\n" + context if context else prompt
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192}
    }

    for model in ["gemini-2.5-pro", "gemini-2.5-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read())
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                return f"[Reviewed by {model}]\n\n{text}"
        except urllib.error.HTTPError as e:
            if e.code == 429 and model == "gemini-2.5-pro":
                continue  # Try Flash
            return f"Gemini API error: {e.code} {e.reason}"
        except Exception as e:
            return f"Gemini API error: {e}"
    return "Both Gemini Pro and Flash unavailable"
```

### Step 4: Present Results

Show Gemini's full response. Then add your own assessment:
- Where you agree with Gemini
- Where you disagree and why
- What action items come out of it

## Dual-Review Protocol

When performing independent challenges, ALWAYS run BOTH:
1. **Claude subagent** — launch a fresh Claude agent (via Agent tool) with no conversation context to review the same scope
2. **Gemini** — call the API as above

Present both reviews side-by-side. This gives Tim two independent perspectives from different model families.

## Important Notes
- Gemini 2.5 Pro is the primary reviewer. Falls back to Flash if Pro quota is exceeded.
- Cost: ~$0.05-0.10 per review (small context)
- The Gemini API key is in `~/Documents/Claude code/credentials.py`
- Weekly automated reviews run via cron: `bash ~/.claude/weekly-audit.sh` (Mondays 9am)
