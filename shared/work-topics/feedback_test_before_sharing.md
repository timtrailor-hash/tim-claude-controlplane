---
name: Test properly before sharing URLs/links
description: Always do a real end-to-end test (not just HTTP 200) before giving Tim a URL to try
type: feedback
scope: shared
---

Test the actual user experience, not just whether the server responds with HTTP 200.

**Why:** During an earlier browser-terminal setup, Claude sent Tim multiple URLs that returned HTTP 200 but had broken WebSocket connections (blank screens, dropped sessions). Tim correctly called Claude out for not testing properly before sharing.

**How to apply:** Before sharing any URL/endpoint with Tim:
1. Test the actual functionality, not just "does it respond"
2. For WebSocket-based tools: test the WebSocket connection, not just the HTTP page
3. For UIs: verify the page actually renders and is interactive
4. Say what was tested and what the results were
5. If full testing is not possible, say so honestly rather than implying it works
