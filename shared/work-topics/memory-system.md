---
name: memory-system
description: Two-tier ChromaDB + FTS5 memory over conversation history, scope-aware bridge, MCP tools, and troubleshooting.
type: project
scope: shared
---

# Memory System — Context & Troubleshooting

> **Single source of truth** for how Claude's memory works.

## Architecture (two-tier)

A two-tier search system over all conversation history, exposed as an MCP server.

- **ChromaDB** — local vector DB with built-in ONNX embeddings (`all-MiniLM-L6-v2`, no API key). Used for semantic search.
- **SQLite FTS5** — keyword search for IPs, error messages, exact identifiers, code symbols.

```
~/.claude.json (user scope)
  └── mcpServers.memory_work → stdio → python3 work_memory_server.py
                                       (registered by claude-bridge/tools/work_setup.sh)

~/.claude/settings.json (user scope)
  └── hooks.SessionEnd → bash work_memory_index.sh → indexes current session on exit

claude-bridge/tools/
  ├── work_memory_server.py     # FastMCP server (work-side fork)
  ├── work_memory_index.sh      # SessionEnd hook script
  └── (data dir set by CONV_MEMORY_DATA_DIR env var, default ~/.claude/work_memory_data)
      ├── chroma/               # ChromaDB vector DB
      ├── fts.db                # SQLite FTS5 database
      └── auto_index.log        # Hook indexing log
```

**Data source:** all `.jsonl` conversation transcripts under `~/.claude/projects/` (recursively).

**Chunking:** messages split at ~500 chars with 50-char overlap. Each chunk indexed in both backends with metadata: `conv_id`, `date`, `topic`, `role`, `msg_idx`, `chunk_idx`.

**Topic auto-detection:** first user message scanned for keywords → routed to a topic, else `general`. Subagent conversations tagged `subagents`.

## Scope frontmatter and the bridge filter

Topic files carry a frontmatter `scope:` field:

- `scope: shared` — safe to surface in any context, no personal/sensitive content.
- `scope: personal` — only surfaced inside the personal-side environment.
- `scope: work` — only surfaced inside the work-side environment.

The MCP server filters search results by the running environment's scope. A topic file authored as `personal` will not appear in work-side searches even if it lives in a synced repo. This is the bridge filter — it lets a single conversation history be queried by both sides without leaking content across the boundary.

When migrating a topic between sides, rewrite `scope:` and strip identifiers — do not rely on the filter as the only line of defence.

## Auto-Indexing (SessionEnd Hook)

When any Claude Code session ends, a SessionEnd hook fires:

1. Claude Code passes `{session_id, transcript_path, cwd}` as JSON on stdin.
2. `auto_index.sh` extracts fields, calls `index_conversation()`.
3. Logs to `data/auto_index.log`.
4. Always outputs `{"continue": true}` — never blocks session exit.
5. 30-second timeout — fails silently on errors.

**Manual re-index** is still available for bulk operations:
```bash
# Via MCP tool (in a Claude session):
index_all_transcripts()

# Via command line:
python3 -c "from memory_server import index_all_transcripts; print(index_all_transcripts())"
```

## Stale Re-Indexing

Conversations can grow after initial indexing (context compaction, session resumption). The `indexed_convs` table tracks `file_size` at index time. During `index_all_transcripts`:

- If a file has grown by >10%, it gets re-indexed.
- `force_reindex=True` re-indexes everything regardless.
- The auto_index hook always indexes the current session (fresh or updated).

## MCP Tools

| Tool | Backend | Use Case | Example |
|------|---------|----------|---------|
| `search_memory` | ChromaDB | Semantic / fuzzy questions | "what did we try for the build cache?" |
| `search_exact` | SQLite FTS5 | IPs, error messages, exact identifiers | env-var names, error codes |
| `index_conversation` | Both | Index a single `.jsonl` file | After a long session |
| `index_all_transcripts` | Both | Bulk index all new `.jsonl` files | Periodic maintenance |
| `get_memory_stats` | Both | Show DB sizes, chunk counts | Checking health |

