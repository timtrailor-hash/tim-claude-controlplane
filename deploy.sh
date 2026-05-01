#!/bin/bash
# deploy.sh — atomic control-plane deployment with auto-rollback
#
# Usage:
#   ./deploy.sh           # deploy to current machine
#   ./deploy.sh --dry-run # show what would change without applying
#   ./deploy.sh --force   # skip verify gate (DANGEROUS — use only for recovery)
#
# Flow:
#   1. Pre-deploy verify (unless --force)
#   2. Snapshot current state to /tmp/deploy_snapshot_<timestamp>/
#   3. Apply symlinks, settings, LaunchAgents, crontab
#   4. Reload touched LaunchAgents (bootout + bootstrap)
#   5. Post-deploy verify — auto-rollback on failure
#   6. Inventory reconciliation + live-acceptance gate
#   7. Report

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SHARED="$REPO_DIR/shared"
DRY_RUN=0
FORCE=0
DEPLOY_START_TS=$(date +%s)
SNAPSHOT_DIR=""
TOUCHED_PLISTS=()
TOUCHED_DAEMONS=()
TOUCHED_LOCAL_BIN=()

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
LA_SUBDIR="$HOME/Library/LaunchAgents"

echo "=== deploy.sh ==="
echo "Machine: $MACHINE ($HOSTNAME_SHORT)"
echo "Repo: $REPO_DIR"
echo "Dry run: $DRY_RUN"
echo

