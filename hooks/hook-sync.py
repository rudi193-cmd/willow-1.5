#!/usr/bin/env python3
"""
PostToolUse Hook: hook-sync
=============================
When a hook file is written or edited in ~/.claude/hooks/,
automatically sync it to willow-1.5/hooks/.

Hooks are Willow's nervous system. They live in the repo.
Local edits without repo sync are drift.

Input (stdin): PostToolUse JSON with tool_name, tool_input
Exit 0: always (informational, never blocks)
"""

import json
import shutil
import sys
from pathlib import Path

LOCAL_HOOKS = Path("/home/sean/.claude/hooks")
REPO_HOOKS = Path("/mnt/c/Users/Sean/Documents/GitHub/willow-1.5/hooks")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    p = Path(file_path)

    # Check if the edited file is under ~/.claude/hooks/
    try:
        p.relative_to(LOCAL_HOOKS)
    except ValueError:
        sys.exit(0)

    # Compute the relative path and target
    rel = p.relative_to(LOCAL_HOOKS)
    target = REPO_HOOKS / rel

    # Skip archived hooks
    if "archive" in str(rel):
        sys.exit(0)

    # Ensure target directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(str(p), str(target))
        print(f"[HOOK-SYNC] {rel} → willow-1.5/hooks/{rel}", file=sys.stderr)
    except Exception as e:
        print(f"[HOOK-SYNC] FAILED: {rel} — {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
