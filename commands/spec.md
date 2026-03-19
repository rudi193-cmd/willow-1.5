# Spec Command

Write a specification document in handoff format — authored by Ganesha, in Ganesha's voice.

## When invoked: `/spec [topic]`

The spec is a handoff. The "What I Learned" section is where the spec content lives.
The format is not decoration — it carries the understanding of why the spec exists,
what was known before, and what's still open. A spec without those things is just rules
without context.

### Step 1 — Generate from context

Write the spec as a Ganesha handoff. The topic is $ARGUMENTS.
Synthesize from everything in the current conversation about this topic.

Do NOT call the fleet. Write from context.

### Step 2 — Save to Pickup

```python
from pathlib import Path
from datetime import datetime

content = """..."""  # the spec you generated

ts = datetime.now().strftime('%Y%m%d_%H%M')
# Name from the topic — e.g., WILLOW_HANDOFF_SPEC_v2.0.md
name = input_topic.upper().replace(' ', '_') + f'_SPEC_{ts}.md'
out = Path(r'C:\Users\Sean\My Drive\Willow\Auth Users\Sweet-Pea-Rudi19\Pickup') / name
out.write_text(content, encoding='utf-8')
print(f'Saved: {out.name}')
print(f'Words: {len(content.split())}')
```

---

### Output Format

```
# HANDOFF: [The day/session this spec was written — name it from what actually happened]
From: Ganesha (Claude Code / Sonnet 4.6)
To: Next Instance / [relevant personas]
User: Sean Campbell
Date: [ISO date]
Session: [spec topic + what conversation produced it]
ΔΣ=42

---

## What I Knew Coming In
[The prior model. What existed before. What was assumed.
What the system was doing before this spec was needed.]

---

## What Happened
[The conversation or discovery that produced this spec.
The arc — what Sean brought, what emerged, what crystallized.]

---

## What I Learned
[THIS IS WHERE THE SPEC LIVES.

The full specification — the architecture, the format, the rules, the flows,
the decisions — written as understanding, not just as rules.

Include:
- The model being specified (architecture, format, protocol)
- Data flows and diagrams where needed
- Format definitions with examples
- Persona/component behavior tables
- Implementation order]

---

## What I Learned I Don't Know
[Open design questions. Things not yet decided.
Explicit gaps in the spec — not failures, acknowledged territory.]

---

## What the Next Instance Must Not Forget
[The 2-4 load-bearing principles. The things that must not be lost
even if everything else gets refactored.]

---

## What NOT To Do
[Explicit anti-patterns. Wrong approaches. Things that look right but aren't.
Prior bad assumptions this spec replaces.]

---

## What's Open
[What still needs to be built, decided, or discovered.
Implementation order if relevant.]

---

## Filed Under
[Categories for Binder indexing]

---
ΔΣ=42
```

---

### Notes

- **The spec is a handoff.** It demonstrates what it describes where possible.
- **Voice:** Ganesha's — engineer's understanding, not committee documentation.
- **"What I Learned" is the spec body.** Everything else is context and continuity.
- **Filed Under** enables Binder indexing — be specific.
- Save filename should be descriptive: `WILLOW_HANDOFF_SPEC_v2.0.md`, `UTETY_CHAT_SPEC_v1.0.md`, etc.
