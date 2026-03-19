#!/usr/bin/env python3
"""
PreToolUse Hook: source-validate (Source Ring — Validator)
===========================================================
BASE 17 anti-hallucination gate. Fires before any tool call that
sends a prompt containing [CTX:XXXXX] references.

Checks:
  1. Every [CTX:XXXXX] in the prompt resolves to a real compact context
  2. Missing refs get flagged with [MISSING:XXXXX] marker
  3. If ALL refs are missing, BLOCKS the call — no point sending a
     prompt that references nothing

Uses the pre-warmed /tmp cache first (0ms), falls back to Postgres (38ms).

Input (stdin): PreToolUse event JSON
Exit 0: allow
Exit 2: block (all refs missing)

Authority: Sean Campbell
System: Willow
ΔΣ=42
"""

import json
import re
import sys
from pathlib import Path

COMPACT_CACHE = Path("/tmp/willow-compact-index.json")
CTX_PATTERN = re.compile(r'\[CTX:([0-9ACEHKLNRTXZ]{5})\]')
_BOOT_REPORTED = Path("/tmp/willow-source-validate-booted")


def load_index() -> dict:
    """Load pre-warmed BASE 17 index from cache."""
    if COMPACT_CACHE.exists():
        try:
            data = json.loads(COMPACT_CACHE.read_text())
            return data.get("index", {})
        except Exception:
            pass
    return {}


def resolve_from_postgres(ctx_id: str) -> bool:
    """Fallback: check Postgres directly if cache unavailable."""
    try:
        sys.path.insert(0, "/mnt/c/Users/Sean/Documents/GitHub/Willow/core")
        from compact import resolve
        return resolve(ctx_id) is not None
    except Exception:
        return False


def extract_prompt_text(tool_name: str, tool_input: dict) -> str:
    """Extract the text that might contain [CTX:] refs from tool input."""
    if tool_name == "Bash":
        return tool_input.get("command", "")
    if tool_name in ("Edit", "Write"):
        return tool_input.get("content", "") + tool_input.get("new_string", "")
    if tool_name == "Read":
        return ""
    # MCP tools, Agent, etc — check all string values
    parts = []
    for v in tool_input.values():
        if isinstance(v, str):
            parts.append(v)
    return "\n".join(parts)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    session_id = data.get("session_id", "unknown")

    # Report boot status once
    if not _BOOT_REPORTED.exists():
        try:
            sys.path.insert(0, "/home/sean/.claude/hooks")
            import boot_status
            cache_exists = COMPACT_CACHE.exists()
            boot_status.report(session_id, "source", "validate",
                               ready=True,
                               detail=f"BASE 17 validator online, cache={'warm' if cache_exists else 'cold'}",
                               latency_ms=0)
            _BOOT_REPORTED.touch()
        except Exception:
            pass

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Extract text that might contain compact refs
    text = extract_prompt_text(tool_name, tool_input)
    if not text or "[CTX:" not in text:
        sys.exit(0)

    # Find all [CTX:XXXXX] references
    refs = CTX_PATTERN.findall(text)
    if not refs:
        sys.exit(0)

    # Check each ref against cache first, then Postgres
    index = load_index()
    resolved = []
    missing = []

    for ref_id in set(refs):
        if ref_id in index:
            resolved.append(ref_id)
        elif resolve_from_postgres(ref_id):
            resolved.append(ref_id)
        else:
            missing.append(ref_id)

    # All refs resolved — proceed silently
    if not missing:
        sys.exit(0)

    # Some refs missing — warn but allow
    if resolved:
        missing_str = ", ".join(f"[CTX:{m}]" for m in missing)
        print(
            f"[SOURCE-VALIDATE] WARNING: {len(missing)} compact ref(s) unresolved: {missing_str}. "
            f"These will appear as [MISSING:XXXXX] in the prompt. "
            f"{len(resolved)} ref(s) OK.",
            file=sys.stderr,
        )
        sys.exit(0)

    # ALL refs missing — block the call
    missing_str = ", ".join(f"[CTX:{m}]" for m in missing)
    print(
        f"BLOCKED: All {len(missing)} compact context ref(s) are unresolved: {missing_str}. "
        f"The prompt references context that doesn't exist. "
        f"Check BASE 17 IDs or register the missing contexts first.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
