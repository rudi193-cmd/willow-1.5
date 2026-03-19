#!/usr/bin/env python3
"""
Stop Hook: Session Extract
===========================
Fires when a Claude Code session ends.

session_end lifecycle:
  1. Build handoff from session JSONL (fleet-summarized or basic fallback)
  2. Write SESSION_HANDOFF_*.md to Pickup (pigeon ingests it)
  3. Write JSONL copy to Nest (pigeon stages it through full pipeline)
  4. Trigger pigeon scan
  5. POST /api/journal/session/end — close journal session
  6. POST /api/agents/checkin — update last_seen
  7. Delete /tmp/willow-session-ganesha.json

Fallback: if Nest/Pickup writes fail, POST directly to /api/knowledge/ingest.

Input (stdin): {"session_id": "...", "stop_hook_active": true}
Exit 0: always (informational, never blocks)
"""

import io, json, sys, urllib.request, shutil
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

WILLOW_URL   = "http://localhost:8420"
USERNAME     = "Sweet-Pea-Rudi19"
AGENT_NAME   = "ganesha"
PROJECTS_DIR = Path("/home/sean/.claude/projects")
SESSION_FILE = Path(f"/tmp/willow-session-{AGENT_NAME}.json")
WILLOW_CORE  = "/mnt/c/Users/Sean/Documents/GitHub/Willow"
PICKUP_DIR   = Path("/mnt/c/Users/Sean/My Drive/Willow/Auth Users/Sweet-Pea-Rudi19/Pickup")
NEST_DIR     = Path("/mnt/c/Users/Sean/My Drive/Willow/Auth Users/ganesha/Nest")
FLEET_TURNS  = 20   # turns to include in fleet prompt
TRUNC_CHARS  = 450  # per-turn truncation


# ── HTTP helpers ─────────────────────────────────────────────────────────────

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


# ── Find + parse session JSONL ────────────────────────────────────────────────

def find_jsonl(session_id: str) -> Path | None:
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        candidate = proj_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    return None


def extract_content_text(content) -> str:
    if isinstance(content, str):
        return content[:TRUNC_CHARS]
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append((item.get("text") or "")[:200])
                elif item.get("type") == "tool_result":
                    c = item.get("content", "")
                    if isinstance(c, str):
                        parts.append(c[:100])
            elif isinstance(item, str):
                parts.append(item[:200])
        return " ".join(parts)[:TRUNC_CHARS]
    return ""


def extract_tool_names(content) -> list:
    if not isinstance(content, list):
        return []
    names = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            n = item.get("name", "")
            if n and n not in names:
                names.append(n)
    return names


def parse_turns(jsonl_path: Path):
    turns = []
    cwd = None
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type")
                if t not in ("user", "assistant"):
                    continue
                if cwd is None:
                    cwd = obj.get("cwd", "")
                msg = obj.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", t)
                content = msg.get("content", "")
                text = extract_content_text(content)
                tools = extract_tool_names(content) if role == "assistant" else []
                is_human = role == "user" and isinstance(content, str)
                if text or tools:
                    turns.append({
                        "role": role, "text": text, "tools": tools,
                        "ts": obj.get("timestamp", ""), "is_human": is_human,
                    })
    except Exception as e:
        print(f"[SESSION-EXTRACT] parse error: {e}", file=sys.stderr)
    return turns, cwd


# ── Build handoff content ─────────────────────────────────────────────────────

def build_fleet_prompt(turns, cwd, session_id):
    head = turns[:2]
    tail = turns[-(FLEET_TURNS - 2):] if len(turns) > 2 else []
    omitted = max(0, len(turns) - FLEET_TURNS)
    selected = head
    if omitted > 0:
        selected += [{"role": "...", "text": f"[...{omitted} turns omitted...]", "tools": []}]
    selected += tail

    lines = []
    for t in selected:
        label = "HUMAN" if t["role"] == "user" else "CLAUDE"
        tool_note = f" [{','.join(t['tools'][:3])}]" if t.get("tools") else ""
        lines.append(f"[{label}{tool_note}]: {t['text'][:300]}")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""Extract a SESSION_HANDOFF from this Claude Code session. Output ONLY markdown.

Date: {date_str} | Dir: {(cwd or 'unknown')[-40:]}

---TRANSCRIPT---
{chr(10).join(lines)}
---END TRANSCRIPT---

Write these sections:

# SESSION_HANDOFF {date_str}
## SESSION_META
- date/project/session_type (coding|debugging|planning|mixed)

## ENTITIES_REGISTRY
- **name** | type | role

## DECISIONS_LOG
### title
- Decided/Rationale/Impact

## TECHNICAL_DELTA
- `path` — created|modified — what changed

## KNOWLEDGE_ATOMS
1. standalone fact

## NARRATIVE_BEATS
One paragraph: what was hard, what was satisfying.

## NEXT_SESSION_SEEDS
- [ ] task

