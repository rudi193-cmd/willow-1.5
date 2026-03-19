"""
compact_portless.py — BASE 17 pointer system for portless architecture

Extends the compact context system to support file pointers.
When category='file', content is a file path — resolved on demand.
When category='atom', content is a Postgres atom ID — resolved on demand.
When category='handoff', content is a Nest file path — resolved on demand.

All other categories work as before (content inline).

This module wraps core/compact.py for portless use. It adds:
1. File pointer registration (register_file)
2. Atom pointer registration (register_atom)
3. Batch registration for session deltas (register_session_delta)
4. Unified resolve that handles both inline and pointer content
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# Connection params
_PG_PARAMS = {
    "dbname": os.environ.get("WILLOW_PG_DB", "willow"),
    "user": os.environ.get("WILLOW_PG_USER", "willow"),
    "password": os.environ.get("WILLOW_PG_PASS", "willow"),
    "host": os.environ.get("WILLOW_PG_HOST", "172.26.176.1"),
    "port": int(os.environ.get("WILLOW_PG_PORT", "5437")),
}

# BASE 17 alphabet
_ALPHABET = "0123456789ACEHKLNRTXZ"
_BASE = 17


def _gen_id(length=5) -> str:
    import time, random
    seed = int(time.time() * 1000) ^ os.getpid() ^ random.randint(0, 0xFFFFFF)
    chars = []
    for _ in range(length):
        seed, rem = divmod(seed, _BASE)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def _connect():
    import psycopg2
    conn = psycopg2.connect(**_PG_PARAMS)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET search_path = sweet_pea_rudi19, public")
    cur.close()
    return conn


def _ensure_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS compact_contexts (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'pattern',
            label TEXT,
            agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            access_count INTEGER DEFAULT 0,
            last_accessed TIMESTAMP
        )
    """)
    cur.close()


# ── Register ──────────────────────────────────────────────────────────

def register(content: str, category: str = "pattern", label: str = None,
             agent: str = None, ttl_hours: float = 0, ctx_id: str = None) -> str:
    """Register content under a BASE 17 ID. Returns 5-char ID."""
    conn = _connect()
    try:
        _ensure_table(conn)
        cur = conn.cursor()
        cid = ctx_id or _gen_id()
        now = datetime.now()
        from datetime import timedelta
        expires = (now + timedelta(hours=ttl_hours)) if ttl_hours > 0 else None

        cur.execute("""
            INSERT INTO compact_contexts (id, content, category, label, agent, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                content = EXCLUDED.content, category = EXCLUDED.category,
                label = EXCLUDED.label, expires_at = EXCLUDED.expires_at
        """, (cid, content, category, label, agent, now, expires))
        cur.close()
        return cid
    finally:
        conn.close()


def register_file(file_path: str, label: str = None, agent: str = "ganesha") -> str:
    """Register a file pointer. Content = file path. Resolver reads on demand."""
    path = str(Path(file_path).resolve())
    name = Path(file_path).name
    return register(
        content=f"file:{path}",
        category="file",
        label=label or name,
        agent=agent,
    )


def register_atom(atom_id: int, table: str = "knowledge", label: str = None,
                  agent: str = "ganesha") -> str:
    """Register a Postgres atom pointer. Resolver queries on demand."""
    return register(
        content=f"atom:{table}:{atom_id}",
        category="atom",
        label=label or f"{table}-{atom_id}",
        agent=agent,
    )


def register_handoff(file_path: str, title: str, agent: str = "ganesha") -> str:
    """Register a handoff file pointer."""
    path = str(Path(file_path).resolve())
    return register(
        content=f"file:{path}",
        category="handoff",
        label=title,
        agent=agent,
        ttl_hours=0,  # permanent
    )


def register_session_delta(files: list[str], label: str = None,
                           agent: str = "ganesha") -> dict[str, str]:
    """Register all files from a session delta. Returns {path: base17_id}."""
    result = {}
    for f in files:
        cid = register_file(f, agent=agent)
        result[f] = cid
    return result


# ── Resolve ───────────────────────────────────────────────────────────

