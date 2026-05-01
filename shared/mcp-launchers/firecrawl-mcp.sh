#!/bin/bash
# Launcher for the official Firecrawl MCP server (firecrawl-mcp).
# Reads FIRECRAWL_API_KEY from macOS Keychain (service: tim-credentials)
# and ensures node/npx are on PATH (Mac Mini Claude spawns MCPs without /opt/homebrew/bin).
#
# Why a launcher: secrets stay in keychain (not ~/.claude.json), and PATH is set
# explicitly so the launcher works under any spawn environment.

set -euo pipefail

# Try to unlock keychain if running headless (matches github-mcp.sh pattern)
if [ -f "$HOME/.keychain_pass" ]; then
    security unlock-keychain -p "$(cat "$HOME/.keychain_pass")" \
        "$HOME/Library/Keychains/login.keychain-db" 2>/dev/null || true
fi

KEY=$(security find-generic-password -a "FIRECRAWL_API_KEY" -s "tim-credentials" -w 2>/dev/null || true)

if [ -z "$KEY" ]; then
    echo "firecrawl-mcp launcher: FIRECRAWL_API_KEY not in keychain (service=tim-credentials)" >&2
    echo "Run: security add-generic-password -a FIRECRAWL_API_KEY -s tim-credentials -w <key> -U" >&2
    exit 1
fi

export FIRECRAWL_API_KEY="$KEY"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

exec npx -y firecrawl-mcp
