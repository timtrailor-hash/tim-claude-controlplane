#!/bin/bash
# deploy.sh — idempotent control-plane deployment
#
# Usage:
#   ./deploy.sh           # deploy to current machine
#   ./deploy.sh --dry-run # show what would change without applying
#   ./deploy.sh --force   # skip verify gate (DANGEROUS — use only for recovery)
#
# Flow:
#   1. Detect machine (hostname)
#   2. Run verify.sh (pre-deploy gate)
#   3. Symlink shared/ into ~/.claude/
#   4. Apply machine-specific settings.json
#   5. Apply LaunchAgents + crontab (Mac Mini only)
#   6. Run verify.sh again (post-deploy validation)
#   7. Report

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SHARED="$REPO_DIR/shared"
DRY_RUN=0
FORCE=0
DEPLOY_START_TS=$(date +%s)
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
    esac
done

# Detect machine
HOSTNAME_SHORT=$(hostname -s)
case "$HOSTNAME_SHORT" in
    *mini*|*Mini*) MACHINE="mac-mini" ;;
    *)             MACHINE="laptop" ;;
esac
MACHINE_DIR="$REPO_DIR/machines/$MACHINE"

echo "=== deploy.sh ==="
echo "Machine: $MACHINE ($HOSTNAME_SHORT)"
echo "Repo: $REPO_DIR"
echo "Dry run: $DRY_RUN"
echo

# Pre-deploy verification (unless --force)
if [ "$FORCE" = "0" ] && [ -f "$REPO_DIR/verify.sh" ]; then
    echo "--- Pre-deploy verify ---"
    if ! bash "$REPO_DIR/verify.sh" --quick; then
        echo "ABORT: pre-deploy verification failed. Fix issues or use --force."
        exit 1
    fi
fi

CHANGES=0

# Backups go OUTSIDE ~/.claude/ managed dirs so drift_check doesn't trip on them
BACKUP_DIR="$HOME/.claude/.deploy-backups"
mkdir -p "$BACKUP_DIR"

