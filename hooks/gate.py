#!/usr/bin/env python3
"""
UserPromptSubmit Hook: Gate
============================

THE GATE — Ofshield decides. Jeles retrieves.

OFSHIELD (The Gate, Threshold Faculty):
    Every message passes through here first. Most pass silently.

    A gap is when the message is reaching for something not in the room:
      - A direct question (ends with ?, starts with question word)
      - A reference to a specific system, file, or named thing Willow knows
      - A technical pattern (snake_case, version numbers, file paths)

    A conversation is already carrying what it needs:
      - Acknowledgments, roleplay, short commands, responses in-flow

    Gap: Jeles opens the right drawer.
    No gap: exit 0. Silent. Don't touch the boards.

JELES (The Librarian, The Stacks):
    Retrieval priority:
      1. GET /api/knowledge/semantic-search  — Willow corpus (MCP-first, embeddings)
      2. POST /api/feedback/provide — if feedback signal detected
      3. POST /api/journal/event    — log message turn

    Fallback (MCP unreachable):
      - Direct SQLite read from willow_knowledge.db (read-only)

FAILURE MODE:
    Symptom: governance-commits or unrelated narratives on a conversational message.
    Cause: score_gap() returned >= GAP_THRESHOLD for a non-gap message.
    Fix: Adjust signal weights in score_gap(). Not: adding to STOP_WORDS.
"""

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

WILLOW_URL   = "http://localhost:8420"
USERNAME     = "Sweet-Pea-Rudi19"
AGENT_NAME   = "ganesha"
SESSION_FILE = Path(f"/tmp/willow-session-{AGENT_NAME}.json")
GAP_THRESHOLD = 4

# Fallback SQLite paths (read-only, used only when MCP unreachable)
KNOWLEDGE_DB = Path("/mnt/c/Users/Sean/Documents/GitHub/Willow/artifacts/Sweet-Pea-Rudi19/willow_knowledge.db")
SHIVA_DB     = Path("/mnt/c/Users/Sean/Documents/GitHub/Willow/shiva_memory/shiva.db")

# ─── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{WILLOW_URL}{path}", timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _post(path: str, payload: dict) -> dict | None:
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{WILLOW_URL}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.loads(r.read())
    except Exception:
        return None


# ─── Gap detection (OFSHIELD) ─────────────────────────────────────────────────

QUESTION_STARTERS = {
    "what", "how", "why", "when", "where", "which", "who",
    "is", "are", "does", "did", "can", "could", "should", "would", "will",
}

ACKNOWLEDGMENTS = {
    "yes", "ok", "okay", "right", "exactly", "fine", "done", "next",
    "go", "proceed", "continue", "accepted", "accept", "approve",
    "confirmed", "confirm", "got", "sure", "yep", "nope", "no",
    "agreed", "perfect", "great", "good", "noted", "filed", "sent",
    "received", "understood", "thanks", "thank", "please", "sorry",
    "wait", "hold", "stop", "pause",
}

KNOWN_ENTITIES = {
    "willow", "kart", "utety", "ganesha", "shiva", "loam", "rings",
    "vine", "soil", "graft", "pulse", "leaf", "prism", "crown",
    "gerald", "ada", "frank", "jeles", "binder", "pigeon", "steve",
    "oakenscroll", "riggs", "hanz", "nova", "alexis", "ofshield",
    "mitra", "consus", "jane", "agent_engine", "context_injector",
    "llm_router", "ecosystem", "governance", "willow_knowledge",
    "shiva_memory", "dual_commit", "die-namic", "aionic", "safe",
    "context_store", "session_extract", "enrichment_queue",
}

HISTORY_SIGNALS = {
    "yesterday", "previous", "last session", "last time", "before",
    "history", "prior", "earlier", "remember", "recall", "we did",
}

FEEDBACK_PATTERNS = (
    r'\b(you should|next time|don\'t|always|never|prefer|stop|you keep|again|'
    r'already told you|wrong|that\'s not|actually|incorrect)\b'
)

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "this", "that", "these",
    "those", "i", "you", "he", "she", "it", "we", "they", "what", "which",
    "who", "when", "where", "why", "how", "all", "each", "every", "and",
    "or", "but", "if", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "up", "out", "about", "into", "through", "during", "before",
    "after", "above", "below", "just", "also", "as", "so", "then", "there",
    "than", "too", "very", "its", "your", "let", "go", "get", "run", "build",
    "make", "create", "add", "use", "need", "want", "think", "know", "see",
    "look", "find", "work", "going", "not", "no", "yes", "now", "new", "old",
    "well", "like", "back", "here", "still", "really", "already", "even",
    "only", "both", "same", "other", "more", "most", "some", "any", "few",
    "full", "first", "last", "next", "right", "over", "under", "down", "off",
    "away", "around", "put", "set", "keep", "take", "give", "tell", "show",
    "say", "ask", "try", "start", "stop", "move", "change", "open", "close",
    "sean", "claude", "agent", "token", "model",
}