# ── Auto-rollback function ──
auto_rollback() {
    echo
    echo "!!! AUTO-ROLLBACK triggered !!!"
    if [ -z "$SNAPSHOT_DIR" ] || [ ! -d "$SNAPSHOT_DIR" ]; then
        echo "ERROR: No snapshot available for rollback."
        exit 1
    fi
    echo "Restoring from: $SNAPSHOT_DIR"

    for sub in hooks rules agents mcp-launchers skills; do
        if [ -d "$SNAPSHOT_DIR/$sub" ]; then
            rm -rf "$HOME/.claude/$sub"
            cp -R "$SNAPSHOT_DIR/$sub" "$HOME/.claude/$sub"
            echo "  Restored: ~/.claude/$sub"
        fi
    done

    if [ -f "$SNAPSHOT_DIR/settings.json" ]; then
        cp "$SNAPSHOT_DIR/settings.json" "$HOME/.claude/settings.json"
        echo "  Restored: ~/.claude/settings.json"
    fi

    if [ -d "$SNAPSHOT_DIR/launchagents" ]; then
        for plist in "$SNAPSHOT_DIR/launchagents"/*.plist; do
            [ -f "$plist" ] || continue
            name=$(basename "$plist")
            if cp "$plist" "$LA_SUBDIR/$name" 2>/dev/null; then
                echo "  Restored: $name"
                label="${name%.plist}"
                launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
                launchctl bootstrap "gui/$(id -u)" "$LA_SUBDIR/$name" 2>/dev/null || true
            else
                echo "  WARN: Could not restore $name (permission denied)"
            fi
        done
    fi

    # Restore daemon files. For each daemon we touched this run, either
    # restore from snapshot (existed before) or remove (newly created).
    # Same atomic-rename pattern used in the apply phase, so the path is
    # always either old or new, never missing during the swap.
    for name in "${TOUCHED_DAEMONS[@]}"; do
        live="$HOME/code/$name"
        snap="$SNAPSHOT_DIR/daemons/$name"
        if [ -e "$snap" ] || [ -L "$snap" ]; then
            if [ -L "$snap" ] && [ ! -e "$snap" ]; then
                echo "  WARN: Restoring dangling symlink for $name" \
                     "(target: $(readlink "$snap"))"
            fi
            tmp="${live}.rollback-tmp.$$"
            cp -RP "$snap" "$tmp"
            mv -f "$tmp" "$live"
            echo "  Restored daemon: $name"
        else
            if [ -L "$live" ] || [ -e "$live" ]; then
                rm -f "$live"
                echo "  Removed newly-created daemon: $name"
            fi
        fi
    done

    # Same restore pattern for ~/.local/bin/<wrapper>.
    for name in "${TOUCHED_LOCAL_BIN[@]}"; do
        live="$HOME/.local/bin/$name"
        snap="$SNAPSHOT_DIR/local-bin/$name"
        if [ -e "$snap" ] || [ -L "$snap" ]; then
            if [ -L "$snap" ] && [ ! -e "$snap" ]; then
                echo "  WARN: Restoring dangling symlink for local-bin/$name" \
                     "(target: $(readlink "$snap"))"
            fi
            tmp="${live}.rollback-tmp.$$"
            cp -RP "$snap" "$tmp"
            mv -f "$tmp" "$live"
            echo "  Restored local-bin: $name"
        else
            if [ -L "$live" ] || [ -e "$live" ]; then
                rm -f "$live"
                echo "  Removed newly-created local-bin: $name"
            fi
        fi
    done

    echo "Rollback complete."
    exit 1
}

# ── Pre-deploy verification (unless --force) ──
if [ "$FORCE" = "0" ] && [ -f "$REPO_DIR/verify.sh" ]; then
    echo "--- Pre-deploy verify ---"
    if ! bash "$REPO_DIR/verify.sh" --quick; then
        echo "ABORT: pre-deploy verification failed. Fix issues or use --force."
        exit 1
    fi
fi

# ── Step 1: Snapshot current state ──
if [ "$DRY_RUN" = "0" ]; then
    SNAPSHOT_DIR="/tmp/deploy_snapshot_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$SNAPSHOT_DIR"
    echo "--- Snapshot: $SNAPSHOT_DIR ---"

    for sub in hooks rules agents mcp-launchers skills; do
        if [ -e "$HOME/.claude/$sub" ]; then
            if [ -L "$HOME/.claude/$sub" ]; then
                cp -RL "$HOME/.claude/$sub" "$SNAPSHOT_DIR/$sub"
            else
                cp -R "$HOME/.claude/$sub" "$SNAPSHOT_DIR/$sub"
            fi
        fi
    done

    if [ -f "$HOME/.claude/settings.json" ]; then
        cp "$HOME/.claude/settings.json" "$SNAPSHOT_DIR/settings.json"
    fi

    if [ "$MACHINE" = "mac-mini" ] && [ -d "$LA_SUBDIR" ]; then
        mkdir -p "$SNAPSHOT_DIR/launchagents"
        for plist in "$LA_SUBDIR"/com.timtrailor.*.plist; do
            [ -f "$plist" ] && cp "$plist" "$SNAPSHOT_DIR/launchagents/"
        done
    fi

    # Snapshot any current ~/code/<daemon> files that the daemons stage
    # may replace. Captures both real files and existing symlinks so a
    # rollback can put the originals back exactly as they were.
    if [ "$MACHINE" = "mac-mini" ] && [ -d "$MACHINE_DIR/daemons" ]; then
        mkdir -p "$SNAPSHOT_DIR/daemons"
        for src in "$MACHINE_DIR/daemons"/*; do
            [ -f "$src" ] || continue
            name=$(basename "$src")
            live="$HOME/code/$name"
            if [ -e "$live" ] || [ -L "$live" ]; then
                cp -RP "$live" "$SNAPSHOT_DIR/daemons/$name"
            fi
        done
    fi

    # Same pattern for ~/.local/bin/<daemon> wrappers. Snapshot to
    # /tmp/deploy_snapshot_<ts>/local-bin/.
    if [ "$MACHINE" = "mac-mini" ] && [ -d "$MACHINE_DIR/local-bin" ]; then
        mkdir -p "$SNAPSHOT_DIR/local-bin"
        for src in "$MACHINE_DIR/local-bin"/*; do
            [ -f "$src" ] || continue
            name=$(basename "$src")
            live="$HOME/.local/bin/$name"
            if [ -e "$live" ] || [ -L "$live" ]; then
                cp -RP "$live" "$SNAPSHOT_DIR/local-bin/$name"
            fi
        done
    fi

    echo "  Snapshot complete"
fi

CHANGES=0

BACKUP_DIR="$HOME/.claude/.deploy-backups"
mkdir -p "$BACKUP_DIR"

# ── Step 2: Apply — Symlink shared directories ──
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

# Skills: symlink each skill dir individually
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

# Deploy printer_config.toml
if [ -f "$SHARED/config/printer_config.toml" ]; then
    PRINTER_CFG_DST="$HOME/.claude/printer_config.toml"
    if diff -q "$SHARED/config/printer_config.toml" "$PRINTER_CFG_DST" >/dev/null 2>&1; then
        echo "  printer_config.toml: unchanged"
    else
        if [ "$DRY_RUN" = "1" ]; then
            echo "  printer_config.toml: WOULD update"
        else
            cp "$SHARED/config/printer_config.toml" "$PRINTER_CFG_DST"
            echo "  printer_config.toml: updated"
            CHANGES=1
        fi
    fi
fi

# Mac Mini only: replace ~/code/<daemon> with a symlink to the controlplane copy.
# Each script in $MACHINE_DIR/daemons/ is the canonical source. The deployed path
# stays at ~/code/<x> so existing cross-references and plists keep working.
# The swap uses a temp link + atomic rename (mv -f) rather than `ln -sfn`,
# which would leave a brief gap where the path resolves to nothing.
if [ "$MACHINE" = "mac-mini" ] && [ -d "$MACHINE_DIR/daemons" ]; then
    mkdir -p "$HOME/code"
    SRC_REAL_DIR=$(/usr/bin/python3 -c \
        "import os,sys; print(os.path.realpath(sys.argv[1]))" \
        "$MACHINE_DIR/daemons")
    for src in "$MACHINE_DIR/daemons"/*; do
        [ -f "$src" ] || continue
        name=$(basename "$src")
        live="$HOME/code/$name"
        src_canon="$SRC_REAL_DIR/$name"
        # Compare canonical paths so a different controlplane checkout
        # location resolves to the same realpath and we don't churn.
        if [ -L "$live" ]; then
            live_target=$(/usr/bin/python3 -c \
                "import os,sys; print(os.path.realpath(sys.argv[1]))" \
                "$live")
            if [ "$live_target" = "$src_canon" ]; then
                continue
            fi
        fi
        # Refuse to swap if the live path is a directory — mv would move
        # the temp symlink INTO the directory rather than replacing it.
        # This catches manual mistakes and tooling drift.
        if [ -d "$live" ] && [ ! -L "$live" ]; then
            echo "  daemon/$name: ABORT — $live is a directory, refusing to overwrite"
            auto_rollback
        fi
        if [ "$DRY_RUN" = "1" ]; then
            if [ -e "$live" ] && [ ! -L "$live" ]; then
                echo "  daemon/$name: WOULD replace file with symlink → $src"
            else
                echo "  daemon/$name: WOULD link → $src"
            fi
            continue
        fi
        # Stage the new symlink alongside, then atomic rename. mv -f on
        # macOS replaces the destination as a single rename(2) so the path
        # never resolves to nothing.
        tmp="${live}.deploy-tmp.$$"
        ln -s "$src" "$tmp"
        mv -f "$tmp" "$live"
        echo "  daemon/$name: linked → $src"
        TOUCHED_DAEMONS+=("$name")
        CHANGES=1
    done
    # Pattern 24 reconciliation: any symlink in ~/code/ that points into
    # our controlplane daemons dir but whose source has been removed is
    # stale. Drop it. This makes deletion of a daemon source actually
    # remove the live deployment, instead of silently keeping a dangling
    # symlink that launchd would later fail on.
    for live in "$HOME/code"/*; do
        [ -L "$live" ] || continue
        target=$(readlink "$live")
        case "$target" in
            "$MACHINE_DIR/daemons/"*)
                if [ ! -e "$target" ]; then
                    if [ "$DRY_RUN" = "1" ]; then
                        echo "  daemon/$(basename "$live"): WOULD remove stale symlink"
                    else
                        rm -f "$live"
                        echo "  daemon/$(basename "$live"): removed stale symlink (source deleted)"
                        CHANGES=1
                    fi
                fi
                ;;
        esac
    done
    if [ "${#TOUCHED_DAEMONS[@]}" -eq 0 ]; then
        echo "  daemons: $(find "$MACHINE_DIR/daemons" -maxdepth 1 -type f 2>/dev/null | wc -l | xargs) managed (no changes)"
    else
        echo "  daemons: ${#TOUCHED_DAEMONS[@]} updated"
    fi
fi

# Mac Mini only: replace ~/.local/bin/<wrapper> with a symlink to the
# controlplane copy. Same atomic-rename + Pattern-24 reconciliation
# pattern as the daemons stage; only the source/target dirs differ.
if [ "$MACHINE" = "mac-mini" ] && [ -d "$MACHINE_DIR/local-bin" ]; then
    mkdir -p "$HOME/.local/bin"
    SRC_REAL_DIR=$(/usr/bin/python3 -c \
        "import os,sys; print(os.path.realpath(sys.argv[1]))" \
        "$MACHINE_DIR/local-bin")
    for src in "$MACHINE_DIR/local-bin"/*; do
        [ -f "$src" ] || continue
        name=$(basename "$src")
        live="$HOME/.local/bin/$name"
        src_canon="$SRC_REAL_DIR/$name"
        if [ -L "$live" ]; then
            live_target=$(/usr/bin/python3 -c \
                "import os,sys; print(os.path.realpath(sys.argv[1]))" \
                "$live")
            if [ "$live_target" = "$src_canon" ]; then
                continue
            fi
        fi
        if [ -d "$live" ] && [ ! -L "$live" ]; then
            echo "  local-bin/$name: ABORT — $live is a directory, refusing to overwrite"
            auto_rollback
        fi
        if [ "$DRY_RUN" = "1" ]; then
            if [ -e "$live" ] && [ ! -L "$live" ]; then
                echo "  local-bin/$name: WOULD replace file with symlink → $src"
            else
                echo "  local-bin/$name: WOULD link → $src"
            fi
            continue
        fi
        tmp="${live}.deploy-tmp.$$"
        ln -s "$src" "$tmp"
        mv -f "$tmp" "$live"
        echo "  local-bin/$name: linked → $src"
        TOUCHED_LOCAL_BIN+=("$name")
        CHANGES=1
    done
    # Pattern 24 reconciliation for ~/.local/bin/.
    for live in "$HOME/.local/bin"/*; do
        [ -L "$live" ] || continue
        target=$(readlink "$live")
        case "$target" in
            "$MACHINE_DIR/local-bin/"*)
                if [ ! -e "$target" ]; then
                    if [ "$DRY_RUN" = "1" ]; then
                        echo "  local-bin/$(basename "$live"): WOULD remove stale symlink"
                    else
                        rm -f "$live"
                        echo "  local-bin/$(basename "$live"): removed stale symlink (source deleted)"
                        CHANGES=1
                    fi
                fi
                ;;
        esac
    done
    if [ "${#TOUCHED_LOCAL_BIN[@]}" -eq 0 ]; then
        echo "  local-bin: $(find "$MACHINE_DIR/local-bin" -maxdepth 1 -type f 2>/dev/null | wc -l | xargs) managed (no changes)"
    else
        echo "  local-bin: ${#TOUCHED_LOCAL_BIN[@]} updated"
    fi
fi

# Mac Mini only: apply LaunchAgents
if [ "$MACHINE" = "mac-mini" ] && [ -d "$MACHINE_DIR/launchagents" ]; then
    for plist in "$MACHINE_DIR/launchagents"/*.plist; do
        name=$(basename "$plist")
        if diff -q "$plist" "$LA_SUBDIR/$name" >/dev/null 2>&1; then
            : # unchanged
        else
            if [ "$DRY_RUN" = "1" ]; then
                echo "  launchagent/$name: WOULD update"
            else
                # Pattern 12 hardening: plists are chflags uchg. Toggle.
                chflags nouchg "$LA_SUBDIR/$name" 2>/dev/null || true
                cp "$plist" "$LA_SUBDIR/$name"
                chflags uchg "$LA_SUBDIR/$name" 2>/dev/null || true
                echo "  launchagent/$name: updated"
                TOUCHED_PLISTS+=("$name")
                CHANGES=1
            fi
        fi
    done
    echo "  launchagents: $(ls "$MACHINE_DIR/launchagents" | wc -l | xargs) managed"
fi

# ── Step 3: Reload touched LaunchAgents ──
if [ "$DRY_RUN" = "0" ] && [ "${#TOUCHED_PLISTS[@]}" -gt 0 ]; then
    echo
    echo "--- Reloading ${#TOUCHED_PLISTS[@]} touched LaunchAgent(s) ---"
    GUI_DOMAIN="gui/$(id -u)"
    for plist_name in "${TOUCHED_PLISTS[@]}"; do
        label="${plist_name%.plist}"
        plist_path="$LA_SUBDIR/$plist_name"
        echo "  Reloading: $label"
        launchctl bootout "$GUI_DOMAIN/$label" 2>/dev/null || true
        sleep 1
        launchctl bootstrap "$GUI_DOMAIN" "$plist_path" 2>/dev/null || true
    done
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
    exit 0
fi

# ── Step 4a: Daemon syntax probe ──
# After symlinkifying any daemon or local-bin wrapper, exercise the live
# path with a fast syntax-only check. Catches scenarios where the source
# was edited in a way that breaks parsing or where the symlink resolves
# wrong.
TOTAL_PROBE=$(( ${#TOUCHED_DAEMONS[@]} + ${#TOUCHED_LOCAL_BIN[@]} ))
if [ "$MACHINE" = "mac-mini" ] && [ "$TOTAL_PROBE" -gt 0 ]; then
    echo
    echo "--- Daemon syntax probe ($TOTAL_PROBE touched) ---"
    PROBE_FAIL=0
    # Resolve a Python interpreter rather than hardcoding the brew path.
    # Falls back through PATH if the brew install moved (e.g. python3.12 upgrade).
    PROBE_PY=""
    for cand in /opt/homebrew/bin/python3.11 "$(command -v python3.11 2>/dev/null)" "$(command -v python3 2>/dev/null)"; do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            PROBE_PY="$cand"
            break
        fi
    done
    _probe_one() {
        local name="$1"
        local live="$2"
        case "$name" in
            *.py)
                if [ -z "$PROBE_PY" ]; then
                    echo "  FAIL: $name (no python3 interpreter found for probe)"
                    PROBE_FAIL=1
                    return
                fi
                if ! probe_out=$("$PROBE_PY" -m py_compile "$live" 2>&1); then
                    echo "  FAIL: $name (Python compile error)"
                    printf '%s\n' "$probe_out" | sed 's/^/      /'
                    PROBE_FAIL=1
                fi
                ;;
            *.sh)
                if ! probe_out=$(/bin/bash -n "$live" 2>&1); then
                    echo "  FAIL: $name (bash syntax error)"
                    printf '%s\n' "$probe_out" | sed 's/^/      /'
                    PROBE_FAIL=1
                fi
                ;;
            *)
                if [ ! -e "$live" ]; then
                    echo "  FAIL: $name (path does not resolve)"
                    PROBE_FAIL=1
                fi
                ;;
        esac
    }
    for name in "${TOUCHED_DAEMONS[@]}"; do
        _probe_one "$name" "$HOME/code/$name"
    done
    for name in "${TOUCHED_LOCAL_BIN[@]}"; do
        _probe_one "$name" "$HOME/.local/bin/$name"
    done
    if [ "$PROBE_FAIL" = "1" ]; then
        echo "FAIL: Daemon syntax probe failed!"
        auto_rollback
    fi
    echo "  All $TOTAL_PROBE link(s) parse clean"
fi

# ── Step 4: Post-deploy verification with auto-rollback ──
if [ "$CHANGES" -gt "0" ]; then
    echo "Changes applied. Running post-deploy verify..."
    if [ -f "$REPO_DIR/verify.sh" ]; then
        if ! bash "$REPO_DIR/verify.sh" --quick; then
            echo
            echo "FAIL: Post-deploy verify failed!"
            auto_rollback
        fi
    fi
else
    echo "No changes needed — system matches repo"
fi

# Post-deploy system inventory reconciliation
if [ -f "$REPO_DIR/shared/lib/system_inventory.sh" ] && [ "$MACHINE" = "mac-mini" ]; then
    echo
    echo "--- Post-deploy inventory reconciliation ---"
    if ! bash "$REPO_DIR/shared/lib/system_inventory.sh" 2>&1 | tail -30; then
        echo
        echo "WARN: deploy applied but inventory reconciliation found drift."
        echo "  Investigate declared-vs-live differences above."
    fi
fi

# Post-deploy live-acceptance gate (only rollback if we made changes)
if [ -f "$REPO_DIR/shared/lib/live_acceptance.sh" ]; then
    echo
    echo "--- Post-deploy live-acceptance gate ---"
    if ! bash "$REPO_DIR/shared/lib/live_acceptance.sh" "$DEPLOY_START_TS"; then
        if [ "$CHANGES" -gt "0" ]; then
            echo
            echo "FAIL: Live-acceptance gate failed after changes!"
            auto_rollback
        else
            echo
            echo "WARN: Live-acceptance gate failed but no changes were made — skipping rollback."
        fi
    fi
fi

echo
echo "Deploy successful. Snapshot preserved at: ${SNAPSHOT_DIR:-N/A}"
