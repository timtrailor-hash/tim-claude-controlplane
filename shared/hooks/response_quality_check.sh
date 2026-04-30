#!/bin/bash
# response_quality_check.sh — Stop hook (response-gate)
#
# Runs response_quality_check.py against the latest assistant message.
# Exits 2 + stderr on violation, which Claude Code feeds back to Claude
# as a continuation; Claude self-corrects. Anti-loop: 3-retry cap.
#
# See feedback_response_gate.md for the design rationale.

exec /opt/homebrew/bin/python3.11 /Users/timtrailor/.claude/hooks/response_quality_check.py