_TECHNICAL_RE = re.compile(
    r'[a-z]+_[a-z]+'           # snake_case
    r'|[A-Z][a-z]+[A-Z]'       # camelCase
    r'|\.[a-z]{2,5}\b'         # file extensions
    r'|v\d+\.\d+'              # version numbers
    r'|/[a-z]+/[a-z]'          # path fragments
    r'|#\d+'                   # issue numbers
    r'|\b\d{4}-\d{2}-\d{2}\b'  # dates
)


def score_gap(prompt: str) -> tuple:
    text = prompt.strip()
    text_lower = text.lower()
    words_lower = re.findall(r'\b[a-z][a-z0-9]{2,}\b', text_lower)

    score = 0
    keywords = []
    is_history = False

    if text_lower.rstrip().endswith("?"):
        score += 3
    first_word = words_lower[0] if words_lower else ""
    if first_word in QUESTION_STARTERS:
        score += 2
    if _TECHNICAL_RE.search(text):
        score += 2
    for entity in KNOWN_ENTITIES:
        if entity in text_lower:
            score += 1
            if entity not in keywords:
                keywords.append(entity)
            break
    for signal in HISTORY_SIGNALS:
        if signal in text_lower:
            score += 1
            is_history = True
            break

    if len(text) < 15:
        score -= 4
    if text.startswith("*"):
        score -= 5
    non_stop = {w for w in words_lower if w not in STOP_WORDS and w not in ACKNOWLEDGMENTS}
    if len(non_stop) == 0:
        score -= 5
    elif len(non_stop) == 1 and len(words_lower) <= 4:
        score -= 3

    seen = set(keywords)
    for w in words_lower:
        if w not in STOP_WORDS and w not in ACKNOWLEDGMENTS and w not in seen and len(w) >= 3:
            seen.add(w)
            keywords.append(w)

    return score, keywords[:8], is_history


def extract_intent(prompt: str, keywords: list) -> str:
    text = prompt.strip().lower().rstrip("?").strip()
    text = re.sub(
        r'^(what|how|why|when|where|which|who)\s+(is|are|was|were|does|did|the|a|an|your|my|our|this|that)?\s*',
        '', text
    ).strip()
    if len(text) >= 6:
        return text[:80]
    return " ".join(keywords[:5])


def detect_feedback(prompt: str) -> bool:
    return bool(re.search(FEEDBACK_PATTERNS, prompt.lower()))


# ─── Retrieval (JELES) ────────────────────────────────────────────────────────

CAT_SCORE = {
    "specs": 10, "projects": 9, "governance": 8,
    "documentation": 7, "narrative": 6, "code": 4, "data": 3,
}
NOISE_PATTERNS = (
    "applypatch", "commit-msg", "pre-push", "sample",
    "CHANGELOG", "changelog", ".git/", "node_modules",
    "package-lock", "__pycache__", ".gitignore", "venv/",
)


def is_noise(title: str) -> bool:
    return any(p in title for p in NOISE_PATTERNS)


# ── Ring scoring weights ───────────────────────────────────────────────────────

_CATEGORY_TRUST = {
    "corrections": 5, "governance": 4, "specs": 3,
    "reference": 3, "documentation": 2, "code": 2,
    "narrative": 1, "data": 1, "media": 0,
}
_SOURCE_AUTHENTICITY = {
    "file": 3, "conversation": 2, "agent": 2, "media": 0,
}


def _ring_score(item: dict) -> float:
    """Composite score: relevance (Bridge) x authenticity (Source) x trust (Continuity)."""
    relevance = float(item.get("similarity") or 0.5)
    src = item.get("source_type", "")
    cat = item.get("category", "")
    authenticity = (_SOURCE_AUTHENTICITY.get(src, 1) +
                    _CATEGORY_TRUST.get(cat, 1)) / 8.0
    trust = 1.0 if cat in ("corrections", "governance") else \
            0.7 if cat in ("specs", "reference") else \
            0.4 if cat in ("documentation", "code") else 0.2
    return relevance * 0.5 + authenticity * 0.3 + trust * 0.2


