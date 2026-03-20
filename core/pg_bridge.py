"""
pg_bridge.py — LOAM (1.5)
===========================
L — Layer
O — Of
A — Accumulated
M — Memory

Knowledge retrieval from Willow's Postgres graph.
1.4 LOAM was SQLite FTS5. 1.5 LOAM is Postgres direct — portless.

Retrieval cascade:
  local WillowStore → Postgres (this) → fleet generation

Optional dependency. Shell and MCP server work without it (standalone mode).
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

    # ── Opus ─────────────────────────────────────────────────────────

    def search_opus(self, query: str, limit: int = 20) -> list[dict]:
        """Search opus.atoms by title or content."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT id, title, domain, depth, source_file, created
                FROM opus.atoms
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

    def ingest_opus_atom(self, content: str, domain: str = "meta",
                         depth: int = 1, source_session: str = None) -> Optional[int]:
        """Write an atom to opus.atoms."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            title = content[:80].split(".")[0] if "." in content[:80] else content[:80]
            cur.execute("""
                INSERT INTO opus.atoms (content, title, domain, depth, source_session, source_file)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (content, title, domain, depth, source_session,
                  f"session:{source_session}" if source_session else None))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            return None

    def opus_feedback(self, domain: str = None) -> list[dict]:
        """Read opus feedback entries. If domain given, filter by it."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            if domain:
                cur.execute("SELECT id, domain, principle, source, created FROM opus.feedback WHERE domain = %s ORDER BY created", (domain,))
            else:
                cur.execute("SELECT id, domain, principle, source, created FROM opus.feedback ORDER BY created")
            columns = [d[0] for d in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []

    def opus_feedback_write(self, domain: str, principle: str, source: str = "self") -> bool:
        """Write an opus feedback entry."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO opus.feedback (domain, principle, source, created)
                VALUES (%s, %s, %s, NOW())
            """, (domain, principle, source))
            cur.close()
            return True
        except Exception:
            return False

    def opus_journal_write(self, entry: str, session_id: str = None) -> Optional[int]:
        """Write a journal entry to opus.journal."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO opus.journal (entry, session_id, created_at)
                VALUES (%s, %s, NOW())
                RETURNING id
            """, (entry, session_id))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            return None

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

    # ── Task Queue ──────────────────────────────────────────────────────

    def submit_task(self, task: str, submitted_by: str = "ganesha",
                    agent: str = "kart") -> Optional[str]:
        """Submit a task to the queue. Returns task_id."""
        import hashlib, time
        task_id = hashlib.sha256(f"{task}{time.time()}".encode()).hexdigest()[:12]
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO kart_task_queue (task_id, submitted_by, agent, task)
                VALUES (%s, %s, %s, %s)
                RETURNING task_id
            """, (task_id, submitted_by, agent, task))
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            return None

    def task_status(self, task_id: str) -> Optional[dict]:
        """Get task status by task_id."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT task_id, submitted_by, agent, task, status, result,
                       steps, created_at, started_at, completed_at
                FROM kart_task_queue WHERE task_id = %s
            """, (task_id,))
            row = cur.fetchone()
            if not row:
                cur.close()
                return None
            columns = [d[0] for d in cur.description]
            cur.close()
            return dict(zip(columns, row))
        except Exception:
            return None

    def claim_task(self, agent: str = "kart") -> Optional[dict]:
        """Claim the oldest pending task for an agent. Returns task or None."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                UPDATE kart_task_queue
                SET status = 'running', started_at = NOW()
                WHERE id = (
                    SELECT id FROM kart_task_queue
                    WHERE status = 'pending' AND agent = %s
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING task_id, task, submitted_by
            """, (agent,))
            row = cur.fetchone()
            if not row:
                cur.close()
                return None
            columns = [d[0] for d in cur.description]
            cur.close()
            return dict(zip(columns, row))
        except Exception:
            return None

    def complete_task(self, task_id: str, result: dict, steps: int = 0) -> bool:
        """Mark a task as complete with result."""
        import json as _json
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                UPDATE kart_task_queue
                SET status = 'complete', result = %s, steps = %s, completed_at = NOW()
                WHERE task_id = %s
            """, (_json.dumps(result), steps, task_id))
            cur.close()
            return True
        except Exception:
            return False

    def fail_task(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        import json as _json
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                UPDATE kart_task_queue
                SET status = 'failed', result = %s, completed_at = NOW()
                WHERE task_id = %s
            """, (_json.dumps({"error": error}), task_id))
            cur.close()
            return True
        except Exception:
            return False

    def pending_tasks(self, agent: str = "kart", limit: int = 10) -> list[dict]:
        """List pending tasks for an agent."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT task_id, task, submitted_by, created_at
                FROM kart_task_queue
                WHERE status = 'pending' AND agent = %s
                ORDER BY created_at ASC
                LIMIT %s
            """, (agent, limit))
            columns = [d[0] for d in cur.description]
            results = [dict(zip(columns, row)) for row in cur.fetchall()]
            cur.close()
            return results
        except Exception:
            return []

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
                ("opus_atoms", "SELECT COUNT(*) FROM opus.atoms"),
                ("opus_feedback", "SELECT COUNT(*) FROM opus.feedback"),
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
