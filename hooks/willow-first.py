#!/usr/bin/env python3
"""
PreToolUse Hook: willow-first.py
================================
BLOCKING enforcement: Use Willow MCP/HTTP tools before Bash/Grep/Glob
for knowledge searches on Willow directories.

Exit 2 = BLOCK the tool call with a message.
Exit 0 = allow.

This hook BLOCKS when Claude is doing content/knowledge searches on Willow
dirs without having tried MCP first. Operational commands (git, python, etc.)
are always allowed.
"""
import json
import re
import sys
from pathlib import Path

# ALL Sean's project/data dirs — not just Willow repos
MANAGED_DIRS = (
    "/mnt/c/Users/Sean/Documents/GitHub/Willow",
    "/mnt/c/Users/Sean/Documents/GitHub/willow-1.5",
    "/mnt/c/Users/Sean/Documents/GitHub/die-namic-system",
    "/mnt/c/Users/Sean/Documents/GitHub/SAFE",
    "/mnt/c/Users/Sean/Documents/GitHub/Willow1.1",
    "/mnt/c/Users/Sean/Documents/Willow",
    "/mnt/c/Users/Sean/Willow",
    "/mnt/c/Users/Sean/My Drive/Willow",
    "/mnt/c/Users/Sean/gdoc_exports",
    "/mnt/c/Users/Sean/screenshots",
)
# Legacy alias
WILLOW_DIRS = MANAGED_DIRS

# Patterns that suggest a content/knowledge search (not code work)
SEARCH_PATTERNS = re.compile(
    r'\b(grep|rg|find|cat|head|tail|less|ag)\b'
    r'|--include|--glob|-type\s+f'
    r'|\brglob\b|\bglob\b'
)

# DB queries that should go through MCP
DB_SEARCH_PATTERNS = re.compile(
    r'SELECT.*FROM.*(knowledge|entities|witness|nest_review|pigeon|ganesha)',
    re.IGNORECASE
)

# Patterns in Grep/Glob that suggest knowledge search vs code search
KNOWLEDGE_SEARCH_TERMS = re.compile(
    r'(entity|knowledge|witness|review|promote|chrome|drift|corpus'
    r'|handoff|session|ingest|category|summary|filed|personal|narrative'
    r'|reference|legal|media|document|organize|structure|folder|index)',
    re.IGNORECASE
)

# Operational commands — always allowed, these aren't knowledge searches
OPERATIONAL = re.compile(
    r'\b(pip|python|python3|node|npm|git|ps|kill|nohup|systemctl|service'
    r'|mkdir|rm|mv|cp|chmod|chown|ln|touch|curl|wc|echo|diff|du)\b'
    r'|^\s*cd\s'
    # Allow direct python execution (running scripts, not searching)
    r'|python3?\s+-c'
)

# Bash ls is allowed — it's operational, not a knowledge search
LS_PATTERN = re.compile(r'^\s*ls\b')

# Code-level searches are fine — searching for function defs, imports, etc.
CODE_SEARCH = re.compile(
    r'\b(def |class |import |from |require|export|function )\b'
    r'|\.py:|\.js:|\.ts:'
    r'|\bpattern\b.*\b(def|class|import)\b'
)


_BOOT_REPORTED = Path("/tmp/willow-source-gate-booted")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    # Report boot status once
    if not _BOOT_REPORTED.exists():
        try:
            session_id = data.get("session_id", "unknown")
            sys.path.insert(0, "/home/sean/.claude/hooks")
            import boot_status
            boot_status.report(session_id, "source", "gate",
                               ready=True,
                               detail="willow-first + dual-commit + impact-analysis",
                               latency_ms=0)
            _BOOT_REPORTED.touch()
        except Exception:
            pass

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if tool == "Bash":
        command = tool_input.get("command", "")
        targets_managed = any(d in command for d in MANAGED_DIRS)

        if not targets_managed:
            sys.exit(0)

        # Always allow operational commands (git, python, mv, cp, etc.)
        if OPERATIONAL.search(command):
            sys.exit(0)

        # ls is operational — allow it
        if LS_PATTERN.search(command):
            sys.exit(0)

        # Block DB knowledge queries — MCP is the primary path
        if DB_SEARCH_PATTERNS.search(command):
            print(
                "BLOCKED: Use mcp__willow__willow_knowledge_search or "
                "store_search, not raw SQL. MCP caches results in local store.",
                file=sys.stderr,
            )
            sys.exit(2)

        # Block content searches on managed dirs — MCP + local store first
        if SEARCH_PATTERNS.search(command):
            print(
                "BLOCKED: Use mcp__willow__willow_knowledge_search or "
                "store_search_all first. Cache results with store_put. "
                "Bash search is the fallback, not the default.",
                file=sys.stderr,
            )
            sys.exit(2)

    elif tool in ("Grep", "Glob"):
        path = tool_input.get("path", "")
        pattern = tool_input.get("pattern", "")

        if not any(d in path for d in MANAGED_DIRS):
            sys.exit(0)

        # Code-level searches (function defs, imports) are always fine
        if CODE_SEARCH.search(pattern):
            sys.exit(0)

        # Knowledge-level searches should go through MCP
        if KNOWLEDGE_SEARCH_TERMS.search(pattern):
            print(
                f"BLOCKED: '{pattern}' looks like a knowledge search. "
                f"Use mcp__willow__willow_knowledge_search first, "
                f"cache with store_put. Grep/Glob is for code, not knowledge.",
                file=sys.stderr,
            )
            sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
