#!/usr/bin/env python3
"""
PreToolUse Hook: Dual Commit Enforcement (T1/T2/T3/T4)
========================================================
Governance gate for Edit and Write tool calls.

Tier classification:
  T1 — Core production modules — POST /api/governance/propose → block until ratified
  T2 — Artifacts, logs, knowledge — POST /api/journal/event → pass through
  T3 — Safe apps, user content — pass through
  T4 — .claude/ config/hooks, local specs — immediate pass-through (operator-controlled)

T1 flow:
  1. POST /api/governance/propose → if auto_approved: pass through (exit 0)
  2. If status=pending: exit 2 (block) with commit_id in message
  3. Sean approves in dashboard → retry the edit

T2 flow:
  1. POST /api/journal/event (non-blocking, fire-and-forget)
  2. exit 0

Input (stdin): PreToolUse JSON with tool_name, tool_input
Exit 0: allow
Exit 2: block (with reason on stderr)
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

WILLOW_URL = "http://localhost:8420"
USERNAME   = "Sweet-Pea-Rudi19"
AGENT_NAME = "ganesha"
SESSION_FILE = Path(f"/tmp/willow-session-{AGENT_NAME}.json")

# ─── Tier classification ──────────────────────────────────────────────────────

# T1: Core production — requires ratification
T1_PATTERNS = [
    r"/Willow/core/",
    r"/Willow/server\.py$",
    r"/Willow/governance/(?!commits/)",   # governance/ but not commits dir
    r"/Willow/mcp/willow_server\.py$",
    r"/Willow/api/",
    r"/die-namic-system/core/",
    r"/die-namic-system/server",
]

# T2: Artifacts, logs — log + allow
T2_PATTERNS = [
    r"/Willow/artifacts/",
    r"/Willow/shiva_memory/",
    r"/Willow/governance/commits/",       # proposal files themselves are T2
]

# T4: Operator config — immediate pass-through
T4_PATTERNS = [
    r"/\.claude/",
    r"^/home/sean/\.claude/",
    r"^/tmp/",
]


def classify_path(file_path: str) -> str:
    for pat in T4_PATTERNS:
        if re.search(pat, file_path):
            return "T4"
    for pat in T1_PATTERNS:
        if re.search(pat, file_path):
            return "T1"
    for pat in T2_PATTERNS:
        if re.search(pat, file_path):
            return "T2"
    return "T3"


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _post(path: str, payload: dict) -> dict | None:
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{WILLOW_URL}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def get_willow_session_id() -> str:
    try:
        if SESSION_FILE.exists():
            return json.loads(SESSION_FILE.read_text()).get("willow_session_id", "")
    except Exception:
        pass
    return ""


# ─── Governance proposal ──────────────────────────────────────────────────────

def build_diff_summary(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Edit":
        old = (tool_input.get("old_string") or "")[:200]
        new = (tool_input.get("new_string") or "")[:200]
        return f"Edit: replace\n---\n{old}\n---\nwith\n---\n{new}\n---"
    if tool_name == "Write":
        content = (tool_input.get("content") or "")[:300]
        return f"Write new file:\n{content}..."
    return f"{tool_name} operation"


def propose_t1(file_path: str, tool_name: str, tool_input: dict) -> tuple[str, str]:
    """
    POST a T1 governance proposal.
    Returns (status, commit_id).
    Status: 'auto_approved' | 'distributed' | 'pending' | 'error'
    """
    summary = build_diff_summary(tool_name, tool_input)
    result = _post("/api/governance/propose", {
        "title": f"{tool_name}: {Path(file_path).name}",
        "proposer": AGENT_NAME,
        "summary": summary,
        "file_path": file_path,
        "diff": summary,
        "proposal_type": "Code Enhancement",
        "trust_level": "ENGINEER",
        "risk_level": "LOW",
    })
    if not result:
        return "error", ""
    status    = result.get("status", "error")
    commit_id = result.get("commit_id", "")
    return status, commit_id


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    tool_name  = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}

    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path:
        sys.exit(0)

    tier = classify_path(file_path)

    # T4 — operator config, pass through immediately
    if tier == "T4":
        sys.exit(0)

    # T3 — pass through
    if tier == "T3":
        sys.exit(0)

    willow_session_id = get_willow_session_id()

    # T2 — log + allow
    if tier == "T2":
        if willow_session_id:
            _post("/api/journal/event", {
                "username": USERNAME,
                "session_id": willow_session_id,
                "event_type": "tool_pre_t2",
                "payload": {
                    "tool": tool_name,
                    "file_path": file_path,
                    "tier": "T2",
                },
            })
        sys.exit(0)

    # T1 — propose + gate
    if tier == "T1":
        status, commit_id = propose_t1(file_path, tool_name, tool_input)

        if willow_session_id:
            _post("/api/journal/event", {
                "username": USERNAME,
                "session_id": willow_session_id,
                "event_type": "governance_gate",
                "payload": {
                    "tool": tool_name,
                    "file_path": file_path,
                    "tier": "T1",
                    "commit_id": commit_id,
                    "status": status,
                },
            })

        if status in ("auto_approved", "distributed"):
            # Precedent found — no human approval needed
            print(f"[GOVERNANCE] Auto-approved via precedent ({commit_id})", file=sys.stderr)
            sys.exit(0)

        if status == "pending":
            print(
                f"[GOVERNANCE GATE] T1 change requires ratification.\n"
                f"  File:      {file_path}\n"
                f"  Proposal:  {commit_id}\n"
                f"  Approve:   http://localhost:8420/governance\n"
                f"  Then retry this edit.",
                file=sys.stderr
            )
            sys.exit(2)  # block

        # MCP unreachable — warn but allow (don't silently block when server is down)
        print(
            f"[GOVERNANCE] Server unreachable — T1 edit proceeding unguarded.\n"
            f"  File: {file_path}\n"
            f"  Log this manually when server is restored.",
            file=sys.stderr
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
