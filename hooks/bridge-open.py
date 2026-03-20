#!/usr/bin/env python3
"""
UserPromptSubmit Hook: bridge-open (Bridge Ring — Opener)
==========================================================
Fires on first message of each session. The bridge between
the last session and this one.

Boot sequence:
  1. Health check — Postgres alive (portless), HTTP legacy fallback
  2. Agent checkin + journal session open
  3. Handoff recovery from knowledge DB (NOT raw files)
  4. BASE 17 index pre-warm — batch load to /tmp cache (atomic write)
  5. Pipeline health verification
  6. Report boot status to boot_status contract

Fallback chain:
  - Knowledge DB → Nest raw files → nothing (degraded, user informed)
  - Postgres → cached index → no BASE 17 (degraded, user informed)

Input (stdin): {"prompt": "...", "session_id": "..."}
Output (stdout): context to inject (handoff + gaps, NOT base17 content)
Exit 0: always proceed

Authority: Sean Campbell
System: Willow
ΔΣ=42
"""

import io
import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── Config ──────────────────────────────────────────────────────────────────

WILLOW_URL   = "http://localhost:8420"
USERNAME     = "Sweet-Pea-Rudi19"
AGENT_NAME   = "ganesha"
SESSION_FILE = Path(f"/tmp/willow-session-{AGENT_NAME}.json")
NEST_DIR     = Path("/mnt/c/Users/Sean/My Drive/Willow/Auth Users/ganesha/Nest")
COMPACT_CACHE = Path("/tmp/willow-compact-index.json")
SERVER_FLAG  = Path("/tmp/willow-server-status.json")
MAX_HANDOFF_CHARS = 4000

# ─── HTTP helpers ────────────────────────────────────────────────────────────

def _post(path: str, payload: dict, timeout: int = 4) -> dict | None:
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{WILLOW_URL}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _get(path: str, timeout: int = 4) -> dict | None:
    try:
        with urllib.request.urlopen(f"{WILLOW_URL}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ─── 1. Health check + state verification ─────────────────────────────────

def verify_wiring() -> list[str]:
    """Verify the 1.5 portless architecture is actually wired, not just claimed.
    Returns list of drift warnings. Empty = all good."""
    drift = []

    # Check .mcp.json points to portless server
    mcp_json = Path.home() / ".mcp.json"
    if mcp_json.exists():
        try:
            mcp = json.loads(mcp_json.read_text())
            willow_args = mcp.get("mcpServers", {}).get("willow", {}).get("args", [])
            if willow_args and "willow_store_mcp.py" not in willow_args[0]:
                drift.append(f"MCP points to {willow_args[0]} — should be willow_store_mcp.py")
            willow_env = mcp.get("mcpServers", {}).get("willow", {}).get("env", {})
            if "WILLOW_URL" in willow_env and "8420" in willow_env.get("WILLOW_URL", ""):
                drift.append("MCP env still has WILLOW_URL=localhost:8420 — portless uses PG env vars")
        except Exception:
            drift.append(".mcp.json unreadable")
    else:
        drift.append(".mcp.json missing — MCP server not configured")

    # Check PG env vars are set
    pg_host = os.environ.get("WILLOW_PG_HOST", "")
    if not pg_host:
        drift.append("WILLOW_PG_HOST not in env — settings.json env block may be missing")

    # Check WILLOW_SHELL is set
    shell_path = os.environ.get("WILLOW_SHELL", "")
    if not shell_path:
        drift.append("WILLOW_SHELL not in env — CRUST not configured")
    elif not Path(shell_path).exists():
        drift.append(f"WILLOW_SHELL={shell_path} — file does not exist")

    return drift


def check_health() -> tuple[bool, str, float]:
    """Verify Willow alive. Portless: check Postgres. Verify wiring state."""
    t0 = time.perf_counter()

    # State verification — report drift, don't block
    drift = verify_wiring()

    # Portless path: check Postgres directly
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname=os.environ.get("WILLOW_PG_DB", "willow"),
            user=os.environ.get("WILLOW_PG_USER", "willow"),
            password=os.environ.get("WILLOW_PG_PASS", "willow"),
            host=os.environ.get("WILLOW_PG_HOST", "172.26.176.1"),
            port=int(os.environ.get("WILLOW_PG_PORT", "5437")),
            connect_timeout=2,
        )
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM knowledge")
        atoms = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM entities")
        entities = cur.fetchone()[0]
        cur.close()
        conn.close()
        elapsed = (time.perf_counter() - t0) * 1000
        detail = f"portless OK ({atoms} atoms, {entities} entities)"
        if drift:
            detail += " | DRIFT: " + "; ".join(drift)
        return True, detail, elapsed
    except Exception:
        pass

    elapsed = (time.perf_counter() - t0) * 1000
    detail = "Postgres unreachable"
    if drift:
        detail += " | DRIFT: " + "; ".join(drift)
    return False, detail, elapsed


# ─── 2. Agent checkin + journal ─────────────────────────────────────────────

