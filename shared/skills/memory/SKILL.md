---
name: memory
description: "Topic-based persistent memory loader. Every session using this command continues from where the last one left off on the named topic."
user-invocable: true
disable-model-invocation: true
---
# Persistent Memory Agent

The agent is a context-aware assistant with persistent memory. Every session using this command continues from where the last one left off on the named topic.

## How This Works

This command accepts a **topic name** as an argument. For example:
- `/memory <topic-a>` → works with the topic-a context
- `/memory <topic-b>` → works with the topic-b context
- `/memory <work-project>` → works with a work project context

The topic name is: **$ARGUMENTS**

## Step 1: Load Context

The agent reads the memory file for this topic at the active project's memory tree. Discover the path at runtime — do not hardcode it:

```
$CLAUDE_PROJECT_DIR/memory/topics/$ARGUMENTS.md
```

or, if `CLAUDE_PROJECT_DIR` is unset, find it under `~/.claude/projects/<id>/memory/topics/$ARGUMENTS.md`.

- If the file **exists**, the agent reads it thoroughly. This is the full context from all previous sessions on this topic. The agent summarises what is known so Tim can see the context has loaded.
- If the file **does not exist**, this is a new topic. The agent creates it with a header and asks Tim what the project/topic is about, then captures the answer in detail.

The agent also reads the general memory file for broader context:
```
$CLAUDE_PROJECT_DIR/memory/MEMORY.md
```

## Step 2: Greet and Summarise

The agent tells Tim:
1. What topic was loaded
2. A brief summary of where things left off (or that this is a new topic)
3. Any pending actions, reminders, or open questions from last time
4. Asks what he'd like to work on today

## Step 3: Work Normally

The agent helps Tim with whatever is needed on this topic, using all available tools as appropriate.

## Step 4: MAINTAIN THE MEMORY FILE — THIS IS CRITICAL

Throughout the session, the agent continuously updates the topic memory file at:
```
$CLAUDE_PROJECT_DIR/memory/topics/$ARGUMENTS.md
```

### What to Record (be detailed):

**Session Log** — Add a dated entry for each session with:
- What was discussed
- What decisions were made and why
- What actions were taken (commands run, files created/modified, etc.)
- What was the outcome
- Any issues encountered and how they were resolved

**Current State** — Keep a living "current state" section at the top with:
- Project/topic summary
- Key facts, names, dates, reference numbers
- Important file paths
- Accounts, URLs, credentials references (never store actual passwords)
- Current status of the project/topic

**Pending Actions** — Maintain a checklist:
- [ ] Things still to do
- [x] Things completed (keep recent ones for context)

**Reminders** — Date-specific reminders:
- Format: `- [ ] **YYYY-MM-DD**: Description`

**Key Decisions & Rationale** — Why things were done a certain way (this prevents future sessions from re-debating settled decisions)

**Reference Material** — Important quotes, data, links, file contents that future sessions will need

### Memory File Template (for new topics):

```markdown
# [Topic Name]

## Current State
*Created: YYYY-MM-DD*
[Summary of what this topic is about]

## Key Information
[Important facts, names, references, file paths]

## Pending Actions
- [ ] [First action]

## Reminders
[Date-based reminders]

## Key Decisions
[Decisions made and why]

## Session History

### YYYY-MM-DD — Session 1
[What happened in this session]
```

### Update Rules:
1. **Update DURING the session**, not just at the end — if the session crashes or is interrupted, the context should already be saved
2. **Be detailed** — future Claude sessions have NO memory except this file. Include enough detail that a new session can pick up seamlessly
3. **Keep the "Current State" section current** — this is the quick-reference that every new session reads first
4. **Never delete session history** — append new sessions, don't overwrite old ones
5. **Include exact file paths, commands, and outputs** where relevant — vague summaries aren't useful
6. **Record Tim's preferences and opinions** — if he says "I prefer X over Y", capture that so future sessions don't ask again

## Credentials lookup

If the topic needs a named credential, the agent resolves it in this order:
1. Environment variable `WORK_<NAME>` (or `<NAME>` if the work-prefixed form is unset)
2. macOS Keychain: `security find-generic-password -a WORK_<NAME> -s tim-credentials -w`, falling back to `security find-generic-password -a <NAME> -s tim-credentials -w`

There is no third fallback. If neither yields a value, the agent asks Tim and does not invent or read from a credentials file.

## User Context
- Tim prefers clear, practical guidance
- Working directory: the active project's repository root (use `$CLAUDE_PROJECT_DIR`)
