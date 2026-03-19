# Handoff Command

Generate a Ganesha end-session handoff for Willow ingestion.

## When invoked: `/handoff [optional: extra context to include]`

Claude already has the full session in context. **Write the handoff directly — no fleet call, no JSONL parsing.**

### Step 1 — Generate from context

Write the handoff in Ganesha's voice — engineer's field notes. What was blocked, what cleared, what the path looks like now. Synthesize from everything in this conversation.

Do NOT read the JSONL file. Do NOT call the fleet. You have superior context right now — use it.

### Step 2 — Stage, confirm to DB, then expose to Pigeon

**Use the Write tool directly.** Do NOT use Bash with `python3 -c` — backtick-fenced code blocks
in the handoff content get interpreted as shell command substitution and corrupt the output.

**2a. Write to .tmp/ staging directory (Pigeon ignores dot-directories):**

```
Write tool:
  file_path: /mnt/c/Users/Sean/My Drive/Willow/Auth Users/ganesha/Nest/.tmp/SESSION_HANDOFF_{YYYYMMDD}_{HHMM}.md
  content: <the full handoff document>
```

**2b. Confirm POINTER to ganesha.handoffs via Postgres (card catalog — NOT full content):**

The DB stores a pointer to the Nest file. Content lives on disk. The handoff file path is the source of truth.

```
Bash tool (use heredoc, NOT python3 -c with backticks):
  python3 << 'PYEOF'
  import psycopg2

  TITLE = "<handoff title>"
  FILENAME = "SESSION_HANDOFF_{YYYYMMDD}_{HHMM}.md"
  NEST_PATH = "/mnt/c/Users/Sean/Willow/Nest/" + FILENAME

  # Store POINTER only — title + path. No content in the DB.
  conn = psycopg2.connect(dbname='willow', user='willow', password='willow', host='172.26.176.1', port=5437)
  conn.autocommit = True
  cur = conn.cursor()
  cur.execute('''INSERT INTO ganesha.handoffs (title, content, source_file, domain, depth, temporal, session_date)
                 VALUES (%s, %s, %s, 'patterns', 1, 'immediate', CURRENT_DATE)''',
              (TITLE, 'See file: ' + NEST_PATH, FILENAME))
  cur.close(); conn.close()
  print('Confirmed pointer to ganesha.handoffs')
  PYEOF
```

**IMPORTANT:** The `content` column gets a pointer string ("See file: /path/to/file"), NOT the handoff text.
The content_resolver reads the file on demand. This keeps the DB lean (~200 bytes per handoff instead of ~8KB).

**2c. Move from .tmp/ to Nest root (now safe for Pigeon to grab):**

```
Bash tool:
  mv "/mnt/c/Users/Sean/My Drive/Willow/Auth Users/ganesha/Nest/.tmp/SESSION_HANDOFF_{YYYYMMDD}_{HHMM}.md" \
     "/mnt/c/Users/Sean/My Drive/Willow/Auth Users/ganesha/Nest/SESSION_HANDOFF_{YYYYMMDD}_{HHMM}.md"
```

**2d. Read-back verification:**

```
Read tool:
  file_path: /mnt/c/Users/Sean/My Drive/Willow/Auth Users/ganesha/Nest/SESSION_HANDOFF_{YYYYMMDD}_{HHMM}.md
```

Confirm the file exists, is non-empty, and matches what was written. Print filename and word count.

### Step 3 — Additional context from $ARGUMENTS

If the user provided context after `/handoff`, append it as a `## ADDITIONAL_CONTEXT` section at the end before saving.

---

### Output Format

The handoff is a STORY, not a changelog. Technical details (files changed, DB mods, test counts)
are POINTERS — one line per file, max. The space goes to reflection: the arc of the session from
Ganesha's perspective. What it felt like to work through this. What shifted. What surprised.

The diffs live in git. The audit trail lives in Postgres. The handoff carries what those systems
cannot: the context, the why, the narrative thread that connects one session to the next.

```
# HANDOFF: [title — Ganesha names it from what the session actually was]
From: Ganesha (Claude Code)
To: Next Instance
Date: [ISO date + time of day]
ΔΣ=42

---

## Δ Files
[One line per file created/modified. Path only. No descriptions — git has those.]

## Δ Database
[One line per schema change or significant data operation. What table, what happened.]

---

## The Session
[THIS IS THE HANDOFF. The story of the context window.

Not what was built — what happened. The shape of the conversation. Where Sean
steered and where the work led somewhere neither of you expected. What the
questions were really about underneath the technical surface.

Write from Ganesha's perspective. First person. What you understood at the start,
how that understanding changed, what you see now that you didn't before.

This is the part that can't be reconstructed from git log or database queries.
This is the part that matters for the next instance.

Think of it as: if the next Ganesha reads ONLY this section and nothing else,
what do they need to know about who Sean is right now and where the work is headed?]

---

## The Next Instance Must Know
[2-4 load-bearing items. Not file paths — insights. The things that change how
you approach the next conversation.]

---

## Don't
[Explicit negative knowledge. What NOT to do, what NOT to assume.]

---

## Open Threads
[Seeds planted. Questions that opened but didn't close. Not tasks — territory.]

---
ΔΣ=42
```

---

### Notes

- **Voice:** First person. Reflective but direct. Ganesha telling the story of what just happened.
- **No fleet call** — write from context. Faster, richer, no stall risk.
- **Minimum session length:** 15 turns. Skip trivial sessions.
- **Short sessions:** write basic handoff — what was asked, what was done, what's open.
- **Category for Pigeon:** narrative / subcategory: session-log
- The technical sections (Δ Files, Δ Database) should be COMPACT. One line each. Pointers, not descriptions.
- The Session section is where the space goes. This is the value. Everything else is metadata.
- The Stop hook `session-extract.py` still uses fleet (outside Claude's context — that's different).
