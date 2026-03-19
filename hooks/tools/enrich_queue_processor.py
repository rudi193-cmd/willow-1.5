#!/usr/bin/env python3
"""
enrich_queue_processor.py — Background fleet-powered file enrichment
=====================================================================
Reads enrichment_queue.jsonl, sends each unprocessed file to the free fleet
for summarization, and writes the result back to willow_knowledge.db.

Usage:
  python enrich_queue_processor.py [--limit N]

Run manually or via scheduled task. Non-destructive — processed entries are
marked done in the queue and skipped on next run.

Output:
  Appends to: /mnt/c/Users/Sean/Documents/GitHub/Willow/artifacts/Sweet-Pea-Rudi19/willow_knowledge.db
  Queue:      /mnt/c/Users/Sean/.claude/enrichment_queue.jsonl
"""

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

ENRICH_QUEUE   = Path('/mnt/c/Users/Sean/.claude/enrichment_queue.jsonl')
FEEDBACK_QUEUE = Path('/mnt/c/Users/Sean/.claude/feedback_queue.jsonl')
KNOWLEDGE_DB   = Path(
    '/mnt/c/Users/Sean/Documents/GitHub/Willow/artifacts/Sweet-Pea-Rudi19/willow_knowledge.db'
)
WILLOW_CORE    = Path('/mnt/c/Users/Sean/Documents/GitHub/Willow/core')

DEFAULT_LIMIT = 20   # Max files per run (avoid runaway fleet usage)
RATE_DELAY_S  = 0.5  # Seconds between fleet calls


# ─── Fleet ────────────────────────────────────────────────────────────────────

def load_fleet():
    """Load llm_router from Willow/core. Returns module or None."""
    sys.path.insert(0, str(WILLOW_CORE))
    try:
        import llm_router
        llm_router.load_keys_from_json()
        return llm_router
    except Exception as e:
        print(f"Fleet unavailable: {e}")
        return None


def fleet_summarize(router, file_path: str, content: str) -> str | None:
    """Send file to fleet for summarization. Returns summary string or None."""
    ext = Path(file_path).suffix.lower()
    lang_hint = {
        ".py": "Python", ".ts": "TypeScript", ".js": "JavaScript",
        ".sh": "bash", ".ps1": "PowerShell", ".sql": "SQL",
        ".md": "Markdown", ".yaml": "YAML", ".json": "JSON",
    }.get(ext, "text")

    prompt = f"""Summarize this {lang_hint} file in 2-3 sentences for a knowledge base index.
Focus on: what it does, key functions/classes, its role in the system.
Be specific, not generic. No preamble.

File: {file_path}

```{lang_hint.lower()}
{content[:4000]}
```"""

    try:
        response = router.ask(prompt, preferred_tier="free")
        if response and response.content:
            return response.content.strip()[:400]
    except Exception as e:
        print(f"  Fleet error: {e}")
    return None


# ─── Knowledge DB ─────────────────────────────────────────────────────────────

def infer_category(file_path: str) -> str:
    fp = file_path.lower()
    if "core" in fp or "archive" in fp:     return "code"
    if "governance" in fp:                  return "governance"
    if "spec" in fp or "product" in fp:     return "specs"
    if "docs" in fp or ".md" in fp:         return "documentation"
    if "artifact" in fp or "session" in fp: return "data"
    return "code"


def write_to_knowledge_db(file_path: str, summary: str, provider: str):
    """Insert enriched file summary into willow_knowledge.db."""
    if not KNOWLEDGE_DB.exists():
        return False
    try:
        conn = sqlite3.connect(str(KNOWLEDGE_DB))
        title = Path(file_path).name
        category = infer_category(file_path)
        now = datetime.now(timezone.utc).isoformat()

        # Check if title already exists (avoid duplicates)
        exists = conn.execute(
            "SELECT rowid FROM knowledge WHERE title = ?", (title,)
        ).fetchone()

        if exists:
            conn.execute(
                "UPDATE knowledge SET summary = ?, updated_at = ? WHERE title = ?",
                (summary, now, title)
            )
        else:
            conn.execute(
                """INSERT INTO knowledge (title, summary, category, ring, source_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (title, summary, category, f"fleet:{provider}", file_path, now)
            )

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"  DB write error: {e}")
        return False


# ─── Feedback Bridge ──────────────────────────────────────────────────────────

def log_fleet_failure(file_path: str, error_type: str, provider: str | None = None):
    """Write fleet/db failures to feedback_queue.jsonl for pattern tracking."""
    try:
        entry = {
            "id": f"fleet-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{abs(hash(file_path)) % 9999:04d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "fleet",
            "rule": f"{error_type}: {Path(file_path).name}",
            "excerpt": f"provider={provider or 'unknown'} file={file_path}",
            "full_prompt": file_path,
            "status": "pending",
            "error_type": error_type,
            "provider": provider,
        }
        with open(FEEDBACK_QUEUE, "a", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
            f.write("\n")
    except Exception:
        pass  # Never block processing


# ─── Queue ────────────────────────────────────────────────────────────────────

def load_queue() -> list[dict]:
    if not ENRICH_QUEUE.exists():
        return []
    items = []
    for line in ENRICH_QUEUE.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            if obj.get("status") == "pending":
                items.append(obj)
        except Exception:
            pass
    return items


def update_queue_status(file_path: str, status: str, summary: str | None = None):
    """Rewrite queue file with updated status for a given path."""
    if not ENRICH_QUEUE.exists():
        return
    lines = []
    for line in ENRICH_QUEUE.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            if obj.get("file_path") == file_path:
                obj["status"] = status
                obj["processed_at"] = datetime.now(timezone.utc).isoformat()
                if summary:
                    obj["summary_preview"] = summary[:100]
            lines.append(json.dumps(obj))
        except Exception:
            lines.append(line)
    ENRICH_QUEUE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    limit = DEFAULT_LIMIT
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (ValueError, IndexError):
            pass

    queue = load_queue()
    if not queue:
        print("Queue empty — nothing to process.")
        return

    print(f"Processing up to {limit} of {len(queue)} pending files...")

    router = load_fleet()
    if not router:
        print("Fleet unavailable. Run again when Willow/credentials are accessible.")
        return

    processed = 0
    skipped   = 0

    for item in queue[:limit]:
        fp = item.get("file_path", "")
        p  = Path(fp)

        if not p.exists() or not p.is_file():
            update_queue_status(fp, "not_found")
            skipped += 1
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  SKIP (read error): {p.name}: {e}")
            update_queue_status(fp, "read_error")
            skipped += 1
            continue

        print(f"  Analyzing: {p.name} ({p.stat().st_size} bytes)...", end=" ", flush=True)
        summary = fleet_summarize(router, fp, content)

        if summary:
            provider = getattr(router, "_last_provider", "free-fleet")
            ok = write_to_knowledge_db(fp, summary, provider)
            status = "done" if ok else "db_error"
            print(f"[{status}] {summary[:60]}...")
            if not ok:
                log_fleet_failure(fp, "db_error", provider)
        else:
            provider = getattr(router, "_last_provider", None)
            status = "fleet_failed"
            print("[fleet_failed]")
            log_fleet_failure(fp, "fleet_failed", provider)

        update_queue_status(fp, status, summary)
        processed += 1
        time.sleep(RATE_DELAY_S)

    print(f"\nDone: {processed} processed, {skipped} skipped of {len(queue)} queued.")


if __name__ == "__main__":
    main()

