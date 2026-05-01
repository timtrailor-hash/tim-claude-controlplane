---
name: feedback_test_before_shipping
description: Tim expects iOS changes to be tested before deployment — don't ship untested gesture/UI changes
type: feedback
scope: shared
---
Test iOS UI/gesture changes before shipping. Don't claim a fix works based on code review alone — SwiftUI's gesture arbitration, text selection, and widget rendering have behaviours that only manifest on-device.

**Why:** During the 2026-04-11 session, three consecutive text-selection "fixes" (DragGesture minimumDistance tuning, .textSelection on container, .textSelection per-line) all shipped without testing and all failed. Tim explicitly asked "did you test it?" — the answer was no. The working fix (UITextView via UIViewRepresentable) was the fourth attempt. Each failed iteration cost a build+deploy cycle and eroded trust.

**How to apply:** For any iOS change that involves gestures, selection, text rendering, or widget display: either use the iOS Simulator to verify (xcodebuild + xcrun simctl), or clearly state "I can't test this remotely — here's what I changed and why I expect it to work, but please verify on device" BEFORE deploying. Never say "shipped" and then discover it doesn't work when Tim tests.
