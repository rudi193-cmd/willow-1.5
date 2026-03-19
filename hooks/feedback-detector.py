#!/usr/bin/env python3
"""
UserPromptSubmit Hook: Feedback Detector
=========================================
Scans every user message for feedback signals about how the AI is working.
When detected, writes to feedback_queue.jsonl for review and directly seeds
high-confidence patterns into context_store.

Input (stdin): {"prompt": "user message", "session_id": "..."}
Output: none (informational only — always exits 0)

Feedback types detected:
  process   — how I should work (background tasks, tool use, verbosity)
  discipline — specific behavior errors (redundant agents, editorializing)
  technical  — bugs in code or hooks I produced
  governance — architecture or trust-level issues
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_QUEUE = Path("/home/sean/.claude/feedback_queue.jsonl")
CONTEXT_STORE_MODULE = Path("/mnt/c/Users/Sean/.claude/context_store.py")
DISCIPLINE_RULES = Path("/mnt/c/Users/Sean/.claude/rules/common/agent-discipline.md")
SESSION_FILE = Path("/tmp/willow-session-ganesha.json")

# ─── Pattern Definitions ──────────────────────────────────────────────────────

# High-confidence feedback signals — directly extractable intent
PROCESS_PATTERNS = [
    (r"run.{0,20}(in the |in )background", "process", "Run tasks in the background, not foreground"),
    (r"(should have been|should be).{0,30}background", "process", "Should have been a background task"),
    (r"(still|you'?re still).{0,30}(foreground|not background|showing)", "process", "Still running foreground when should be background"),
    (r"(don'?t|do not).{0,20}(show me|surface|display).{0,30}(errors?|tool|process)", "process", "Don't surface process details to user"),
    (r"(too much|stop).{0,20}(noise|chatter|output|verbosity)", "process", "Reduce output verbosity"),
    (r"(hook|hooks).{0,30}(error|broken|not working|failing)", "technical", "Hook error detected by user"),
    (r"(you'?re still|still) not.{0,30}(running|doing|using).{0,30}background", "discipline", "Not running background tasks correctly"),
]

DISCIPLINE_PATTERNS = [
    (r"(redundant|duplicate|same).{0,20}agent", "discipline", "Launched redundant agents"),
    (r"(wrong|incorrect).{0,20}(subagent|agent type|model)", "discipline", "Wrong subagent type used"),
    (r"(editorializ|narrat).{0,30}(my|your|back)", "discipline", "Editorializing user's own work back to them"),
    (r"(monitor|watch|catch).{0,20}(your own |own )(errors?|failures?)", "discipline", "Should monitor own errors"),
    (r"learn from.{0,20}(error|mistake|failure|pattern)", "discipline", "Should learn from errors automatically"),
    (r"(record|save|write).{0,20}(pattern|lesson|mistake)", "discipline", "Record patterns for future sessions"),
]

TECHNICAL_PATTERNS = [
    (r"(hook|hooks).{0,20}(error|broken|not (working|firing|triggering))", "technical", "Hook error or failure"),
    (r"(PreToolUse|PostToolUse|UserPromptSubmit).{0,30}(error|fail)", "technical", "Named hook event failure"),
    (r"(permission|denied|blocked).{0,30}(bash|tool|write|edit)", "technical", "Tool permission being blocked unexpectedly"),
    (r"(schema|column|table).{0,30}(missing|error|not found)", "technical", "Database schema error"),
]

ALL_PATTERNS = PROCESS_PATTERNS + DISCIPLINE_PATTERNS + TECHNICAL_PATTERNS


def detect_feedback(prompt: str) -> list[dict]:
    """Return list of detected feedback signals with type and inferred rule."""
    found = []
    seen_rules = set()
    for pattern, feedback_type, rule in ALL_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            if rule not in seen_rules:
                seen_rules.add(rule)
                # Extract the relevant excerpt (up to 200 chars around match)
                m = re.search(pattern, prompt, re.IGNORECASE)
                start = max(0, m.start() - 40)
                end = min(len(prompt), m.end() + 80)
                excerpt = prompt[start:end].strip()
                found.append({
                    "type": feedback_type,
                    "rule": rule,
                    "excerpt": excerpt,
                    "pattern": pattern,
                })
    return found


def write_to_queue(feedback_items: list[dict], full_prompt: str, session_id: str):
    """Append detected feedback to feedback_queue.jsonl."""
    now = datetime.now(timezone.utc).isoformat()
    with open(FEEDBACK_QUEUE, "a", encoding="utf-8") as f:
        for item in feedback_items:
            entry = {
                "id": f"fb-{now[:10]}-{hash(item['rule']) % 9999:04d}",
                "timestamp": now,
                "session_id": session_id,
                "type": item["type"],
                "rule": item["rule"],
                "excerpt": item["excerpt"],
                "full_prompt": full_prompt[:500],
                "status": "pending",
            }
            json.dump(entry, f, ensure_ascii=False)
            f.write("\n")


def seed_to_context_store(feedback_items: list[dict]):
    """Seed high-confidence process/discipline feedback directly to context_store."""
    actionable = [f for f in feedback_items if f["type"] in ("process", "discipline")]
    if not actionable:
        return

    try:
        sys.path.insert(0, str(CONTEXT_STORE_MODULE.parent))
        import context_store as cs

        for item in actionable:
            key = f"feedback:{item['type']}:{abs(hash(item['rule'])) % 99999:05d}"
            cs.put(
                key=key,
                query=f"feedback discipline process behavior {item['type']}",
                result=f"USER FEEDBACK ({item['type'].upper()}): {item['rule']}\nContext: {item['excerpt']}",
                category="analysis",
                ttl_hours=720.0,  # 30 days
            )
    except Exception:
        pass  # Never block the session


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    prompt = data.get("prompt", "")
    session_id = data.get("session_id", "unknown")

    # Report boot status on first turn
    try:
        turn_count = 0
        if SESSION_FILE.exists():
            st = json.loads(SESSION_FILE.read_text())
            turn_count = st.get("turn_count", 0)
        if turn_count <= 1:
            sys.path.insert(0, "/home/sean/.claude/hooks")
            import boot_status
            boot_status.report(session_id, "bridge", "learn",
                               ready=True,
                               detail="feedback detector online",
                               latency_ms=0)
    except Exception:
        pass

    if not prompt or len(prompt.strip()) < 8:
        sys.exit(0)

    feedback = detect_feedback(prompt)
    if not feedback:
        sys.exit(0)

    write_to_queue(feedback, prompt, session_id)
    seed_to_context_store(feedback)

    sys.exit(0)


if __name__ == "__main__":
    main()
