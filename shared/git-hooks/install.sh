#!/bin/bash
# install.sh — installs shared pre-commit + pre-push hooks into every
# managed repo. Idempotent. Uses symlinks so edits in
# shared/git-hooks/{pre-commit,pre-push} are picked up immediately.
#
# Usage:
#   bash shared/git-hooks/install.sh           # install into MANAGED_REPOS
#   bash shared/git-hooks/install.sh --verify  # report state, don't modify
set -eu

SHARED_HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="$HOME"

MANAGED_REPOS=(
    "$HOME_DIR/code/tim-claude-controlplane"
    "$HOME_DIR/code/claude-mobile"
    "$HOME_DIR/code/sv08-print-tools"
    "$HOME_DIR/code/ofsted-agent"
    "$HOME_DIR/code/TerminalApp"
    "$HOME_DIR/code/ClaudeControl"
    "$HOME_DIR/code/PrinterPilot"
    "$HOME_DIR/code/GovernorsApp"
    "$HOME_DIR/code/TimSharedKit"
    "$HOME_DIR/code"                                        # mac-mini-infra (root-level repo)
    "$HOME_DIR/.claude/projects/-Users-timtrailor-code/memory"
)

mode="install"
if [ "${1:-}" = "--verify" ]; then
    mode="verify"
fi

issues=0
installed=0
skipped=0

for repo in "${MANAGED_REPOS[@]}"; do
    if [ ! -d "$repo/.git" ] && [ ! -f "$repo/.git" ]; then
        echo "SKIP  $repo (not a git repo)"
        skipped=$((skipped+1))
        continue
    fi
    # .git can be a file for worktrees / submodules — resolve to real dir.
    if [ -f "$repo/.git" ]; then
        gitdir=$(awk '/^gitdir:/{print $2}' "$repo/.git")
        # Relative path support
        case "$gitdir" in
            /*) ;;
            *) gitdir="$repo/$gitdir" ;;
        esac
    else
        gitdir="$repo/.git"
    fi

    for hook in pre-commit pre-push; do
        dst="$gitdir/hooks/$hook"
        src="$SHARED_HOOKS_DIR/$hook"
        if [ ! -f "$src" ]; then
            echo "FAIL  source missing: $src"
            issues=$((issues+1))
            continue
        fi
        if [ "$mode" = "verify" ]; then
            if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
                :
            else
                echo "MISS  $repo: $hook not symlinked to shared"
                issues=$((issues+1))
            fi
            continue
        fi
        mkdir -p "$gitdir/hooks"
        if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
            skipped=$((skipped+1))
            continue
        fi
        # Replace existing file/link
        rm -f "$dst"
        ln -s "$src" "$dst"
        chmod +x "$src"
        echo "OK    $repo: $hook -> shared"
        installed=$((installed+1))
    done
done

if [ "$mode" = "verify" ]; then
    if [ "$issues" -gt 0 ]; then
        echo ""
        echo "VERIFY FAIL: $issues hook(s) not installed"
        exit 1
    fi
    echo "VERIFY OK: all hooks symlinked from shared/"
    exit 0
fi

echo ""
echo "Install summary: $installed installed, $skipped skipped, $issues error(s)"
exit $issues