---
ΔΣ=42"""


def call_fleet(prompt: str) -> str | None:
    try:
        sys.path.insert(0, WILLOW_CORE)
        import llm_router
        llm_router.load_keys_from_json()
        resp = llm_router.ask(prompt, preferred_tier="free", task_type="text_summarization")
        if resp and resp.content and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        print(f"[SESSION-EXTRACT] fleet error: {e}", file=sys.stderr)
    return None


def basic_handoff(turns, cwd, session_id) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tools_used = []
    for t in turns:
        for tool in t.get("tools", []):
            if tool not in tools_used:
                tools_used.append(tool)
    user_msgs = [t["text"].strip()[:200] for t in turns if t.get("is_human") and len(t["text"].strip()) > 4]
    key_actions = [
        f"[{','.join(t['tools'][:3])}] {t['text'][:120]}"
        for t in turns if t["role"] == "assistant" and t.get("tools")
    ]
    return f"""# SESSION_HANDOFF {date_str}

## SESSION_META
- date: {date_str}
- session_id: {session_id[:8]}
- working_dir: {cwd or 'unknown'}
- turns: {len(turns)}
- tools_used: {', '.join(tools_used) or 'none recorded'}

## LAST_USER_MESSAGES
{chr(10).join(f'- {m}' for m in user_msgs[-8:]) or '- (none extracted)'}

## KEY_ACTIONS
{chr(10).join(f'- {a}' for a in key_actions[-10:]) or '- (none recorded)'}

---
ΔΣ=42
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = {}

    session_id = event.get("session_id", "")
    if not session_id:
        sys.exit(0)

    # Read session state from /tmp (willow_session_id for journal close)
    willow_session_id = ""
    try:
        if SESSION_FILE.exists():
            state = json.loads(SESSION_FILE.read_text())
            willow_session_id = state.get("willow_session_id", "")
    except Exception:
        pass

    # Find + parse JSONL
    jsonl_path = find_jsonl(session_id)
    if not jsonl_path:
        print(f"[SESSION-EXTRACT] JSONL not found for {session_id[:8]}", file=sys.stderr)
        sys.exit(0)

    turns, cwd = parse_turns(jsonl_path)
    turn_count = len(turns)
    print(f"[SESSION-EXTRACT] Building handoff for {session_id[:8]} ({turn_count} turns)...")

    # Build handoff — fleet for substantial sessions, basic fallback for short ones
    if turn_count >= 8:
        prompt = build_fleet_prompt(turns, cwd, session_id)
        content = call_fleet(prompt) or basic_handoff(turns, cwd, session_id)
    else:
        content = basic_handoff(turns, cwd, session_id)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    handoff_filename = f"SESSION_HANDOFF_{ts}.md"
    jsonl_copy_filename = f"SESSION_{session_id[:8]}_{ts}.jsonl"
    ingested = False

    # ── 1. Write SESSION_HANDOFF to Nest (pigeon ingests via full pipeline) ──
    try:
        if NEST_DIR.exists():
            (NEST_DIR / handoff_filename).write_text(content, encoding="utf-8")
            print(f"[SESSION-EXTRACT] Handoff → Nest: {handoff_filename}")
        else:
            print(f"[SESSION-EXTRACT] Nest not found: {NEST_DIR}", file=sys.stderr)
    except Exception as e:
        print(f"[SESSION-EXTRACT] Nest write failed: {e}", file=sys.stderr)

    # ── 1.5. Drop session_end event to agent nest ─────────────────────────────
    _post(f"/api/agents/{AGENT_NAME}/event", {
        "event_type": "session_end",
        "content": content[:500],
        "metadata": {
            "session_id": session_id[:8],
            "turn_count": turn_count,
            "handoff_file": handoff_filename,
        },
    })

    # ── 2. Copy JSONL to Nest (pigeon stages through full pipeline) ───────────
    try:
        if NEST_DIR.exists():
            shutil.copy2(str(jsonl_path), str(NEST_DIR / jsonl_copy_filename))
            print(f"[SESSION-EXTRACT] JSONL → Nest: {jsonl_copy_filename}")
        else:
            print(f"[SESSION-EXTRACT] Nest not found: {NEST_DIR}", file=sys.stderr)
    except Exception as e:
        print(f"[SESSION-EXTRACT] Nest write failed: {e}", file=sys.stderr)

    # ── 3. Trigger pigeon scan ────────────────────────────────────────────────
    scan_result = _post(f"/api/pigeon/scan", {"username": USERNAME})
    if scan_result:
        new_count = scan_result.get("new_droppings", 0)
        print(f"[SESSION-EXTRACT] Pigeon scan triggered: {new_count} new items staged")
    else:
        print(f"[SESSION-EXTRACT] Pigeon scan trigger failed (server unreachable)", file=sys.stderr)
        # Fallback: direct ingest so something always lands in knowledge
        result = _post("/api/knowledge/ingest", {
            "username": USERNAME,
            "filename": handoff_filename,
            "file_hash": "",
            "category": "narrative",
            "content_text": content[:6000],
            "provider": "session-extract-hook",
            "tags": ["ganesha", "handoff", ts[:8]],
        })
        if result:
            print(f"[SESSION-EXTRACT] Fallback ingest: {handoff_filename}")
            ingested = True

    # ── 4. Close journal session ──────────────────────────────────────────────
    if willow_session_id:
        _post("/api/journal/session/end", {
            "username": USERNAME,
            "session_id": willow_session_id,
        })

    # ── 5. Update agent presence ──────────────────────────────────────────────
    _post("/api/agents/checkin", {"agent_name": AGENT_NAME})

    # ── 6. Clean up session state file ───────────────────────────────────────
    try:
        SESSION_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