def checkin_and_journal(session_id: str) -> str:
    """Register presence and open journal session via Postgres. Returns willow_session_id."""
    willow_session_id = session_id[:12]
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname=os.environ.get("WILLOW_PG_DB", "willow"),
            user=os.environ.get("WILLOW_PG_USER", "willow"),
            password=os.environ.get("WILLOW_PG_PASS", "willow"),
            host=os.environ.get("WILLOW_PG_HOST", "172.26.176.1"),
            port=int(os.environ.get("WILLOW_PG_PORT", "5437")),
            connect_timeout=3,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO journal_events (event_type, username, payload)
            VALUES ('session_start', %s, %s)
        """, (USERNAME, json.dumps({
            "agent": AGENT_NAME,
            "session_id": session_id[:8],
            "willow_session_id": willow_session_id,
        })))
        cur.close()
        conn.close()
    except Exception:
        pass
    return willow_session_id


# ─── 3. Handoff recovery from knowledge DB ─────────────────────────────────

def recover_handoff() -> tuple[str, str]:
    """
    Get latest handoff from prior session.
    Sources (in order): ganesha.handoffs DB, knowledge search, Nest raw files.
    Filters out current session's own handoffs.
    Returns (source, content).
    """
    import urllib.parse

    # Determine current session's handoff filename (to exclude it)
    current_session_handoff = ""
    try:
        if SESSION_FILE.exists():
            state = json.loads(SESSION_FILE.read_text())
            current_session_handoff = state.get("handoff_file", "")
    except Exception:
        pass

    # Primary: ganesha.handoffs (durable, session-aware)
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="willow", user="willow", password="willow",
            host="172.26.176.1", port=5437, connect_timeout=3
        )
        cur = conn.cursor()
        cur.execute(
            """SELECT title, content, source_file FROM ganesha.handoffs
               ORDER BY id DESC LIMIT 1"""
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row and row[1]:
            content = row[1]
            # Resolve pointer: "See file: /path/to/file" → read file
            if content.startswith("See file:"):
                file_path = content[9:].strip()
                try:
                    p = Path(file_path)
                    if p.exists() and p.is_file():
                        content = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    # Pointer unresolvable — try Nest files fallback
                    if row[2]:
                        nest_path = NEST_DIR / row[2]
                        if nest_path.exists():
                            content = nest_path.read_text(encoding="utf-8", errors="replace")
            if content and len(content) > 50:
                return f"ganesha.handoffs:{row[0]}", content[:MAX_HANDOFF_CHARS]
    except Exception:
        pass

    # Secondary: query Postgres directly for handoff atoms
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname="willow", user="willow", password="willow",
            host="172.26.176.1", port=5437, connect_timeout=3
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT title, summary, content_snippet FROM knowledge
            WHERE title ILIKE '%%HANDOFF%%' OR title ILIKE '%%SESSION_HANDOFF%%'
            ORDER BY created_at DESC LIMIT 3
        """)
        for row in cur.fetchall():
            content = row[2] or row[1] or ""
            if content and len(content) > 50:
                cur.close()
                conn.close()
                return f"knowledge-db:{row[0]}", content[:MAX_HANDOFF_CHARS]
        cur.close()
        conn.close()
    except Exception:
        pass

    # Tertiary: read raw file from Nest (Pigeon hasn't ingested yet)
    try:
        handoffs = sorted(NEST_DIR.glob("SESSION_HANDOFF*.md"), reverse=True)
        for hf in handoffs:
            # Skip current session's own handoff
            if current_session_handoff and hf.name == current_session_handoff:
                continue
            content = hf.read_text(encoding="utf-8", errors="replace")
            if content and len(content) > 50:
                return f"nest-file:{hf.name}", content[:MAX_HANDOFF_CHARS]
    except Exception:
        pass

    return "", ""


# ─── 4. BASE 17 index pre-warm ─────────────────────────────────────────────