def query_willow_mcp(intent: str, keywords: list) -> list:
    """Three-ring retrieval: relevance x authenticity x trust."""
    q = intent or " ".join(keywords[:5])
    result = _get(
        f"/api/knowledge/semantic-search?q={urllib.parse.quote(q)}"
        f"&limit=12&username={USERNAME}"
    )
    if not result:
        return []
    items = result.get("results") or []
    scored = []
    for item in items:
        title = item.get("title") or item.get("filename") or ""
        if is_noise(title):
            continue
        text = (item.get("content_snippet") or
                item.get("summary") or
                item.get("content_text") or "")[:140]
        score = _ring_score(item)
        scored.append((score, {
            "title": title,
            "summary": text,
            "category": item.get("category", ""),
            "ring": item.get("source_type", "willow"),
            "trust": item.get("category", ""),
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:4]]


def query_willow_sqlite(keywords: list, intent: str) -> list:
    """Fallback: direct SQLite read when MCP unreachable."""
    import sqlite3
    if not KNOWLEDGE_DB.exists():
        return []
    useful_cats = tuple(CAT_SCORE.keys())
    try:
        conn = sqlite3.connect(str(KNOWLEDGE_DB))
        conn.row_factory = sqlite3.Row
        intent_words = [w for w in intent.split() if len(w) >= 3]
        fts_query = " OR ".join(f'"{k}"' for k in (intent_words or keywords)[:6])
        if not fts_query:
            conn.close()
            return []
        cats_ph = ",".join("?" * len(useful_cats))
        rows = conn.execute(
            f"""SELECT k.title, k.summary, k.category, k.ring
                FROM knowledge_fts f
                JOIN knowledge k ON k.rowid = f.rowid
                WHERE knowledge_fts MATCH ?
                  AND k.category IN ({cats_ph})
                LIMIT ?""",
            (fts_query, *useful_cats, 16),
        ).fetchall()
        conn.close()
        scored, seen = [], set()
        for r in rows:
            title = r["title"] or ""
            if is_noise(title) or title in seen:
                continue
            seen.add(title)
            cat_score = CAT_SCORE.get(r["category"], 1)
            kw_matches = sum(1 for k in keywords if k.lower() in title.lower())
            scored.append((cat_score + kw_matches, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:4]]
    except Exception:
        return []


def query_willow_postgres(intent: str, keywords: list) -> list:
    """Degraded fallback: direct Postgres FTS when MCP/server is unreachable."""
    try:
        sys.path.insert(0, "/home/sean/.claude/hooks")
        import pg
        terms = [w for w in (intent or "").split() if len(w) >= 2]
        if not terms:
            terms = keywords[:6]
        ts_query = " | ".join(t for t in terms if t.isalnum())
        if not ts_query:
            return []
        rows = pg.query(
            """SELECT title, summary, category, source_type
               FROM knowledge
               WHERE search_vector @@ to_tsquery('english', %s)
               ORDER BY ts_rank(search_vector, to_tsquery('english', %s)) DESC
               LIMIT 12""",
            (ts_query, ts_query),
            schema="sweet_pea_rudi19",
        )
        scored = []
        for title, summary, cat, src in rows:
            if is_noise(title or ""):
                continue
            score = _ring_score({
                "category": cat or "",
                "source_type": src or "",
                "similarity": 0.5,
            })
            scored.append((score, {
                "title": title or "",
                "summary": (summary or "")[:140],
                "category": cat or "",
                "ring": src or "postgres-fallback",
            }))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:4]]
    except Exception:
        return []


def query_shiva_postgres(keywords: list) -> list:
    """Degraded fallback: shiva corrections via direct Postgres."""
    try:
        sys.path.insert(0, "/home/sean/.claude/hooks")
        import pg
        terms = " | ".join(k for k in keywords[:6] if k.isalnum())
        if not terms:
            return []
        rows = pg.query(
            """SELECT domain, principle, correction
               FROM shiva_corrections
               ORDER BY created_at DESC LIMIT 3""",
            schema="sweet_pea_rudi19",
        )
        results = []
        for domain, principle, correction in rows:
            snippet = (principle or correction or "")[:140].strip()
            if snippet:
                results.append({
                    "title": f"[correction] {domain or 'general'}",
                    "summary": snippet,
                    "category": "governance",
                    "ring": "shiva",
                })
        return results
    except Exception:
        return []


