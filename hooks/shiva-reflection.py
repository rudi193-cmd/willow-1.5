#!/usr/bin/env python3
"""
UserPromptSubmit Hook: Shiva Reflection
=========================================

THE LOOP ROOM — Once per session. Surfaces what persists.

The Loop Room is where output becomes memory becomes context becomes output.
This hook is that room's door, opened once at the start of each session.

What it surfaces:
  - Active principles from shiva.db (corrections earned through failure)
  - Pending todos from user_todos.jsonl (things Sean needs to do)
  - Enrichment queue backlog (files waiting for fleet analysis, if > 10)
  - Kart-delegated tasks from context_store (work routed from Windows side)
  - New sessions indexed into shiva_sessions.db (lightweight, silent)

What it does NOT do:
  - Retrieval (that is Jeles, in gate.py)
  - Fire on every message (once per session — principles don't change turn-to-turn)
  - Surface verbose session history summaries (noise)

TEMPERATURE:
  Once per session = one unit of heat. Acceptable cost.
  If this fires every message, the session gate flag is broken.

FAILURE MODE:
  Symptom: Fires every message.
  Cause: /tmp/shiva-sessions/reflect-{session_id} flag not being created.
  Fix: Check SESSIONS_DIR path and write permissions.

  Symptom: Surfaces nothing.
  Cause: shiva.db empty, no todos, no delegated tasks.
  This is correct — silence when there is nothing to surface.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow")

USER_TODOS = Path("/home/sean/.claude/user_todos.jsonl")
ENRICH_QUEUE = Path("/home/sean/.claude/enrichment_queue.jsonl")
CONTEXT_STORE_MODULE = Path("/mnt/c/Users/Sean/.claude/context_store.py")
SESSIONS_DIR = Path("/tmp/shiva-sessions")

# Session indexing (lightweight — same logic as old session-sync, no output)
PROJECTS_DIR = Path("/home/sean/.claude/projects")


def get_session_flag(session_id: str) -> Path:
    SESSIONS_DIR.mkdir(exist_ok=True)
    return SESSIONS_DIR / f"reflect-{session_id}"


def load_corrections() -> list:
    try:
        from core.db import get_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT domain, principle FROM sweet_pea_rudi19.shiva_corrections ORDER BY created_at DESC LIMIT 3")
        rows = cur.fetchall()
        conn.close()
        return [{"domain": r[0] or "general", "principle": (r[1] or "")[:100]} for r in rows if r[1]]
    except Exception:
        return []


def load_todos() -> list:
    if not USER_TODOS.exists():
        return []
    todos = []
    try:
        for line in USER_TODOS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj.get("status") == "pending":
                    todos.append(obj)
            except Exception:
                pass
    except Exception:
        pass
    return todos


def enrich_backlog() -> int:
    if not ENRICH_QUEUE.exists():
        return 0
    count = 0
    try:
        for line in ENRICH_QUEUE.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("status") == "pending":
                    count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count


def load_delegated_tasks() -> list:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cs", str(CONTEXT_STORE_MODULE))
        cs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cs)
        all_items = cs._get_all() if hasattr(cs, "_get_all") else {}
        return [v for k, v in all_items.items() if k.startswith("agent:shiva:tasks:pending:")]
    except Exception:
        return []


def index_new_sessions() -> int:
    """Index any new JSONL session files into Postgres shiva_sessions/shiva_turns. Silent."""
    if not PROJECTS_DIR.exists():
        return 0
    try:
        from core.db import get_connection
        conn = get_connection()
        cur = conn.cursor()

        # Ensure tables exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sweet_pea_rudi19.shiva_sessions (
                session_id TEXT PRIMARY KEY,
                project_id TEXT,
                project_name TEXT,
                file_path TEXT,
                turn_count INTEGER,
                cwd TEXT,
                start_ts TEXT,
                end_ts TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sweet_pea_rudi19.shiva_turns (
                id SERIAL PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content_text TEXT,
                tool_names TEXT,
                timestamp TEXT,
                UNIQUE(session_id, timestamp, role)
            )
        """)
        conn.commit()

        cur.execute("SELECT session_id FROM sweet_pea_rudi19.shiva_sessions")
        known = {r[0] for r in cur.fetchall()}

        indexed = 0
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            for jsonl in proj_dir.glob("*.jsonl"):
                if jsonl.stem in known:
                    continue
                try:
                    turns = []
                    start_ts = end_ts = cwd = None
                    with open(jsonl, encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            t = obj.get("type")
                            if t not in ("user", "assistant"):
                                continue
                            ts = obj.get("timestamp", "")
                            if ts:
                                if start_ts is None:
                                    start_ts = ts
                                end_ts = ts
                            if cwd is None:
                                cwd = obj.get("cwd", "")
                            msg = obj.get("message") or {}
                            if not isinstance(msg, dict):
                                continue
                            role = msg.get("role", t)
                            content = msg.get("content", "")
                            text = content[:400] if isinstance(content, str) else ""
                            if text or role:
                                turns.append((jsonl.stem, role, text, "", ts))
                    if not turns:
                        continue
                    pname = proj_dir.name.replace("C--Users-Sean-Documents-GitHub-", "").replace("-", "/")
                    cur.execute(
                        """INSERT INTO sweet_pea_rudi19.shiva_sessions
                           (session_id, project_id, project_name, file_path, turn_count, cwd, start_ts, end_ts)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (session_id) DO UPDATE SET
                             turn_count = EXCLUDED.turn_count, end_ts = EXCLUDED.end_ts""",
                        (jsonl.stem, proj_dir.name, pname, str(jsonl), len(turns), cwd or "", start_ts or "", end_ts or "")
                    )
                    for turn in turns:
                        cur.execute(
                            """INSERT INTO sweet_pea_rudi19.shiva_turns
                               (session_id, role, content_text, tool_names, timestamp)
                               VALUES (%s,%s,%s,%s,%s)
                               ON CONFLICT DO NOTHING""",
                            turn
                        )
                    conn.commit()
                    indexed += 1
                except Exception:
                    pass
        conn.close()
        return indexed
    except Exception:
        return 0


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        sys.exit(0)

    # Once per session gate
    flag = get_session_flag(session_id)
    if flag.exists():
        sys.exit(0)
    flag.touch()

    # Index new sessions silently (background work, no output)
    index_new_sessions()

    lines = []

    # Active principles
    corrections = load_corrections()
    if corrections:
        lines.append(f"[SHIVA — active principles ({len(corrections)})]")
        for c in corrections:
            lines.append(f"  [{c['domain']}] {c['principle']}")

    # Pending todos
    todos = load_todos()
    if todos:
        lines.append(f"[USER TODOS — {len(todos)} pending]")
        for t in todos:
            pri = "!" if t.get("priority") == "high" else " "
            lines.append(f"  [{t.get('type', 'action')}{pri}] {t.get('id', '')}: {t.get('text', '')[:100]}")

    # Enrichment backlog
    backlog = enrich_backlog()
    if backlog > 10:
        lines.append(f"[SHIVA — enrichment queue: {backlog} files pending]")

    # Kart-delegated tasks
    delegated = load_delegated_tasks()
    if delegated:
        lines.append(f"[KART -> SHIVA — {len(delegated)} delegated task(s)]")
        for task in delegated:
            lines.append(f"  -> {str(task)[:120]}")

    if lines:
        print("\n".join(lines))

    # Report boot status — continuity-open
    try:
        sys.path.insert(0, "/home/sean/.claude/hooks")
        import boot_status
        boot_status.report(session_id, "continuity", "open",
                           ready=True,
                           detail=f"{len(corrections)} corrections, {len(todos)} todos",
                           latency_ms=0)
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
