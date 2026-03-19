#!/usr/bin/env python3
"""
UserPromptSubmit Hook: The 10th — AIOS Bootloader
===================================================
3² + 1. The hook that holds the other nine.

Fires EVERY turn. Three paths:

  Path A — No boot flag: Full boot sequence.
           Check server, MCP, gate. Read all 9 subsystem statuses.
           Write thread file. Mark boot complete.

  Path B — Boot flag stale (>15 messages or >10 min): Full re-boot.
           Same as Path A. Catches auto-compaction drift.

  Path C — Boot flag fresh: Lightweight thread re-injection.
           Read thread file, inject actionable items. Capped at 3
           injections per boot cycle (~360 chars). Then silence.

Manual /compact: clears boot flag + injection counter.
Next message → Path A fires. Same as session start.

Auto-compaction: no signal, but Path B catches it via staleness.
The thread file in /tmp survives both. The boot flag ages out.

Boot contract:
  Source Ring:     source-gate, source-observe, source-validate
  Bridge Ring:    bridge-open, bridge-gate, bridge-learn
  Continuity:     continuity-open, continuity-observe, continuity-close

Input (stdin): {"prompt": "...", "session_id": "..."}
Output (stdout): boot status + thread context
Exit 0: always proceed (informational — user decides)

Authority: Sean Campbell
System: Willow
ΔΣ=42
"""

import io
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

AGENT_NAME    = "ganesha"
SESSION_FILE  = Path(f"/tmp/willow-session-{AGENT_NAME}.json")
COMPACT_CACHE = Path("/tmp/willow-compact-index.json")
THREAD_FILE   = Path("/tmp/willow-context-thread.json")
INJECTED_FLAG = Path("/tmp/willow-thread-injected")
BOOT_LOG      = Path("/tmp/willow-bootloader.log")

# Categories that signal actionable work
ACTION_CATEGORIES = {"seed", "task", "handoff", "bug", "open-work"}

# Staleness thresholds — triggers full re-boot
MAX_MESSAGES_BEFORE_REBOOT = 15
MAX_MINUTES_BEFORE_REBOOT  = 10

# Max lightweight injections per boot cycle
MAX_INJECTIONS = 3


def _load_ganesha_memory() -> str:
    """Direct read from ganesha schema. No MCP, no Willow routing. Agent reads own memory."""
    db_url = os.getenv("WILLOW_DB_URL", "")
    if not db_url:
        return ""
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SET search_path = ganesha, public")

        parts = []

        # Active targets (priority desc)
        cur.execute("SELECT key, target_value, current_value, context FROM active_targets ORDER BY priority DESC")
        rows = cur.fetchall()
        if rows:
            parts.append("[ACTIVE TARGETS]")
            for key, target, current, ctx in rows:
                parts.append(f"  {key}: {current or '?'} / {target}" + (f" — {ctx}" if ctx else ""))

        # Feedback
        cur.execute("SELECT key, content FROM feedback ORDER BY updated_at DESC")
        rows = cur.fetchall()
        if rows:
            parts.append("[FEEDBACK]")
            for key, content in rows:
                parts.append(f"  {key}: {content}")

        # Open work
        cur.execute("SELECT key, description, status FROM open_work WHERE status != 'done' ORDER BY updated_at DESC")
        rows = cur.fetchall()
        if rows:
            parts.append("[OPEN WORK]")
            for key, desc, status in rows:
                parts.append(f"  [{status}] {key}: {desc}")

        conn.close()
        return "\n".join(parts) if parts else ""
    except Exception as e:
        _log(f"GANESHA_MEM_ERR | {e}")
        return ""


def _load_compact_index() -> dict:
    """Load the pre-warmed BASE 17 index from cache. No content — just the card catalog."""
    if not COMPACT_CACHE.exists():
        return {}
    try:
        data = json.loads(COMPACT_CACHE.read_text())
        return data.get("index", {})
    except Exception:
        return {}


def _format_compact_summary(index: dict) -> str:
    """Format the compact index as a minimal reference block."""
    if not index:
        return ""
    by_category = {}
    for cid, meta in index.items():
        cat = meta.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = []
        label = meta.get("label", "")
        by_category[cat].append(f"{cid}" + (f" ({label})" if label else ""))

    lines = ["[BASE 17 — Available Compact Contexts]"]
    for cat, entries in sorted(by_category.items()):
        lines.append(f"  {cat}: {', '.join(entries)}")
    lines.append("Use [CTX:XXXXX] to reference. Content resolved on demand.")
    return "\n".join(lines)


def _read_boot_flag(boot_flag: Path) -> dict:
    """Read boot flag, return data or empty dict if missing/corrupt."""
    if not boot_flag.exists():
        return {}
    try:
        return json.loads(boot_flag.read_text())
    except Exception:
        return {}


