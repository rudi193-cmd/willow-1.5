#!/usr/bin/env python3
"""
PreToolUse Hook: mcp-gate
==========================
Blocks MCP tool calls when Willow server is known to be down.
Reads /tmp/willow-server-status.json (written by bridge-open.py).

If server_ok is false, exits 2 with a message telling the caller
to use direct Postgres or HTTP fallback instead.

If the flag file is missing or stale (>15 min), does a live check
and updates the flag.

Input (stdin): PreToolUse event JSON
Exit 0: allow
Exit 2: block (server down)
"""

import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SERVER_FLAG = Path("/tmp/willow-server-status.json")
WILLOW_URL = "http://localhost:8420"
STALE_MINUTES = 15


def read_flag() -> dict:
    if not SERVER_FLAG.exists():
        return {}
    try:
        return json.loads(SERVER_FLAG.read_text())
    except Exception:
        return {}


def is_stale(flag: dict) -> bool:
    checked = flag.get("checked_at", "")
    if not checked:
        return True
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(checked)).total_seconds() / 60
        return elapsed >= STALE_MINUTES
    except Exception:
        return True


def live_check() -> bool:
    """Quick health check and update flag.
    Portless mode: check Postgres directly. HTTP server is retired.
    """
    ok = False
    # Try Postgres first (portless path)
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="willow", user="willow", password="willow",
            host="172.26.176.1", port=5437, connect_timeout=2,
        )
        conn.close()
        ok = True
    except Exception:
        # Fallback: try HTTP (legacy, may not be running)
        try:
            with urllib.request.urlopen(f"{WILLOW_URL}/api/status", timeout=2) as r:
                data = json.loads(r.read())
                ok = bool(data)
        except Exception:
            ok = False

    try:
        flag_data = json.dumps({
            "server_ok": ok,
            "detail": "live-check from mcp-gate",
            "checked_at": datetime.now().isoformat(),
        })
        fd, tmp_path = tempfile.mkstemp(dir="/tmp", suffix=".srv.tmp")
        with os.fdopen(fd, 'w') as f:
            f.write(flag_data)
        os.replace(tmp_path, str(SERVER_FLAG))
    except Exception:
        pass

    return ok


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    tool_name = data.get("tool_name", "")

    # Only gate MCP tools
    if not tool_name.startswith("mcp__willow__"):
        sys.exit(0)

    # Orchestration tools need the server — can't be faked with raw DB
    ORCHESTRATION_TOOLS = {
        "mcp__willow__willow_chat",
        "mcp__willow__willow_speak",
        "mcp__willow__willow_route",
        "mcp__willow__willow_persona",
        "mcp__willow__willow_knowledge_ingest",
    }
    is_orchestration = tool_name in ORCHESTRATION_TOOLS

    flag = read_flag()

    # No flag or stale — do a live check
    if not flag or is_stale(flag):
        if live_check():
            sys.exit(0)
        else:
            if is_orchestration:
                print(
                    f"BLOCKED: Willow server is down. "
                    f"{tool_name} requires the server (LLM routing / pipeline). "
                    f"Queue this for when the server is back up.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"BLOCKED: Willow server is down. "
                    f"For data queries, use direct Postgres (psycopg2) as degraded fallback. "
                    f"gate.py handles this automatically for knowledge retrieval.",
                    file=sys.stderr,
                )
            sys.exit(2)

    # Flag exists and is fresh
    if flag.get("server_ok"):
        sys.exit(0)

    if is_orchestration:
        print(
            f"BLOCKED: Willow server is down ({flag.get('detail', 'unknown')}). "
            f"{tool_name} requires the server. Queue for later.",
            file=sys.stderr,
        )
    else:
        print(
            f"BLOCKED: Willow server is down ({flag.get('detail', 'unknown')}). "
            f"Use direct Postgres as degraded fallback for data queries.",
            file=sys.stderr,
        )
    sys.exit(2)


if __name__ == "__main__":
    main()
