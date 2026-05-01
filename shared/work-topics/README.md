# shared/work-topics/

Sanitised topic-memory files that the work-laptop Claude installs into its
`memory/topics/` folder during `work_setup.sh`. Source-of-truth for the
behavioural rules and project context that both sides share.

## What lives here

- `feedback_*.md`: behavioural rules Tim has given Claude that apply equally
  on either side (no time estimates, no deferral phrases, response structure,
  plain-English translation, em-dash scope, verify-before-claiming, etc.).
- `work-bridge-phase2-plan.md`: shared phase-2 plan for the work integration.
  Both sides treat this as truth.
- `README.md`: this file.

## What does NOT live here

- Personal-side topic files: `printer.md`, `school-governors.md`,
  `autofaizal.md`, `bgt-date-monitor.md`, anything referencing home network
  topology, family/governance content, or 3D-printer hardware.
- Topic files that depend on personal-side LaunchAgents or daemons that are
  not yet ported to the work side. Those land slice-by-slice as the
  corresponding capability ships (slice H/I/J).

## How sync works

`tools/work_setup.sh` Section L (in the `claude-bridge` repo) copies every
file here except this `README.md` into every existing
`~/.claude/projects/<id>/memory/topics/` directory on the work laptop, then
re-runs the work-side memory indexer so the new content shows up in the
local memory MCP. It uses `rsync --checksum` so upstream revisions of a rule
will reach the work side even if a previous sync left a newer mtime. Safe
to re-run; runs every SessionStart on the work laptop.

If no project directories exist yet (first-ever setup), Section L seeds a
single one under `~/.claude/projects/-Users-<username>-code` so the next
session has the rules loaded.

## How to add a new topic to this set

1. Sanitise: strip personal IPs, paths, hardware refs, family/governance
   refs. Replace first-person ("I called this out") with third-person
   ("Tim called this out") so both sides read it the same way.
2. Frontmatter: `scope: shared`. Drop any `originSessionId` field that
   identifies a personal-side session.
3. Drop the file in this directory.
4. Add an entry under `work_topics:` in `WORK_ALLOWLIST.yaml`.
5. Open a PR through `/review`. The work side picks up the new file the
   next time `work_setup.sh` runs (SessionStart hook on work laptop).
