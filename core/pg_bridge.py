"""
pg_bridge.py — Bridge to Willow's Postgres knowledge graph

Optional dependency. Shell and MCP server work without it (standalone mode).
When available, provides the second tier of the retrieval cascade:
  local WillowStore (SQLite) → Postgres (this) → fleet generation
"""

import os
from typing import Optional


def _pg_params() -> dict:
    """Connection params from env vars or defaults."""
    return {
        "dbname": os.environ.get("WILLOW_PG_DB", "willow"),
        "user": os.environ.get("WILLOW_PG_USER", "willow"),
        "password": os.environ.get("WILLOW_PG_PASS", "willow"),
        "host": os.environ.get("WILLOW_PG_HOST", "172.26.176.1"),
        "port": int(os.environ.get("WILLOW_PG_PORT", "5437")),
    }


class PgBridge:
    """Bridge to Willow's Postgres knowledge graph. Optional — shell works without it."""

    def __init__(self, params: dict = None):
        import psycopg2
        self._psycopg2 = psycopg2
        self._params = params or _pg_params()
        self._conn = None

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = self._psycopg2.connect(**self._params)
            self._conn.autocommit = True
        return self._conn

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    def ping(self) -> bool:
        """Check if Postgres is reachable."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            return True
        except Exception:
            self._conn = None
            return False

    # ── Knowledge Search ──────────────────────────────────────────────

    def search_knowledge(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search on knowledge_slim view (no content_snippet)."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            # Use to_tsquery with OR-joined terms (fix from loam.search bug)
            terms = " | ".join(t.strip() for t in query.split() if t.strip())
            cur.execute("""
                SELECT id, title, summary, source_type, source_id, category,
                       lattice_domain, lattice_type, lattice_status,
                       ts_rank(search_vector, to_tsquery('english', %s)) AS rank
                FROM knowledge
                WHERE search_vector @@ to_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
            """, (terms, terms, limit))
            columns = [d[0] for d in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []

    def search_entities(self, query: str, limit: int = 20) -> list[dict]:
        """Search entities table."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, entity_type, first_seen, mention_count
                FROM entities
                WHERE name ILIKE %s
                ORDER BY mention_count DESC
                LIMIT %s
            """, (f"%{query}%", limit))
            columns = [d[0] for d in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []

    def search_ganesha(self, query: str, limit: int = 20) -> list[dict]:
        """Search ganesha.atoms by title or content."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, domain, depth, source_file, created
                FROM ganesha.atoms
                WHERE title ILIKE %s OR content ILIKE %s
                ORDER BY created DESC
                LIMIT %s
            """, (f"%{query}%", f"%{query}%", limit))
            columns = [d[0] for d in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []

    # ── Edges ─────────────────────────────────────────────────────────

    def edges_for(self, atom_id: int) -> list[dict]:
        """Get all edges involving an atom."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT e.source_id, e.target_id, e.edge_type, e.weight,
                       s.title AS source_title, t.title AS target_title
                FROM knowledge_edges e
                LEFT JOIN knowledge s ON e.source_id = s.id
                LEFT JOIN knowledge t ON e.target_id = t.id
                WHERE e.source_id = %s OR e.target_id = %s
                ORDER BY e.weight DESC
                LIMIT 50
            """, (atom_id, atom_id))
            columns = [d[0] for d in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []

    # ── Ingest ────────────────────────────────────────────────────────

    def ingest_atom(self, title: str, summary: str, source_type: str,
                    source_id: str, category: str = "general",
                    domain: str = None, lattice_type: str = None,
                    lattice_status: str = None) -> Optional[int]:
        """Write a new atom to the knowledge table. Returns atom id."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO knowledge (title, summary, source_type, source_id, category,
                                       lattice_domain, lattice_type, lattice_status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW()::text)
                RETURNING id
            """, (title, summary, source_type, source_id, category,
                  domain, lattice_type, lattice_status))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            return None

    def ingest_ganesha_atom(self, content: str, domain: str = "meta",
                            depth: int = 1, source_session: str = None) -> Optional[int]:
        """Write an atom to ganesha.atoms."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            title = content[:80].split(".")[0] if "." in content[:80] else content[:80]
            cur.execute("""
                INSERT INTO ganesha.atoms (content, title, domain, depth, source_session, source_file)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (content, title, domain, depth, source_session,
                  f"session:{source_session}" if source_session else None))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            return None

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Knowledge graph stats."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            result = {}
            for table, query in [
                ("knowledge", "SELECT COUNT(*) FROM knowledge"),
                ("entities", "SELECT COUNT(*) FROM entities"),
                ("edges", "SELECT COUNT(*) FROM knowledge_edges"),
                ("ganesha_atoms", "SELECT COUNT(*) FROM ganesha.atoms"),
                ("ganesha_handoffs", "SELECT COUNT(*) FROM ganesha.handoffs"),
            ]:
                try:
                    cur.execute(query)
                    result[table] = cur.fetchone()[0]
                except Exception:
                    conn.rollback()
                    result[table] = -1
            cur.close()
            return result
        except Exception:
            return {}


def try_connect() -> Optional[PgBridge]:
    """Try to create a PgBridge. Returns None if Postgres unavailable."""
    try:
        bridge = PgBridge()
        if bridge.ping():
            return bridge
        return None
    except Exception:
        return None
