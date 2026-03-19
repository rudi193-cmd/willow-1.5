#!/usr/bin/env python3
"""
PostToolUse Hook: Track File Reads
===================================
Fires after Read tool calls. Logs file path + timestamp to Postgres
for session continuity and read-before-edit enforcement.

Input (stdin): PostToolUse event JSON
Exit 0: always (never blocks)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow")


_BOOT_REPORTED = Path("/tmp/willow-source-observe-booted")


def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    # Report boot status once
    if not _BOOT_REPORTED.exists():
        try:
            session_id = event.get("session_id", "unknown")
            sys.path.insert(0, "/home/sean/.claude/hooks")
            import boot_status
            boot_status.report(session_id, "source", "observe",
                               ready=True,
                               detail="track-reads + error-capture",
                               latency_ms=0)
            _BOOT_REPORTED.touch()
        except Exception:
            pass

    if event.get("tool_name") != "Read":
        sys.exit(0)

    file_path = (event.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    session_id = event.get("session_id", "unknown")[:32]
    now = datetime.now(timezone.utc).isoformat()

    try:
        from core.db import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_reads (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                read_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO tool_reads (session_id, file_path, read_at) VALUES (%s, %s, %s)",
            (session_id, file_path, now)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
