"""
willow_client.py — Shared Willow client for hooks

Portless-first: tries direct Postgres, falls back to HTTP.
All hooks import this instead of hardcoding localhost:8420.

Usage in hooks:
    from tools.willow_client import willow_get, willow_post, willow_search, server_ok
"""

import json
import os
import urllib.request


WILLOW_URL = os.environ.get("WILLOW_URL", "http://localhost:8420")

_PG_PARAMS = {
    "dbname": os.environ.get("WILLOW_PG_DB", "willow"),
    "user": os.environ.get("WILLOW_PG_USER", "willow"),
    "password": os.environ.get("WILLOW_PG_PASS", "willow"),
    "host": os.environ.get("WILLOW_PG_HOST", "172.26.176.1"),
    "port": int(os.environ.get("WILLOW_PG_PORT", "5437")),
}


def server_ok() -> bool:
    """Check if Willow is reachable. Portless: Postgres first, HTTP legacy fallback."""
    if pg_ok():
        return True
    try:
        with urllib.request.urlopen(f"{WILLOW_URL}/api/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def pg_ok() -> bool:
    """Check if Postgres is reachable."""
    try:
        import psycopg2
        conn = psycopg2.connect(**_PG_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


def willow_get(path: str, timeout: int = 4) -> dict | None:
    """GET from Willow. Tries HTTP first, returns None on failure."""
    try:
        with urllib.request.urlopen(f"{WILLOW_URL}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def willow_post(path: str, payload: dict, timeout: int = 10) -> dict | None:
    """POST to Willow. Tries HTTP first, returns None on failure."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{WILLOW_URL}{path}", data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def willow_search(query: str, limit: int = 10) -> list[dict]:
    """Search knowledge graph. Tries HTTP, falls back to Postgres."""
    # Try HTTP first
    result = willow_get(f"/api/safe/query?q={urllib.request.quote(query)}&limit={limit}")
    if result and isinstance(result, list):
        return result

    # Fallback: direct Postgres
    try:
        import psycopg2
        conn = psycopg2.connect(**_PG_PARAMS)
        cur = conn.cursor()
        terms = " | ".join(t.strip() for t in query.split() if t.strip())
        cur.execute("""
            SELECT id, title, summary, source_type, source_id, category
            FROM knowledge
            WHERE search_vector @@ to_tsquery('english', %s)
            ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
            LIMIT %s
        """, (terms, terms, limit))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []


def willow_status() -> dict:
    """System status. Tries HTTP, falls back to Postgres counts."""
    result = willow_get("/api/status")
    if result:
        return result

    # Fallback
    try:
        import psycopg2
        conn = psycopg2.connect(**_PG_PARAMS)
        cur = conn.cursor()
        status = {}
        for name, query in [
            ("atoms", "SELECT COUNT(*) FROM knowledge"),
            ("entities", "SELECT COUNT(*) FROM entities"),
            ("edges", "SELECT COUNT(*) FROM knowledge_edges"),
        ]:
            try:
                cur.execute(query)
                status[name] = cur.fetchone()[0]
            except Exception:
                conn.rollback()
        cur.close()
        conn.close()
        return {"knowledge": status, "mode": "portless"}
    except Exception:
        return {"error": "both HTTP and Postgres unavailable"}


def ganesha_query(query: str, schema: str = "ganesha", table: str = "atoms",
                  limit: int = 10) -> list[dict]:
    """Query ganesha schema directly (always Postgres, no HTTP path)."""
    try:
        import psycopg2
        conn = psycopg2.connect(**_PG_PARAMS)
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, title, content, domain, depth
            FROM {schema}.{table}
            WHERE title ILIKE %s OR content ILIKE %s
            ORDER BY created DESC
            LIMIT %s
        """, (f"%{query}%", f"%{query}%", limit))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []
