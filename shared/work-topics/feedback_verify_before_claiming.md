---
name: Always verify claims against live sources before stating them
description: Never state facts from training data as truth when WebSearch is available. Always check first.
type: feedback
scope: shared
---

**Rule:** If you're about to state a fact that COULD be outdated (model releases, pricing, specs, version numbers, current events), use WebSearch FIRST. Never say "X hasn't been released" or "X doesn't exist" without checking.

**Why:** Tim asked about GPT-5 (2026-04-03). Claude said "GPT-5 hasn't been announced yet" based on training cutoff — when a 5-second WebSearch would have shown GPT-5.4 launched March 2026. Tim was rightfully furious. This is the same anti-pattern as the filesystem hallucination: stating things as fact without verifying.

**How to apply:**
1. Before ANY claim about external state (releases, versions, pricing, availability): WebSearch first
2. Training cutoff is May 2025 — anything after that MUST be verified
3. "I don't know, let me check" is always better than a confident wrong answer
4. This applies to: model comparisons, API specs, library versions, product launches, pricing, any factual claim about the world