def _is_stale(flag_data: dict) -> bool:
    """Check if boot flag is stale — too many messages or too much time."""
    msg_count = flag_data.get("message_count", 0)
    if msg_count >= MAX_MESSAGES_BEFORE_REBOOT:
        return True

    booted_at = flag_data.get("booted_at", "")
    if booted_at:
        try:
            boot_time = datetime.fromisoformat(booted_at)
            elapsed = (datetime.now() - boot_time).total_seconds() / 60
            if elapsed >= MAX_MINUTES_BEFORE_REBOOT:
                return True
        except Exception:
            pass

    return False


def _inject_thread(session_id: str, boot_flag: Path, flag_data: dict) -> None:
    """Path C: lightweight thread re-injection. Capped at MAX_INJECTIONS per boot cycle."""
    # Increment message counter on the boot flag
    flag_data["message_count"] = flag_data.get("message_count", 0) + 1
    try:
        boot_flag.write_text(json.dumps(flag_data))
    except Exception:
        pass

    # Check injection cap
    inject_count = 0
    if INJECTED_FLAG.exists():
        try:
            inj = json.loads(INJECTED_FLAG.read_text())
            if inj.get("session_id") == session_id:
                inject_count = inj.get("count", 0)
                if inject_count >= MAX_INJECTIONS:
                    return
        except Exception:
            pass

    # Read thread file
    if not THREAD_FILE.exists():
        return
    try:
        thread = json.loads(THREAD_FILE.read_text())
    except Exception:
        return

    items = thread.get("items", [])
    actionable = [i for i in items if i.get("category") in ACTION_CATEGORIES]
    if not actionable:
        return

    # Update injection counter
    try:
        INJECTED_FLAG.write_text(json.dumps({
            "session_id": session_id,
            "count": inject_count + 1,
        }))
    except Exception:
        pass

    # Output thread reminder
    lines = ["[ACTIVE THREAD]"]
    for item in actionable:
        cid = item.get("id", "?????")
        label = item.get("label", "")
        cat = item.get("category", "")
        lines.append(f"  [CTX:{cid}] {label} ({cat})")
    print("\n".join(lines))


