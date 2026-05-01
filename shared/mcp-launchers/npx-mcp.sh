#!/bin/bash
# Generic launcher for any npx-published MCP server.
# Why: Mac Mini's Claude spawns MCP servers without /opt/homebrew/bin on PATH,
# so a bare `npx -y <pkg>` fails with "node: No such file or directory".
# This wrapper prepends the standard Homebrew + system paths and execs npx.
#
# Usage: ~/.claude/mcp-launchers/npx-mcp.sh <package-spec> [extra args]
# Example: ~/.claude/mcp-launchers/npx-mcp.sh @playwright/mcp@latest

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec npx -y "$@"
