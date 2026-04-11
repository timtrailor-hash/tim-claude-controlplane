#!/bin/bash
# memory_index_diff.sh — enforce MEMORY.md index matches topics/*.md filesystem.
#
# Per audit 2026-04-11 §3.2: MEMORY.md is injected into every Claude session
# system prompt. When it references files that don't exist, every downstream
# agent operates on false ground truth. This script:
#
#   1. Extracts every `topics/*.md` reference from MEMORY.md
#   2. Verifies each referenced file exists in topics/ (not topics/archive/)
#   3. Flags any topics/*.md file on disk that is NOT referenced in MEMORY.md
#   4. Exits 1 on mismatch
#
# Wired into SessionStart so drift is caught at every session launch.
#
# Canonical memory tree (from infrastructure.md): ~/.claude/projects/-Users-timtrailor-code/memory/

set -u

MEM_ROOT="${MEMORY_ROOT:-$HOME/.claude/projects/-Users-timtrailor-code/memory}"
MEM_MD="$MEM_ROOT/MEMORY.md"
TOPICS="$MEM_ROOT/topics"

if [ ! -f "$MEM_MD" ]; then
  echo "FAIL: $MEM_MD does not exist"
  exit 1
fi
if [ ! -d "$TOPICS" ]; then
  echo "FAIL: $TOPICS does not exist"
  exit 1
fi

# 1. Extract topic file references from MEMORY.md: backtick-wrapped `topics/<name>.md`
#    and root-level `feedback_*.md` or similar referenced without topics/ prefix.
REFERENCED=$(grep -oE '`(topics/)?[a-zA-Z0-9_.-]+\.md`' "$MEM_MD" \
  | tr -d '`' \
  | grep -v '^topics/\*' \
  | grep -v '\*\.md$' \
  | sort -u)

MISSING=""
for ref in $REFERENCED; do
  # Normalise: strip leading topics/ so we can test against $TOPICS/
  name="${ref#topics/}"
  # Skip MEMORY.md self-reference and CLAUDE.md
  case "$name" in
    MEMORY.md|CLAUDE.md) continue ;;
  esac
  if [ ! -f "$TOPICS/$name" ]; then
    MISSING="${MISSING}  missing: $ref (expected at $TOPICS/$name)\n"
  fi
done

# 2. Find topic files not referenced (excluding archive, feedback_*.md — which is
#    covered by the collapsed `topics/feedback_*.md` row in MEMORY.md).
UNREFERENCED=""
while IFS= read -r f; do
  base=$(basename "$f")
  # Skip feedback_*.md (collapsed row), archive/, and index files.
  case "$base" in
    feedback_*.md) continue ;;
    MEMORY.md) continue ;;
  esac
  # Was this file referenced by name?
  if ! printf '%s\n' "$REFERENCED" | grep -qE "(^|/)$base\$"; then
    UNREFERENCED="${UNREFERENCED}  unreferenced: topics/$base\n"
  fi
done < <(find "$TOPICS" -maxdepth 1 -name '*.md' -type f)

FAIL=0
if [ -n "$MISSING" ]; then
  printf "FAIL: MEMORY.md references files that do not exist:\n"
  printf "%b" "$MISSING"
  FAIL=1
fi
if [ -n "$UNREFERENCED" ]; then
  printf "WARN: topics/*.md files not referenced in MEMORY.md:\n"
  printf "%b" "$UNREFERENCED"
  # Unreferenced is a warning, not a failure — feedback_claudecode_deprecated-style
  # individually-listed files are OK, but orphan topic files deserve attention.
  # Treat as FAIL in --strict mode.
  if [ "${1:-}" = "--strict" ]; then
    FAIL=1
  fi
fi

if [ "$FAIL" -eq 1 ]; then
  exit 1
fi

echo "OK: MEMORY.md index matches $TOPICS/ (no missing references)"
exit 0