def resolve(ctx_id: str) -> dict | None:
    """Resolve a BASE 17 ID. Handles both inline content and file pointers."""
    conn = _connect()
    try:
        _ensure_table(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, content, category, label, agent, created_at, expires_at, access_count
            FROM compact_contexts WHERE id = %s
        """, (ctx_id,))
        row = cur.fetchone()
        if not row:
            return None

        # Check expiry
        if row[6] and row[6] < datetime.now():
            return None

        # Update access count
        cur.execute("""
            UPDATE compact_contexts SET access_count = access_count + 1, last_accessed = %s
            WHERE id = %s
        """, (datetime.now(), ctx_id))
        cur.close()

        content = row[1]
        resolved_content = _resolve_pointer(content)

        return {
            "id": row[0],
            "content": resolved_content or content,
            "raw_pointer": content,
            "category": row[2],
            "label": row[3],
            "agent": row[4],
            "created_at": str(row[5]),
            "access_count": row[7] + 1,
        }
    finally:
        conn.close()


def _resolve_pointer(content: str) -> str | None:
    """If content is a pointer (file:, atom:), resolve it. Otherwise return None."""
    if content.startswith("file:"):
        path = content[5:]
        try:
            p = Path(path)
            if p.exists() and p.is_file() and not p.is_symlink():
                return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return None

    if content.startswith("atom:"):
        parts = content[5:].split(":", 1)
        if len(parts) == 2:
            table, atom_id = parts
            try:
                conn = _connect()
                cur = conn.cursor()
                if table == "knowledge":
                    cur.execute("SELECT title, summary, source_type, source_id FROM knowledge WHERE id = %s", (int(atom_id),))
                elif table == "ganesha.atoms":
                    cur.execute("SELECT title, content, domain, source_file FROM ganesha.atoms WHERE id = %s", (int(atom_id),))
                else:
                    return None
                row = cur.fetchone()
                conn.close()
                if row:
                    return " | ".join(str(v) for v in row if v)
            except Exception:
                pass
        return None

    if content.startswith("See file:"):
        path = content[9:].strip()
        try:
            p = Path(path)
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return None

    return None  # Not a pointer — content is inline


# ── List / Search ─────────────────────────────────────────────────────

def list_contexts(category: str = None, limit: int = 50) -> list[dict]:
    conn = _connect()
    try:
        _ensure_table(conn)
        cur = conn.cursor()
        if category:
            cur.execute("""SELECT id, category, label, agent, LENGTH(content), access_count, created_at::text
                FROM compact_contexts WHERE category = %s ORDER BY created_at DESC LIMIT %s""", (category, limit))
        else:
            cur.execute("""SELECT id, category, label, agent, LENGTH(content), access_count, created_at::text
                FROM compact_contexts ORDER BY created_at DESC LIMIT %s""", (limit,))
        rows = cur.fetchall()
        cur.close()
        return [{"id": r[0], "category": r[1], "label": r[2], "agent": r[3],
                 "content_size": r[4], "accesses": r[5], "created": r[6]} for r in rows]
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage:")
        print("  compact_portless.py list [category]")
        print("  compact_portless.py resolve <ID>")
        print("  compact_portless.py register-file <path> [label]")
        print("  compact_portless.py register-delta <file1> <file2> ...")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "list":
        cat = sys.argv[2] if len(sys.argv) > 2 else None
        for ctx in list_contexts(category=cat):
            print(f"  {ctx['id']} [{ctx['category']}] {ctx['label']} ({ctx['content_size']}b, {ctx['accesses']} accesses)")

    elif cmd == "resolve":
        result = resolve(sys.argv[2])
        if result:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"  Not found: {sys.argv[2]}")

    elif cmd == "register-file":
        path = sys.argv[2]
        label = sys.argv[3] if len(sys.argv) > 3 else None
        cid = register_file(path, label=label)
        print(f"  {cid} → {path}")

    elif cmd == "register-delta":
        files = sys.argv[2:]
        results = register_session_delta(files)
        for path, cid in results.items():
            print(f"  {cid} → {Path(path).name}")
