#!/bin/bash
# PreToolUse hook wrapper — delegates to Python for cross-platform compatibility
exec python3 ~/.claude/hooks/printer_safety.py