def query_shiva_sqlite(keywords: list) -> list:
    """Fallback: shiva corrections via SQLite when MCP unreachable."""
    import sqlite3
    if not SHIVA_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(SHIVA_DB), timeout=3)
        conn.row_factory = sqlite3.Row
        fts = " OR ".join(f'"{k}"' for k in keywords[:6])
        rows = conn.execute(
            """SELECT c.domain, c.principle, c.correction
               FROM corrections_fts f
               JOIN corrections c ON c.id = f.rowid
               WHERE corrections_fts MATCH ?
               LIMIT 2""", (fts,)
        ).fetchall()
        conn.close()
        results = []
        seen = set()
        for r in rows:
            snippet = (r["principle"] or r["correction"] or "")[:140].strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                results.append({
                    "title": f"[correction] {r['domain'] or 'general'}",
                    "summary": snippet,
                    "category": "governance",
                    "ring": "shiva",
                })
        return results
    except Exception:
        return []


def safe_str(s) -> str:
    return (s or "").encode("ascii", "replace").decode()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Need urllib.parse for quoting
    import urllib.parse

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    prompt = data.get("prompt", "")
    if not prompt or len(prompt.strip()) < 4:
        sys.exit(0)

    # ── OFSHIELD: Is the gate warranted? ──────────────────────────────
    score, keywords, is_history = score_gap(prompt)

    # ── Log message turn to journal (background, non-blocking) ────────
    session_id = data.get("session_id", "")
    willow_session_id = ""
    try:
        if SESSION_FILE.exists():
            state = json.loads(SESSION_FILE.read_text())
            willow_session_id = state.get("willow_session_id", "")
            # Increment turn count
            state["turn_count"] = state.get("turn_count", 0) + 1
            SESSION_FILE.write_text(json.dumps(state))
    except Exception:
        pass

    if willow_session_id:
        _post("/api/journal/event", {
            "username": USERNAME,
            "session_id": willow_session_id,
            "event_type": "message",
            "payload": {"turn": data.get("turn_count", 0), "gap_score": score},
        })

    # ── Detect and route feedback signals ─────────────────────────────
    if detect_feedback(prompt):
        _post("/api/feedback/provide", {
            "username": USERNAME,
            "signal": prompt[:500],
            "source": "gate-hook",
            "confidence": 0.8,
        })

    # ── Report boot status on first turn ───────────────────────────────
    try:
        turn_count = 0
        if SESSION_FILE.exists():
            st = json.loads(SESSION_FILE.read_text())
            turn_count = st.get("turn_count", 0)
        if turn_count <= 1:
            sys.path.insert(0, "/home/sean/.claude/hooks")
            import boot_status
            boot_status.report(session_id, "bridge", "gate",
                               ready=True,
                               detail=f"Ofshield+Jeles online, gap_threshold={GAP_THRESHOLD}",
                               latency_ms=0)
    except Exception:
        pass

    # ── Gap check — exit silently if no gap ───────────────────────────
    if score < GAP_THRESHOLD or not keywords:
        sys.exit(0)

    # ── JELES: Pull the right thing ───────────────────────────────────
    intent = extract_intent(prompt, keywords)

    # MCP first → Postgres fallback → SQLite last resort
    results = query_willow_mcp(intent, keywords)
    if not results:
        corrections = query_shiva_postgres(keywords)
        corpus = query_willow_postgres(intent, keywords)
        results = corrections + corpus
    if not results:
        corrections = query_shiva_sqlite(keywords)
        corpus = query_willow_sqlite(keywords, intent)
        results = corrections + corpus

    if not results:
        sys.exit(0)

    # ── Format output ─────────────────────────────────────────────────
    lines = [f"[WILLOW — {len(results)} match(es): {', '.join(keywords[:5])}]"]
    for r in results:
        cat   = safe_str(r.get("category", ""))
        title = safe_str(r.get("title", ""))
        summ  = safe_str(r.get("summary", ""))[:140]
        ring  = safe_str(r.get("ring", ""))
        trust_marker = "✓" if cat in ("corrections", "governance", "specs") else "~"
        lines.append(f"  [{cat}/{ring}]{trust_marker} {title}")
        if summ:
            lines.append(f"    {summ}")

    print("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    main()