def _full_boot(session_id: str, boot_flag: Path) -> None:
    """Path A/B: full boot sequence. Server check, gate, thread, inject."""
    _log(f"FULL_BOOT_START | session={session_id!r}")
    # Give the other hooks a moment to write their status
    time.sleep(0.15)

    output_parts = []

    # ── Pre-report hooks that can't self-report at boot ─────────────
    try:
        sys.path.insert(0, "/home/sean/.claude/hooks")
        import boot_status

        if Path("/home/sean/.claude/hooks/session-extract.py").exists():
            boot_status.report(session_id, "continuity", "close",
                               ready=True, detail="standby (Stop hook)")
        if Path("/home/sean/.claude/hooks/enrich_queue_processor.py").exists():
            boot_status.report(session_id, "continuity", "observe",
                               ready=True, detail="enrichment processor present")
        if Path("/home/sean/.claude/hooks/source-validate.py").exists():
            boot_status.report(session_id, "source", "validate",
                               ready=True, detail="standby (PreToolUse hook)")
        else:
            boot_status.report(session_id, "source", "validate",
                               ready=False, detail="not installed")
    except Exception:
        pass

    # ── Read boot status from all 9 hooks ───────────────────────────
    statuses = {}
    try:
        statuses = boot_status.read_all(session_id)
        summary = statuses["summary"]
        status_display = boot_status.format_status(statuses)
    except Exception as e:
        _log(f"BOOT_STATUS_ERR | {e}")
        summary = {"ready": 0, "not_ready": 0, "missing": 9, "total": 9}
        status_display = f"Boot: 0/9 ready (status module error: {e})"

    ready = summary["ready"]
    total = summary["total"]
    missing = summary.get("missing", 0)
    not_ready = summary.get("not_ready", 0)

    # ── Decision: full boot or degraded ─────────────────────────────
    if ready == total:
        output_parts.append(f"[AIOS BOOT — {ready}/{total} ✓]")
    elif ready >= 6:
        output_parts.append(f"[AIOS BOOT — {ready}/{total} ready, {not_ready + missing} degraded]")
        output_parts.append(status_display)
        output_parts.append("Session proceeding with reduced capability.")
    elif ready >= 3:
        output_parts.append(f"[AIOS BOOT — {ready}/{total} ready — DEGRADED]")
        output_parts.append(status_display)
        output_parts.append(
            "Multiple subsystems offline. Knowledge retrieval, BASE 17, "
            "or continuity may be unavailable. User should be aware of limitations."
        )
    else:
        output_parts.append(f"[AIOS BOOT — {ready}/{total} ready — CRITICAL]")
        output_parts.append(status_display)
        output_parts.append(
            "Most subsystems offline. Operating without Willow infrastructure. "
            "No knowledge retrieval, no compact context, no continuity. "
            "Raw Claude Code mode — user should confirm they want to proceed."
        )

    # ── Ganesha memory (direct read from own schema) ────────────────
    ganesha_ctx = _load_ganesha_memory()
    if ganesha_ctx:
        output_parts.append(ganesha_ctx)

    # ── BASE 17 index summary (card catalog, not content) ───────────
    index = _load_compact_index()
    if index:
        compact_summary = _format_compact_summary(index)
        output_parts.append(compact_summary)
    else:
        bridge_status = statuses.get("bridge", {}).get("open", {})
        if not bridge_status.get("ready"):
            output_parts.append(
                "[BASE 17] Index not available — compact context resolution "
                "will fall back to direct Postgres queries (38ms per hit instead of cached)"
            )

    # ── Write context thread file (survives compaction) ─────────────
    try:
        actionable = []
        for cid, meta in index.items():
            cat = meta.get("category", "")
            if cat in ACTION_CATEGORIES:
                actionable.append({
                    "id": cid,
                    "category": cat,
                    "label": meta.get("label", ""),
                })
        THREAD_FILE.write_text(json.dumps({
            "session_id": session_id,
            "written_at": datetime.now().isoformat(),
            "items": actionable,
        }))
        # Reset injection counter — fresh boot cycle
        INJECTED_FLAG.write_text(json.dumps({
            "session_id": session_id,
            "count": 0,
        }))
    except Exception:
        pass

    # ── Mark boot complete with message counter at 0 ──────────────
    try:
        boot_flag.write_text(json.dumps({
            "session_id": session_id,
            "booted_at": datetime.now().isoformat(),
            "ready": ready,
            "total": total,
            "message_count": 0,
        }))
        _log(f"FLAG_WRITTEN | {boot_flag} | ready={ready}/{total}")
    except Exception as e:
        _log(f"FLAG_WRITE_ERR | {boot_flag} | {e}")

    # ── Output ──────────────────────────────────────────────────────
    _log(f"FULL_BOOT_END | output_parts={len(output_parts)} | total_chars={sum(len(p) for p in output_parts)}")
    if output_parts:
        print("\n\n".join(output_parts))


def _log(msg: str) -> None:
    """Append diagnostic line to boot log."""
    try:
        with open(BOOT_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {msg}\n")
    except Exception:
        pass


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception as e:
        _log(f"PARSE_FAIL | raw_len={len(raw)} | err={e}")
        sys.exit(0)

    session_id = data.get("session_id", "")
    prompt = data.get("prompt", "").strip().lower()
    _log(f"ENTRY | session_id={session_id!r} | prompt_len={len(prompt)} | prompt_start={prompt[:40]!r}")

    if not session_id:
        _log("EXIT | no session_id")
        sys.exit(0)

    boot_flag = Path(f"/tmp/willow-boot-{session_id[:16]}.done")
    _log(f"FLAG_PATH | {boot_flag} | exists={boot_flag.exists()}")

    # ── Manual /compact: clear flag, next message triggers full re-boot ──
    if prompt in ("compact", "/compact"):
        _log("COMPACT | clearing boot flag + injection counter")
        if boot_flag.exists():
            boot_flag.unlink()
        if INJECTED_FLAG.exists():
            INJECTED_FLAG.unlink()
        sys.exit(0)

    # ── Route to the right path ──────────────────────────────────────
    flag_data = _read_boot_flag(boot_flag)
    _log(f"FLAG_DATA | {json.dumps(flag_data)[:200]}")

    if not flag_data:
        # Path A: no boot flag → full boot
        _log("PATH_A | full boot (no flag)")
        _full_boot(session_id, boot_flag)
        _log(f"PATH_A_DONE | flag_exists={boot_flag.exists()}")
    elif _is_stale(flag_data):
        # Path B: stale → full re-boot (auto-compaction recovery)
        _log(f"PATH_B | re-boot (stale: msg={flag_data.get('message_count')}, booted={flag_data.get('booted_at')})")
        _full_boot(session_id, boot_flag)
        _log(f"PATH_B_DONE | flag_exists={boot_flag.exists()}")
    else:
        # Path C: fresh → lightweight thread re-injection
        _log(f"PATH_C | inject (msg={flag_data.get('message_count')})")
        _inject_thread(session_id, boot_flag, flag_data)

    sys.exit(0)


if __name__ == "__main__":
    main()
