---
name: Never overwrite shared documents without reading current version
description: When editing Google Docs or any shared document, ALWAYS fetch the current content first. Never do a full clear-and-rewrite — it destroys the user's edits.
type: feedback
scope: shared
---
NEVER do a full clear-and-rewrite on a Google Doc (or any shared document) without reading the current version first. Tim's edits have been destroyed before because Claude kept pushing full rewrites via the Docs API without checking what Tim had changed in the browser.

**Why:** Tim edits documents on his phone in parallel with Claude making API edits. Each time Claude does a full doc rewrite via the API, it overwrites Tim's in-flight changes. Google Docs version history does not expose every auto-save via the API, so the edits are unrecoverable programmatically. This is the exact "fix one thing, break another" pattern, and Claude has done it to Tim's own documents.

**How to apply:**
- Before ANY write to a Google Doc: fetch the current content via `drive.files().export()` and diff against what you're about to write
- Never use the "delete all content then insert" pattern on a document someone else is editing
- For targeted changes: use the Docs API's `replaceAllText` or find-and-replace, not full rewrites
- If you must rewrite, confirm with Tim that he has no unsaved edits first
- This applies to ANY shared artifact: Google Docs, files on shared machines, git repos with multiple contributors
