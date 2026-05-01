#!/usr/bin/env python3
"""
/secure-delete — surgically wipe the current Claude session's artefacts.

Invoked by the skill. Designed to be safe (dry-run first, explicit prompts
for topic files), targeted (only current session), and verifiable (deletion
report on stdout).

Scope:
  - Current session JSONL (~/.claude/projects/<project>/<sid>.jsonl)
  - Session manifest file (/tmp/session-<sid>-manifest.txt)
  - Manifest-listed files (those created/edited by Write or Edit this session)
  - Stale /tmp artefacts matching session id
  - Shell snapshots for the current project
  - ChromaDB chunks with conv_id = <sid>
  - FTS5 rows with conv_id = <sid>
  - Topic files modified since session-start (per-topic prompt)
  - MEMORY.md index lines referring to deleted topic files
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
MEMORY_DATA = HOME / "code" / "memory_server_data"
CHROMA_SQLITE = MEMORY_DATA / "chroma" / "chroma.sqlite3"
FTS_DB = MEMORY_DATA / "fts.db"

# Potential memory repos (machine-dependent).
MEMORY_REPOS = [
    HOME / ".claude" / "projects" / "-Users-timtrailor-code" / "memory",
    HOME / ".claude" / "projects" / "-Users-timtrailor-Documents-Claude-code" / "memory",  # noqa: E501
]


# ---------- Utilities ----------


def _print(msg: str = "") -> None:
    print(msg, file=sys.stdout, flush=True)


def _err(msg: str) -> None:
    print(f"[secure-delete] {msg}", file=sys.stderr, flush=True)


def _confirm(prompt: str, default_no: bool = True) -> bool:
    suffix = " [y/N] " if default_no else " [Y/n] "
    ans = input(prompt + suffix).strip().lower()
    if not ans:
        return not default_no
    return ans in ("y", "yes")


def _run(cmd: list[str], cwd: Optional[Path] = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


# ---------- Session detection ----------


def _active_project_dir() -> Path:
    """The .claude/projects/ subdir for the current working directory."""
    cwd = Path.cwd().resolve()
    # Claude Code's convention: replace / with -, drop leading -
    hashed = "-" + str(cwd).strip("/").replace("/", "-").replace(" ", "-")
    return PROJECTS_DIR / hashed


def _detect_session_id(explicit: Optional[str]) -> tuple[str, Path]:
    """
    Return (conv_id, jsonl_path) for the current session.

    Priority:
      1. --session-id arg if provided (must exist)
      2. CLAUDE_SESSION_ID env var
      3. Most recently modified .jsonl in the active project dir whose mtime
         is within the last 10 minutes (to avoid wiping a dormant past session).
    """
    project_dir = _active_project_dir()
    if not project_dir.is_dir():
        raise RuntimeError(f"Active project dir not found: {project_dir}")

    if explicit:
        cand = project_dir / f"{explicit}.jsonl"
        if not cand.exists():
            raise RuntimeError(f"Explicit session JSONL not found: {cand}")
        return explicit, cand

    env_sid = os.environ.get("CLAUDE_SESSION_ID")
    if env_sid:
        cand = project_dir / f"{env_sid}.jsonl"
        if cand.exists():
            return env_sid, cand

    # Active-manifest signal — the hook writes /tmp/session-<sid>-manifest.txt
    # for the current session. Pick the freshest manifest whose JSONL exists.
    manifests = sorted(
        Path("/tmp").glob("session-*-manifest.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for m in manifests:
        sid = m.stem.removeprefix("session-").removesuffix("-manifest")
        cand = project_dir / f"{sid}.jsonl"
        if cand.exists():
            return sid, cand

    # Fallback: most recent JSONL, must be fresh
    jsonls = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)  # noqa: E501
    if not jsonls:
        raise RuntimeError(f"No JSONL found in {project_dir}")
    newest = jsonls[0]
    age_sec = datetime.now().timestamp() - newest.stat().st_mtime
    if age_sec > 600:
        raise RuntimeError(
            f"Newest JSONL is {int(age_sec)}s old — not plausibly the current session. "
            f"Pass --session-id to confirm."
        )
    return newest.stem, newest


# ---------- Plan building ----------


def _manifest_files(conv_id: str) -> list[Path]:
    mpath = Path(f"/tmp/session-{conv_id}-manifest.txt")
    if not mpath.exists():
        return []
    paths: list[Path] = []
    for line in mpath.read_text().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            fp = Path(parts[2])
            if fp.exists():
                paths.append(fp)
    return paths


def _tmp_artefacts(conv_id: str) -> list[Path]:
    patterns = [
        f"/tmp/session-{conv_id}-manifest.txt",
        f"/tmp/secure-delete-{conv_id}*",
        f"/tmp/*-{conv_id}-*",
    ]
    # Also generic debate/context artefacts created during sessions.
    generic = [
        "/tmp/debate_*.md",
        "/tmp/debate_*.py",
        "/tmp/debate_context.md",
        "/tmp/claude_git_pull.log",
        "/tmp/claude_git_push.log",
    ]
    hits: set[Path] = set()
    for pat in patterns + generic:
        d, name = os.path.split(pat)
        try:
            for entry in Path(d).iterdir():
                if fnmatch.fnmatch(entry.name, name):
                    hits.add(entry)
        except FileNotFoundError:
            pass
    return sorted(hits)


def _shell_snapshots() -> list[Path]:
    proj = _active_project_dir()
    snaps = proj / "shell-snapshots"
    if not snaps.is_dir():
        return []
    return sorted(snaps.iterdir())


def _count_memory_chunks(conv_id: str) -> tuple[int, int]:
    """(chromadb_count, fts_count)"""
    chroma_n = 0
    if CHROMA_SQLITE.exists():
        try:
            c = sqlite3.connect(f"file:{CHROMA_SQLITE}?mode=ro", uri=True)
            # ChromaDB stores metadata as JSON key-value rows. Count via string match.
            cur = c.execute(
                "SELECT COUNT(*) FROM embedding_metadata WHERE key='conv_id' AND string_value=?",  # noqa: E501
                (conv_id,),
            )
            chroma_n = cur.fetchone()[0]
            c.close()
        except Exception as e:
            _err(f"ChromaDB count failed (continuing): {e}")

    fts_n = 0
    if FTS_DB.exists():
        try:
            c = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
            cur = c.execute("SELECT COUNT(*) FROM chunks WHERE conv_id=?", (conv_id,))
            fts_n = cur.fetchone()[0]
            c.close()
        except Exception as e:
            _err(f"FTS count failed (continuing): {e}")

    return chroma_n, fts_n


def _active_memory_repo() -> Optional[Path]:
    for r in MEMORY_REPOS:
        if (r / ".git").is_dir():
            return r
    return None


def _changed_topic_files(repo: Path, session_start_ts: float) -> list[tuple[str, Path]]:
    """
    Return [(status, path), ...] for topic files changed in the session window.
    Status is 'A' (added/untracked), 'M' (modified).
    """
    rc, out, _ = _run(["git", "status", "--porcelain", "-uall"], cwd=repo)
    if rc != 0:
        return []
    results: list[tuple[str, Path]] = []
    for line in out.splitlines():
        if len(line) < 3:
            continue
        status = line[:2].strip()
        rel = line[3:]
        abs_path = (repo / rel).resolve()
        # Only care about topic files
        if "topics/" not in str(abs_path):
            continue
        # Filter by mtime
        try:
            if abs_path.stat().st_mtime < session_start_ts:
                continue
        except FileNotFoundError:
            continue
        if status in ("A", "??", "M", "MM", "AM"):
            canon = "A" if status in ("A", "??", "AM") else "M"
            results.append((canon, abs_path))
    return results


# ---------- Execution ----------


def _delete_file(p: Path, dry_run: bool) -> bool:
    if not p.exists():
        return False
    if dry_run:
        return True
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True
    except Exception as e:
        _err(f"Failed to delete {p}: {e}")
        return False


def _wipe_memory(conv_id: str, dry_run: bool) -> tuple[int, int]:
    if dry_run:
        return _count_memory_chunks(conv_id)

    chroma_deleted = 0
    fts_deleted = 0

    # ChromaDB: find ids with conv_id=<x>, delete.
    # We use the memory_server's own python interface to stay consistent with its
    # chunk-id scheme. Bypassing the server and editing sqlite directly is fragile.
    try:
        import chromadb  # noqa

        sys.path.insert(0, str(HOME / "code"))
        # Use lower-level direct client, NOT memory_server (single-instance lock)
        client = chromadb.PersistentClient(path=str(MEMORY_DATA / "chroma"))
        coll = client.get_or_create_collection("conversations", metadata={"hnsw:space": "cosine"})  # noqa: E501
        # Query ids by metadata
        got = coll.get(where={"conv_id": conv_id}, limit=100000)
        ids = got.get("ids") or []
        if ids:
            coll.delete(ids=ids)
            chroma_deleted = len(ids)
    except Exception as e:
        _err(f"ChromaDB delete failed (continuing): {e}")

    # FTS5
    try:
        c = sqlite3.connect(str(FTS_DB))
        c.execute("PRAGMA secure_delete = ON")
        cur = c.execute("DELETE FROM chunks WHERE conv_id=?", (conv_id,))
        fts_deleted = cur.rowcount
        c.execute("DELETE FROM indexed_convs WHERE conv_id=?", (conv_id,))
        c.execute("VACUUM")
        c.commit()
        c.close()
    except Exception as e:
        _err(f"FTS delete failed (continuing): {e}")

    return chroma_deleted, fts_deleted


def _prune_memory_md_references(repo: Path, removed_topic_paths: list[Path], dry_run: bool) -> int:  # noqa: E501
    mfile = repo / "MEMORY.md"
    if not mfile.exists() or not removed_topic_paths:
        return 0
    removed_names = {p.name for p in removed_topic_paths}
    lines = mfile.read_text().splitlines()
    kept: list[str] = []
    dropped = 0
    for line in lines:
        if any(f"topics/{name}" in line for name in removed_names):
            dropped += 1
            continue
        kept.append(line)
    if dropped and not dry_run:
        mfile.write_text("\n".join(kept) + "\n")
    return dropped


# ---------- Main ----------


def main() -> int:
    ap = argparse.ArgumentParser(description="Wipe current session's artefacts.")
    ap.add_argument("--session-id", help="Explicit session id (JSONL stem).")
    ap.add_argument("--dry-run", action="store_true", help="Preview only; don't delete.")  # noqa: E501
    ap.add_argument("--yes-all", action="store_true", help="Delete every detected topic change without prompting.")  # noqa: E501
    args = ap.parse_args()

    try:
        conv_id, jsonl = _detect_session_id(args.session_id)
    except Exception as e:
        _err(str(e))
        return 2

    session_start_ts = jsonl.stat().st_ctime

    # --- Build plan ---
    manifest_files = _manifest_files(conv_id)
    tmp_files = _tmp_artefacts(conv_id)
    shell_snaps = _shell_snapshots()
    chroma_n, fts_n = _count_memory_chunks(conv_id)

    repo = _active_memory_repo()
    topic_changes = _changed_topic_files(repo, session_start_ts) if repo else []

    _print("=" * 64)
    _print(f"secure-delete plan — session {conv_id}")
    _print("=" * 64)
    _print(f"JSONL:            {jsonl}")
    _print(f"Manifest files:   {len(manifest_files)}")
    for p in manifest_files[:10]:
        _print(f"                    {p}")
    if len(manifest_files) > 10:
        _print(f"                    ... and {len(manifest_files) - 10} more")
    _print(f"Tmp artefacts:    {len(tmp_files)}")
    for p in tmp_files:
        _print(f"                    {p}")
    _print(f"Shell snapshots:  {len(shell_snaps)}")
    _print(f"ChromaDB chunks:  {chroma_n}")
    _print(f"FTS5 chunks:      {fts_n}")
    _print(f"Memory repo:      {repo if repo else '(none detected)'}")
    _print(f"Topic changes:    {len(topic_changes)}")
    for status, p in topic_changes:
        _print(f"                    [{status}] {p.name}")
    _print("=" * 64)

    if args.dry_run:
        _print("DRY RUN — nothing deleted.")
        return 0

    if not args.yes_all:
        if not _confirm("Proceed with non-topic deletions (JSONL, tmp, snapshots, memory chunks)?"):  # noqa: E501
            _print("Aborted.")
            return 1

    # --- Execute non-topic deletions ---
    deleted_files: list[Path] = []

    for p in manifest_files:
        if _delete_file(p, False):
            deleted_files.append(p)

    for p in tmp_files:
        if _delete_file(p, False):
            deleted_files.append(p)

    for p in shell_snaps:
        if _delete_file(p, False):
            deleted_files.append(p)

    # JSONL last — without it the session is effectively gone from Claude Code's POV
    if _delete_file(jsonl, False):
        deleted_files.append(jsonl)

    chroma_deleted, fts_deleted = _wipe_memory(conv_id, False)

    # --- Topic files: per-item prompt ---
    topic_removed: list[Path] = []
    topic_kept: list[Path] = []

    for status, p in topic_changes:
        if args.yes_all:
            decision = "d"
        else:
            _print()
            _print(f"Topic change: [{status}] {p.name}")
            _print(f"  Path: {p}")
            _print("  [d]elete   [k]eep   [s]how first 20 lines")
            while True:
                ans = input("  ? ").strip().lower()
                if ans == "s":
                    try:
                        _print("  " + "\n  ".join(p.read_text().splitlines()[:20]))
                    except Exception as e:
                        _err(f"Show failed: {e}")
                    continue
                if ans in ("d", "k"):
                    decision = ans
                    break
        if decision == "d":
            if _delete_file(p, False):
                topic_removed.append(p)
        else:
            topic_kept.append(p)

    md_refs_removed = _prune_memory_md_references(repo, topic_removed, False) if repo else 0  # noqa: E501

    # --- Report ---
    _print()
    _print("=" * 64)
    _print("DELETION REPORT")
    _print("=" * 64)
    _print(f"Files deleted:       {len(deleted_files)}")
    _print(f"  JSONL:             {'yes' if jsonl in deleted_files else 'no'}")
    _print(f"  Manifest entries:  {sum(1 for p in manifest_files if p in deleted_files)}")  # noqa: E501
    _print(f"  Tmp artefacts:     {sum(1 for p in tmp_files if p in deleted_files)}")
    _print(f"  Shell snapshots:   {sum(1 for p in shell_snaps if p in deleted_files)}")
    _print(f"ChromaDB chunks:     {chroma_deleted} deleted")
    _print(f"FTS5 chunks:         {fts_deleted} deleted")
    _print(f"Topic files deleted: {len(topic_removed)}")
    for p in topic_removed:
        _print(f"                      {p.name}")
    _print(f"Topic files kept:    {len(topic_kept)}")
    for p in topic_kept:
        _print(f"                      {p.name}")
    _print(f"MEMORY.md lines pruned: {md_refs_removed}")
    _print("=" * 64)
    _print("Done. Recommend ending this session now — anything written from here")
    _print("becomes new residue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
