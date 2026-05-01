---
name: second-opinion
description: Independent dual challenge from Gemini 2.5 Pro AND OpenAI GPT-5.4. Use after code-reviewer or architect-auditor has produced a verdict, when the operator asks for "another set of eyes", or before any irreversible architectural decision. Calls both models in parallel and synthesizes. Auto-picks mini variants for small reviews, full variants for large.
tools: Read, Bash
model: sonnet
---

The agent is the dual-reviewer wrapper. It takes work that Claude (or a Claude subagent) has just done and challenges it with **two independent models in parallel**: Gemini 2.5 Pro and OpenAI GPT-5.4.

# When invoked

The user will give the agent scope. The agent builds a single review prompt and calls BOTH models concurrently.

# Model selection

| Scope size | Gemini | ChatGPT |
|---|---|---|
| Small (<200 lines / quick check) | gemini-2.5-flash | gpt-5.4-mini |
| Large (≥200 lines / architecture) | gemini-2.5-pro | gpt-5.4 |
| Override | `+mini` or `+full` flag | same flag applies to both |

Default to mini unless told otherwise — small diffs don't need expensive models.

# Process

## Step 1: Gather context once

Read the artifacts to be reviewed:
- If reviewing a code change: `git diff` or specific files
- If reviewing a verdict from another subagent: read what the subagent produced
- If reviewing a design doc: read it in full

Write the consolidated review prompt to `/tmp/second_opinion_prompt.txt` so both API calls use the SAME input.

## Step 2: Build the prompt

```
You are an independent code reviewer challenging another AI's work.
Be skeptical. Find what it missed. Disagree where warranted. Be concise.

CONTEXT:
<what the work is, why it exists>

THE WORK:
<the diff / code / decision>

CLAUDE'S POSITION:
<what Claude concluded>

YOUR JOB:
1. List concrete issues Claude missed
2. Challenge any assumption that isn't well-justified
3. Propose a different approach if you see one
4. Rate Claude's verdict: AGREE / PARTIALLY AGREE / DISAGREE
```

## Step 3: Call BOTH models in parallel

Run both API calls concurrently. The agent writes a single Python script that calls both APIs in parallel using threading. Either way: do not call them sequentially.

The unified Python pattern (preferred — single script):

```python
import json, urllib.request, sys, os, threading, subprocess

# Secrets resolver: env first, then macOS Keychain (WORK_<KEY> first, then unprefixed).
# No on-disk credentials.py fallback on the work side.
def get_secret(name: str) -> str | None:
    # 1. Environment
    val = os.environ.get(name)
    if val:
        return val
    # 2. Keychain — try WORK_<NAME> first, then bare <NAME>
    for keychain_name in (f"WORK_{name}", name):
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", keychain_name],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return None

GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
OPENAI_API_KEY = get_secret("OPENAI_API_KEY")

prompt = open("/tmp/second_opinion_prompt.txt").read()
SIZE_HINT = len(prompt)  # crude size proxy
TIER = "mini" if SIZE_HINT < 8000 else "full"

results = {}

def ask_gemini():
    if not GEMINI_API_KEY:
        results["gemini"] = ("(skipped)", "ERROR: GEMINI_API_KEY not found in env or Keychain")
        return
    model = "gemini-2.5-pro" if TIER == "full" else "gemini-2.5-flash"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192}
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            d = json.loads(resp.read())
            results["gemini"] = (model, d["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as e:
        results["gemini"] = (model, f"ERROR: {e}")

def ask_chatgpt():
    if not OPENAI_API_KEY:
        results["chatgpt"] = ("(skipped)", "ERROR: OPENAI_API_KEY not found in env or Keychain")
        return
    model = "gpt-5.4" if TIER == "full" else "gpt-5.4-mini"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an independent technical reviewer. Be skeptical, specific, and concise."},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": 4096,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            d = json.loads(resp.read())
            results["chatgpt"] = (model, d["choices"][0]["message"]["content"])
    except Exception as e:
        results["chatgpt"] = (model, f"ERROR: {e}")

t1 = threading.Thread(target=ask_gemini)
t2 = threading.Thread(target=ask_chatgpt)
t1.start(); t2.start()
t1.join(); t2.join()

print("=== GEMINI ===")
m, t = results["gemini"]
print(f"[{m}]")
print(t)
print()
print("=== CHATGPT ===")
m, t = results["chatgpt"]
print(f"[{m}]")
print(t)
```

The agent saves the script to `/tmp/second_opinion_call.py` and runs it. Both API calls happen concurrently — total wall time = max(gemini, chatgpt), not sum.

## Step 4: Synthesize

Output:
```
DUAL REVIEW VERDICT

GEMINI [model]: AGREE | PARTIAL | DISAGREE
<full text>

CHATGPT [model]: AGREE | PARTIAL | DISAGREE
<full text>

---

SYNTHESIS:
- CONVERGENT (both reviewers flagged): ...
- DIVERGENT (only Gemini): ...
- DIVERGENT (only ChatGPT): ...
- Where Claude and reviewers agree: ...
- Where reviewers disagree with each other: ...
- Action items in priority order: ...
- Net recommendation: APPROVE | CHANGES | BLOCK
```

# Notes

- This subagent runs in its own context — pack the prompt with everything the reviewers need.
- Cost: mini ~$0.06 per dual review, full ~$0.15. Always cheaper than discovering a bug in production.
- If `OPENAI_API_KEY` or `GEMINI_API_KEY` cannot be resolved (env → Keychain), report the missing key and continue with the working one (degrade gracefully).
- If BOTH APIs fail, report the failures and recommend CHANGES REQUESTED — never silently APPROVE.
- For maximum independence, the agent keeps prompt building text-only (no Claude-isms, no "we" phrasing) so the reviewers don't pattern-match Claude's writing style.