# Symlink shared directories into ~/.claude/
for sub in rules hooks agents mcp-launchers; do
    SRC="$SHARED/$sub"
    DST="$HOME/.claude/$sub"
    [ -d "$SRC" ] || continue

    if [ -L "$DST" ] && [ "$(readlink "$DST")" = "$SRC" ]; then
        echo "  $sub: already linked"
    elif [ -d "$DST" ] && [ ! -L "$DST" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            echo "  $sub: WOULD replace directory with symlink → $SRC"
        else
            mv "$DST" "$BACKUP_DIR/${sub}.pre-deploy-$(date +%s)"
            ln -s "$SRC" "$DST"
            echo "  $sub: replaced with symlink (old backed up)"
            CHANGES=1
        fi
    else
        if [ "$DRY_RUN" = "1" ]; then
            echo "  $sub: WOULD create symlink → $SRC"
        else
            mkdir -p "$(dirname "$DST")"
            ln -sf "$SRC" "$DST"
            echo "  $sub: linked"
            CHANGES=1
        fi
    fi
done

# Skills: symlink each skill dir individually (preserves per-machine skill additions)
if [ -d "$SHARED/skills" ]; then
    mkdir -p "$HOME/.claude/skills"
    for skill_dir in "$SHARED/skills"/*/; do
        skill_name=$(basename "$skill_dir")
        DST="$HOME/.claude/skills/$skill_name"
        if [ -L "$DST" ] && [ "$(readlink "$DST")" = "$skill_dir" ]; then
            : # already linked
        elif [ -d "$DST" ] && [ ! -L "$DST" ]; then
            if [ "$DRY_RUN" = "1" ]; then
                echo "  skill/$skill_name: WOULD replace with symlink"
            else
                mv "$DST" "$BACKUP_DIR/skill-${skill_name}.pre-deploy-$(date +%s)"
                ln -s "$skill_dir" "$DST"
                echo "  skill/$skill_name: replaced with symlink"
                CHANGES=1
            fi
        else
            if [ "$DRY_RUN" = "1" ]; then
                echo "  skill/$skill_name: WOULD link"
            else
                ln -sf "$skill_dir" "$DST"
                CHANGES=1
            fi
        fi
    done
    echo "  skills: $(ls "$SHARED/skills" | wc -l | xargs) linked"
fi

# Apply machine-specific settings.json
if [ -f "$MACHINE_DIR/settings.json" ]; then
    SETTINGS_DST="$HOME/.claude/settings.json"
    if diff -q "$MACHINE_DIR/settings.json" "$SETTINGS_DST" >/dev/null 2>&1; then
        echo "  settings.json: unchanged"
    else
        if [ "$DRY_RUN" = "1" ]; then
            echo "  settings.json: WOULD update"
            diff "$SETTINGS_DST" "$MACHINE_DIR/settings.json" | head -20
        else
            cp "$MACHINE_DIR/settings.json" "$SETTINGS_DST"
            echo "  settings.json: updated"
            CHANGES=1
        fi
    fi
fi

# Mac Mini only: apply LaunchAgents
if [ "$MACHINE" = "mac-mini" ] && [ -d "$MACHINE_DIR/launchagents" ]; then
    LA_DIR="$HOME/Library/LaunchAgents"
    for plist in "$MACHINE_DIR/launchagents"/*.plist; do
        name=$(basename "$plist")
        if diff -q "$plist" "$LA_DIR/$name" >/dev/null 2>&1; then
            : # unchanged
        else
            if [ "$DRY_RUN" = "1" ]; then
                echo "  launchagent/$name: WOULD update"
            else
                cp "$plist" "$LA_DIR/$name"
                echo "  launchagent/$name: updated"
                CHANGES=1
            fi
        fi
    done
    echo "  launchagents: $(ls "$MACHINE_DIR/launchagents" | wc -l | xargs) managed"
fi

# Mac Mini only: apply crontab
if [ "$MACHINE" = "mac-mini" ] && [ -f "$MACHINE_DIR/crontab.txt" ]; then
    CURRENT=$(crontab -l 2>/dev/null || true)
    DESIRED=$(cat "$MACHINE_DIR/crontab.txt")
    if [ "$CURRENT" = "$DESIRED" ]; then
        echo "  crontab: unchanged"
    else
        if [ "$DRY_RUN" = "1" ]; then
            echo "  crontab: WOULD update"
        else
            echo "$DESIRED" | crontab -
            echo "  crontab: updated"
            CHANGES=1
        fi
    fi
fi

echo
if [ "$DRY_RUN" = "1" ]; then
    echo "DRY RUN — no changes applied"
elif [ "$CHANGES" = "0" ]; then
    echo "No changes needed — system matches repo"
else
    echo "Changes applied. Running post-deploy verify..."
    if [ -f "$REPO_DIR/verify.sh" ]; then
        bash "$REPO_DIR/verify.sh" --quick
    fi
fi

# Post-deploy system inventory reconciliation — ChatGPT's highest-leverage
# miss from the midway review. Scans live Mac Mini state, diffs against
# system_map.yaml, fails the deploy if any declared service is MISSING or
# any live service is UNDECLARED. On laptop, the inventory script no-ops.
if [ -f "$REPO_DIR/shared/lib/system_inventory.sh" ] && [ "$DRY_RUN" = "0" ] && [ "$MACHINE" = "mac-mini" ]; then
    echo
    echo "--- Post-deploy inventory reconciliation ---"
    if ! bash "$REPO_DIR/shared/lib/system_inventory.sh" 2>&1 | tail -30; then
        echo
        echo "WARN: deploy applied but inventory reconciliation found drift."
        echo "  Investigate declared-vs-live differences above."
        # Note: system_inventory exits non-zero on ANY discrepancy including
        # listeners not in a declared list. That's too noisy for a strict
        # gate right now, so we log + continue. Escalate to exit 3 after
        # the undeclared-listener class is cleaned up.
    fi
fi

# Post-deploy live-acceptance gate (Pattern 20 fix).
# Only runs on mac-mini (where user-visible monitors live). Strict mode
# by default — any required_on_deploy=true check failing will propagate
# a non-zero exit code from deploy.sh. The deploy has already been
# applied at this point (symlinks, plists in place); the gate is
# verification, not rollback. Non-zero exit tells the operator to
# investigate, not that the changes were reverted.
if [ -f "$REPO_DIR/shared/lib/live_acceptance.sh" ] && [ "$DRY_RUN" = "0" ]; then
    echo
    echo "--- Post-deploy live-acceptance gate ---"
    if ! bash "$REPO_DIR/shared/lib/live_acceptance.sh" "$DEPLOY_START_TS"; then
        echo
        echo "WARN: deploy applied but live-acceptance gate failed."
        echo "  Investigate /tmp/live_acceptance.log for details."
        echo "  Changes are still in place; this is a verification failure."
        exit 3
    fi
fi
