#!/usr/bin/env python3
"""
UserPromptSubmit hook — injects pending Pigeon messages into Ganesha's context.
Reads /tmp/ganesha_pending.json, prepends to prompt, clears the file.
"""

import os
import sys
import json
from pathlib import Path

PENDING_FILE = Path("/tmp/ganesha_pending.json")
WILLOW = "/mnt/c/Users/Sean/Documents/GitHub/Willow"

def main():
    if not PENDING_FILE.exists():
        sys.exit(0)

    try:
        messages = json.loads(PENDING_FILE.read_text())
    except Exception:
        sys.exit(0)

    if not messages:
        sys.exit(0)

    # Build injection block
    lines = ["📬 **Pending Pigeon messages (unread):**\n"]
    for m in messages:
        lines.append(f"[{m['id']}] From: {m['from_app']} | Subject: {m['subject']}")
        body = m.get("body", "")[:300]
        lines.append(f"    {body}")
        lines.append("")

    lines.append("---")
    inject = "\n".join(lines)

    # Clear the pending file first
    PENDING_FILE.write_text("[]")

    # Mark messages read in Postgres now that they're consumed
    try:
        with open(f"{WILLOW}/.env") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)
        sys.path.insert(0, WILLOW)
        from core import pigeon
        for m in messages:
            pigeon.mark_inbox_read("ganesha-cli", message_id=m["id"])
    except Exception:
        pass  # don't block injection if mark-read fails

    # Output injection to stdout — Claude Code prepends this to the user's prompt
    print(inject)


if __name__ == "__main__":
    main()
