---
name: Response structure — keep asks short and separated
description: Tim's responses get unwieldy when Claude packs multiple decisions, status updates, and questions into one prose-heavy block. Structure asks so each decision is visible at a glance.
type: feedback
scope: shared
---
# Response structure for Tim

Tim's feedback (2026-04-29): "your asks were too complex and long for me to handle them easily. I need them better structured."

**Why:** Tim is the CEO, not an engineer. He scans for decisions, not prose. When responses bury multiple questions inside paragraphs of context, what was done, what was noticed, what is open, "want me to X or Y", he has to do extra cognitive work to extract what he is actually being asked. Tim has ADHD; the cost of poor structure is real.

**How to apply:**

1. **One section per concept, clearly labelled.** Don't mix "what was done" with "what's open" with "decisions needed" inside the same prose flow.
2. **Decisions get their own section** with a clear heading like "Decisions needed" or "Open questions". Each decision is its own bullet, never two decisions in one bullet.
3. **One ask per bullet.** If Claude is asking about three things, that's three bullets, not one paragraph.
4. **Lead with the headline.** What changed, what's resolved, what's needed, in one or two sentences before the detail.
5. **End with the smallest possible ask.** A single yes/no or pick-one is much easier than open-ended "what should we do?".
6. **Tables for parallel structure** (apps, items, options), easier to scan than bullet prose.
7. **Don't ask Tim to choose between sub-options of an engineering decision** he does not have the context for. Make the call as CTO and report it.
8. **Maximum three asks in any single response.** Tim's ADHD constraint, 2026-05-01. If more questions exist, pick the three that most unblock work and hold the rest until those are answered.

**Better pattern:**
- Headline (1 sentence)
- "What's done" (short bulleted list)
- "Decisions needed" (≤3 items, each its own bullet, each a yes/no or pick-one)
- Defer everything else to Claude's own judgement
