"""
content_resolver.py — Resolve content from source pointers

Atoms are card catalog entries: title, source_type, source_id.
Content lives at the source. This module fetches it on demand.

Used by: retrieval cascade, MCP server, SAFE shell `ask` command.
"""

import json
import os
from pathlib import Path


# Known base paths for content resolution
WILLOW_REPO = os.environ.get(
    "WILLOW_REPO", "/mnt/c/Users/Sean/Documents/GitHub/Willow"
)
ARTIFACTS_BASE = os.path.join(WILLOW_REPO, "artifacts")
DOCS_BASE = os.path.join(WILLOW_REPO, "docs")


def resolve_content(source_type: str, source_id: str, max_chars: int = 5000) -> str | None:
    """
    Resolve content from a source pointer.

    Returns the content string, or None if source is unreachable.
    Never raises — returns None on any failure.
    """
    if not source_type or not source_id:
        return None

    try:
        resolver = _RESOLVERS.get(source_type)
        if resolver:
            content = resolver(source_id)
            if content and len(content) > max_chars:
                return content[:max_chars] + f"\n... (truncated at {max_chars} chars)"
            return content
        return None
    except Exception:
        return None


def _resolve_file_location(source_id: str) -> str | None:
    """source_type='file_location', source_id='Willow:path/to/file'"""
    parts = source_id.split(":", 1)
    if len(parts) != 2:
        return None
    repo, rel_path = parts
    base = _repo_base(repo)
    if not base:
        return None
    full_path = Path(base) / rel_path
    if not full_path.exists():
        return None
    if full_path.is_symlink():
        return None
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _resolve_corpus(source_id: str) -> str | None:
    """source_type='corpus', source_id='loop-room-s1-willow-purpose-revelation'"""
    # Search known corpus locations
    for search_dir in [DOCS_BASE, ARTIFACTS_BASE, WILLOW_REPO]:
        base = Path(search_dir)
        if not base.exists():
            continue
        # Try exact match
        for ext in [".md", ".txt", ".json", ""]:
            candidate = base / f"{source_id}{ext}"
            if candidate.exists() and not candidate.is_symlink():
                try:
                    return candidate.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
        # Try recursive search
        for match in base.rglob(f"*{source_id}*"):
            if match.is_file() and not match.is_symlink():
                try:
                    return match.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
    return None


def _resolve_manual(source_id: str) -> str | None:
    """source_type='manual' — content was manually entered. source_id is the key."""
    # Manual atoms are small enough to keep inline.
    # The summary IS the content for these.
    return None  # Caller should use the summary field directly


def _resolve_behavioral_pattern(source_id: str) -> str | None:
    """source_type='behavioral_pattern', source_id='pattern:Willow:hash'"""
    # These are generated patterns — the summary is the content
    return None


def _resolve_session(source_id: str) -> str | None:
    """source_type='session' or source_file='session:uuid'"""
    session_id = source_id.replace("session:", "")
    # Check known session log locations
    for search_dir in [
        Path.home() / ".claude" / "projects",
        Path(WILLOW_REPO) / "artifacts",
    ]:
        if not search_dir.exists():
            continue
        for match in search_dir.rglob(f"*{session_id}*"):
            if match.is_file() and match.suffix in (".jsonl", ".json", ".md"):
                try:
                    return match.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
    return None


def _resolve_pg_content(source_id: str) -> str | None:
    """Fallback: read content_snippet from the knowledge table directly."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname=os.environ.get("WILLOW_PG_DB", "willow"),
            user=os.environ.get("WILLOW_PG_USER", "willow"),
            password=os.environ.get("WILLOW_PG_PASS", "willow"),
            host=os.environ.get("WILLOW_PG_HOST", "172.26.176.1"),
            port=int(os.environ.get("WILLOW_PG_PORT", "5437")),
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT content_snippet FROM knowledge WHERE source_id = %s LIMIT 1",
            (source_id,)
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _repo_base(repo_name: str) -> str | None:
    """Map repo name to local path."""
    repos = {
        "Willow": WILLOW_REPO,
        "SAFE": os.environ.get("SAFE_REPO", "/mnt/c/Users/Sean/Documents/GitHub/SAFE"),
        "die-namic-system": os.environ.get("DNS_REPO", "/mnt/c/Users/Sean/Documents/GitHub/die-namic-system"),
        "portless-architecture": os.environ.get("PORTLESS_REPO", "/mnt/c/Users/Sean/Documents/GitHub/portless-architecture"),
    }
    return repos.get(repo_name)


# Resolver dispatch table
_RESOLVERS = {
    "file_location": _resolve_file_location,
    "corpus": _resolve_corpus,
    "manual": _resolve_manual,
    "behavioral_pattern": _resolve_behavioral_pattern,
    "session": _resolve_session,
    "pg_fallback": _resolve_pg_content,
}


def resolve_with_fallback(source_type: str, source_id: str, max_chars: int = 5000) -> str | None:
    """Try the primary resolver, then fall back to Postgres content_snippet."""
    result = resolve_content(source_type, source_id, max_chars)
    if result:
        return result
    # Fallback: read from Postgres content_snippet (the data we're trying to stop storing)
    return _resolve_pg_content(source_id)
