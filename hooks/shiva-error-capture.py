#!/usr/bin/env python3
"""
PostToolUse Hook: Error Capture
================================
Fires after Bash tool calls. If stderr/error detected, logs to Postgres
tool_errors table for recall and pattern detection.

Input (stdin): PostToolUse event JSON
Exit 0: always (never blocks)
"""

import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow")

MIN_ERROR_LEN = 10


def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    tool_name = event.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = event.get("tool_input", {})
    tool_response = event.get("tool_response", {})

    error_text = ""

    if isinstance(tool_response, dict):
        content = tool_response.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if "Exit code" in str(inner) or "error" in str(inner).lower():
                        error_text = str(inner)[:600]
        elif isinstance(content, str):
            if "Exit code" in content or "Traceback" in content or "Error" in content:
                error_text = content[:600]

    stderr = tool_response.get("stderr", "") or ""
    if stderr and len(stderr) > MIN_ERROR_LEN:
        error_text = (error_text + "\n" + stderr)[:600].strip()

    if not error_text or len(error_text.strip()) < MIN_ERROR_LEN:
        sys.exit(0)

    # Skip known noise
    noise = ["No such file or directory: '/s'", "ganesha_sessions.db not found"]
    if any(n in error_text for n in noise):
        sys.exit(0)

    command = (tool_input.get("command") or tool_input.get("description") or "")[:200]
    session_id = event.get("session_id", "unknown")[:32]
    now = datetime.now(timezone.utc).isoformat()

    try:
        from core.db import get_connection
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_errors (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                command TEXT,
                error_text TEXT NOT NULL,
                captured_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO tool_errors (session_id, tool_name, command, error_text, captured_at) VALUES (%s, %s, %s, %s, %s)",
            (session_id, tool_name, command, error_text.strip(), now)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