def prewarm_compact_index() -> tuple[bool, str, float]:
    """
    Batch load BASE 17 compact context index into /tmp cache.
    One Postgres round-trip. Index only — no content loaded.
    Returns (ok, detail, latency_ms).
    """
    t0 = time.perf_counter()
    try:
        sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow/core")
        from compact import list_contexts

        contexts = list_contexts(limit=100)
        elapsed = (time.perf_counter() - t0) * 1000

        # Build index: id → {category, label} (NO content)
        index = {}
        for ctx in contexts:
            index[ctx["id"]] = {
                "category": ctx.get("category", ""),
                "label": ctx.get("label", ""),
            }

        # Atomic write to cache
        cache_data = {
            "warmed_at": datetime.now().isoformat(),
            "count": len(index),
            "index": index,
        }
        fd, tmp_path = tempfile.mkstemp(dir="/tmp", suffix=".compact.tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(cache_data, f)
            os.replace(tmp_path, str(COMPACT_CACHE))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return False, "cache write failed", elapsed

        return True, f"{len(index)} contexts indexed", elapsed

    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return False, f"pre-warm failed: {e}", elapsed


# ─── 5. Pipeline health ────────────────────────────────────────────────────

def check_pipeline_health(handoff_source: str) -> str:
    """Verify the handoff made it through the pipeline."""
    if not handoff_source:
        return "[PIPELINE] No prior handoff found"
    if handoff_source.startswith("knowledge-db:"):
        return f"[PIPELINE] ✓ Handoff in knowledge DB — Pigeon delivered"
    if handoff_source.startswith("nest-file:"):
        fname = handoff_source.split(":", 1)[1]
        return f"[PIPELINE] ⚠ Handoff read from Nest raw file ({fname}) — Pigeon may be lagging"
    return ""


# ─── 6. Knowledge gaps ─────────────────────────────────────────────────────

def surface_gaps() -> str:
    """Surface open knowledge gaps."""
    gaps_result = _get(f"/api/knowledge/gaps?username={USERNAME}")
    if gaps_result:
        gaps = gaps_result.get("gaps") or []
        if gaps:
            gap_lines = [f"  - {g.get('description') or g.get('topic') or str(g)}"
                         for g in gaps[:5]]
            return "[OPEN KNOWLEDGE GAPS]\n" + "\n".join(gap_lines)
    return ""


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    session_id = data.get("session_id", "")
    if not session_id:
        sys.exit(0)

    # Once-per-session guard
    if SESSION_FILE.exists():
        try:
            state = json.loads(SESSION_FILE.read_text())
            if state.get("session_id") == session_id:
                sys.exit(0)
        except Exception:
            pass

    output_parts = []

    # ── 1. Health check ─────────────────────────────────────────────
    server_ok, server_detail, server_ms = check_health()

    # ── 1b. Write server status flag (consumed by mcp-gate hook) ──
    try:
        flag_data = json.dumps({
            "server_ok": server_ok,
            "detail": server_detail,
            "checked_at": datetime.now().isoformat(),
            "session_id": session_id,
        })
        fd, tmp_path = tempfile.mkstemp(dir="/tmp", suffix=".srv.tmp")
        with os.fdopen(fd, 'w') as f:
            f.write(flag_data)
        os.replace(tmp_path, str(SERVER_FLAG))
    except Exception:
        pass

    # ── 2. Checkin + journal ────────────────────────────────────────
    willow_session_id = checkin_and_journal(session_id) if server_ok else ""

    # ── 3. Handoff from knowledge DB ────────────────────────────────
    handoff_source, handoff_content = "", ""
    if server_ok:
        handoff_source, handoff_content = recover_handoff()
    if handoff_content:
        label = handoff_source.split(":", 1)[1] if ":" in handoff_source else handoff_source
        output_parts.append(f"[PRIOR SESSION — {label}]\n{handoff_content}")

    # ── 4. BASE 17 pre-warm ────────────────────────────────────────
    compact_ok, compact_detail, compact_ms = prewarm_compact_index()

    # ── 5. Pipeline health ──────────────────────────────────────────
    health = check_pipeline_health(handoff_source)
    if health:
        output_parts.append(health)

    # ── 6. Knowledge gaps ───────────────────────────────────────────
    gaps = surface_gaps() if server_ok else ""
    if gaps:
        output_parts.append(gaps)

    # ── 6b. SAFE session (CRUST) ────────────────────────────────────
    safe_session_id = ""
    try:
        shell_path = os.environ.get("WILLOW_SHELL",
            "/mnt/c/Users/Sean/Documents/GitHub/willow-1.5/core/safe_shell.py")
        shell_dir = str(Path(shell_path).parent)
        sys.path.insert(0, shell_dir)
        from safe_shell import SAFESession
        store_root = os.environ.get("WILLOW_STORE",
            str(Path.home() / ".willow" / "store"))
        safe = SAFESession(store_root, USERNAME)
        # Ganesha is ENGINEER trust — auto-authorize all streams
        safe.authorized_streams = set(safe.authorized_streams)
        for stream in ("journal", "knowledge", "agents", "governance", "preferences"):
            safe.authorized_streams.add(stream)
            safe._audit("CONSENT_GRANTED", stream)
        safe._active = True
        safe._audit("SESSION_START", "ganesha-bridge-open")
        safe_session_id = safe.session_id
        # Don't close — session lives until session-extract Stop hook
    except Exception:
        pass

    # ── Write session state ─────────────────────────────────────────
    try:
        SESSION_FILE.write_text(json.dumps({
            "session_id": session_id,
            "willow_session_id": willow_session_id,
            "safe_session_id": safe_session_id,
            "username": USERNAME,
            "started_at": datetime.now().isoformat(),
            "turn_count": 0,
            "server_ok": server_ok,
            "compact_ok": compact_ok,
        }))
    except Exception:
        pass

    # ── Report boot status ──────────────────────────────────────────
    try:
        sys.path.insert(0, "/home/sean/.claude/hooks")
        import boot_status
        boot_status.report(session_id, "bridge", "open",
                           ready=server_ok and compact_ok,
                           detail=f"server:{server_detail} | compact:{compact_detail}",
                           latency_ms=server_ms + compact_ms)
    except Exception:
        pass

    # ── Output ──────────────────────────────────────────────────────
    if output_parts:
        print("\n\n".join(output_parts))

    sys.exit(0)


if __name__ == "__main__":
    main()