### search_memory parameters
- `query` (str) — natural language.
- `n_results` (int, default 5, max 20).
- `topic` (str, optional) — filter to a specific topic.
- `compact` (bool, default False) — if True, returns metadata + 80-char preview only (~80 tokens vs ~500).

### search_exact parameters
- Same shape. Query supports FTS5 syntax: `"exact phrase"`, `term*` (prefix), `term1 AND term2`, `term1 OR term2`, `NOT term`.

### Compact Mode
Both search tools accept `compact=True` to return concise results:
```
1. [0.85] abc123def456 | 2026-02-28 | general | user: First 80 chars of the chunk text here...
2. [0.72] def789abc012 | 2026-02-27 | general | assistant: Another result preview...
```
Use compact mode for initial scanning, then fetch full results for interesting hits.

## Three query shapes, three tools

- Exploration ("have we touched this?") → `search_memory` (semantic).
- Retrieval ("what exactly did we decide about X?") → `search_exact` (FTS5) when the query has an identifier.
- Synthesis ("why does this keep failing?") → `/deep-context --synthesise`. Never answer a synthesis question with a single semantic search.

## Dependencies
- `chromadb>=1.5.0`
- `fastmcp>=3.0.0`
- Python 3.11

## Troubleshooting

### MCP server not showing tools
- Tools only load at session start. If the server was just registered, restart Claude Code.
- Check registration: `claude mcp list | grep memory`.
- Check config: inspect the `mcpServers.memory` block in `~/.claude.json`.

### Server won't start / import error
```bash
python3 -c "from memory_server import mcp; print('OK')"
python3 -c "import chromadb; import fastmcp; print('deps OK')"
# If deps missing:
pip3 install "chromadb>=1.5.0" "fastmcp>=3.0.0"
```

### Auto-index hook not firing
```bash
# Default work-side data dir is ~/.claude/work_memory_data, overridable via
# CONV_MEMORY_DATA_DIR. Indexer script ships in the claude-bridge repo.
cat /tmp/work_memory_index.log
bash ~/code/claude-bridge/tools/work_memory_index.sh
# Confirm SessionEnd hook is registered in ~/.claude/settings.json (work_setup.sh wires it).
```

### Search returns no results
- Check stats by importing the work memory module and calling `get_memory_stats()`.
- If chunk count is 0, re-index by calling `index_all_transcripts(force_reindex=True)` on the same module.

### ChromaDB errors / corruption
The data directory is fully rebuildable from the `.jsonl` transcripts — nothing is lost.
```bash
rm -rf ~/.claude/work_memory_data   # or whatever CONV_MEMORY_DATA_DIR points to
bash ~/code/claude-bridge/tools/work_memory_index.sh
```

### FTS5 syntax errors
The server auto-wraps unparseable queries in quotes for literal matching. Complex boolean queries need valid FTS5 syntax:
- Phrase: `"exact phrase"`
- AND: `term1 AND term2`
- OR: `term1 OR term2`
- NOT: `NOT term`
- Prefix: `term*`

## Operating principles

- **Topics are the only curated layer.** On the work side, the curated topics live in `~/.claude/projects/<id>/memory/topics/`, populated by `work_setup.sh` Section L from the controlplane's `shared/work-topics/`. ChromaDB / FTS5 / pre-stripped corpus are derived; regenerate, don't hand-edit.
- **Raw JSONLs archived forever.** `~/.claude/projects/*.jsonl` is never deleted, compressed, or summarised for storage. Future synthesis needs the history.
- **Functional health checks.** Memory health is "an actual `search_memory` query returns results", not "the process is running". Wire that into the SessionStart validator.

## Registration

The work-side memory MCP is registered automatically by `work_setup.sh` (Section B). It writes the entry into `~/.claude.json` as:
```
mcpServers.memory_work → stdio → /opt/homebrew/bin/python3.11 work_memory_server.py
```
No manual `claude mcp add` is required. If the server is missing, re-run `work_setup.sh` and relaunch Claude Code.
