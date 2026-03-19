"""
pg.py — Degraded fallback DB helper for hooks
================================================
Used by hooks ONLY when MCP/server is unavailable.
This is NOT the primary path. MCP is the primary path.

Extracted from bootloader.py:68-109.
"""

import os
import psycopg2


def connect(schema="ganesha"):
    """Return (conn, cur) with search_path set. Caller must close."""
    db_url = os.getenv("WILLOW_DB_URL", "")
    if not db_url:
        raise RuntimeError("WILLOW_DB_URL not set")
    conn = psycopg2.connect(db_url, connect_timeout=3)
    cur = conn.cursor()
    cur.execute(f"SET search_path = {schema}, public")
    return conn, cur


def query(sql, params=None, schema="ganesha"):
    """Run query, return rows, auto-close. For simple reads."""
    conn, cur = connect(schema)
    try:
        cur.execute(sql, params or ())
        return cur.fetchall()
    finally:
        conn.close()
