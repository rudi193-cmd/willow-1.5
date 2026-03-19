#!/usr/bin/env python3
"""
PreToolUse Hook: Memory-to-DB Intercept
=========================================
Detects when Ganesha writes to memory flat files and dual-writes:
  1. ganesha.pending_memories (owned by Ganesha, awaits Sean's verification)
  2. Willow knowledge graph via MCP ingest (unless is_sensitive)

Does NOT block the write — allows it as fallback cache, but ensures the
canonical copy goes to Postgres.

Input (stdin): PreToolUse JSON with tool_name, tool_input
Exit 0: always allow (this hook augments, doesn't block)
"""

import json
import sys
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

WILLOW_URL = "http://localhost:8420"
MEMORY_PATHS = [
    "/home/sean/.claude/projects/-home-sean/memory/",
    "/home/sean/.claude/projects/",  # catch other project memory dirs
]
# Content that should NOT go to Willow knowledge (stays ganesha-only)
SENSITIVE_PREFIXES = [
    "feedback_",  # corrections are Ganesha's internal learning
    "system-",    # system internals
]
GANESHA_SCHEMA_INIT = """
CREATE TABLE IF NOT EXISTS ganesha.pending_memories (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    memory_name TEXT NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'project',
    content TEXT NOT NULL,
    source_file TEXT,
    proposed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    reviewed_at TIMESTAMP,
    willow_synced BOOLEAN DEFAULT FALSE,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON ganesha.pending_memories (status);
"""


def _get_session_id():
    """Try to read current session ID from env or temp file."""
    import os
    sid = os.environ.get("CLAUDE_SESSION_ID", "")
    if not sid:
        try:
            p = Path("/tmp/willow-session-ganesha.json")
            if p.exists():
                data = json.loads(p.read_text())
                sid = data.get("session_id", "unknown")
        except Exception:
            sid = "unknown"
    return sid


def _is_memory_write(tool_input):
    """Check if the Write target is a memory file."""
    file_path = tool_input.get("file_path", "")
    for prefix in MEMORY_PATHS:
        if file_path.startswith(prefix) and "/memory/" in file_path:
            return True
    return False


def _is_sensitive(file_path):
    """Check if this memory file should stay ganesha-only (not sent to Willow)."""
    fname = Path(file_path).name
    return any(fname.startswith(p) for p in SENSITIVE_PREFIXES)


def _extract_memory_meta(content):
    """Extract name, type, description from frontmatter if present."""
    meta = {"name": "", "type": "project", "description": ""}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip().lower()
                    val = val.strip()
                    if key in meta:
                        meta[key] = val
    return meta


def _write_to_ganesha(name, mem_type, content, source_file, session_id):
    """Write to ganesha.pending_memories via parameterized psycopg2."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="willow", user="willow", password="willow",
            host="172.26.176.1", port=5437
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SET search_path = ganesha, public")
        cur.execute(GANESHA_SCHEMA_INIT)
        cur.execute(
            """INSERT INTO pending_memories
               (memory_name, memory_type, content, source_file, session_id)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (name, mem_type, content, source_file, session_id)
        )
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def _write_to_willow(name, content, mem_type):
    """Write to Willow knowledge graph via MCP ingest endpoint."""
    try:
        payload = json.dumps({
            "title": f"Ganesha Memory: {name}",
            "content": content,
            "category": f"ganesha-memory|{mem_type}",
            "tags": ["ganesha", "memory", mem_type]
        }).encode()
        req = urllib.request.Request(
            f"{WILLOW_URL}/api/knowledge/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.exit(0)  # Can't parse, allow through

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Only intercept Write tool targeting memory files
    if tool_name != "Write":
        sys.exit(0)

    if not _is_memory_write(tool_input):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    content = tool_input.get("content", "")

    # Skip MEMORY.md index file — that's just a pointer
    if file_path.endswith("MEMORY.md"):
        sys.exit(0)

    meta = _extract_memory_meta(content)
    name = meta["name"] or Path(file_path).stem
    mem_type = meta["type"]
    session_id = _get_session_id()
    sensitive = _is_sensitive(file_path)

    # 1. Always write to ganesha.pending_memories (Ganesha owns this)
    db_ok = _write_to_ganesha(name, mem_type, content, file_path, session_id)

    # 2. Write to Willow knowledge UNLESS sensitive
    willow_ok = False
    if not sensitive:
        willow_ok = _write_to_willow(name, content, mem_type)

    # Report what happened (visible to user as hook output)
    parts = []
    if db_ok:
        parts.append("ganesha.pending_memories ✓")
    if willow_ok:
        parts.append("Willow knowledge ✓")
    if sensitive:
        parts.append("(sensitive — Willow skipped)")

    if parts:
        status = " | ".join(parts)
        print(f"[MEMORY DUAL-WRITE] {name} → {status}", file=sys.stderr)

    # Always allow the flat file write (fallback cache)
    sys.exit(0)


if __name__ == "__main__":
    main()
