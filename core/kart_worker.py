#!/usr/bin/env python3
"""
kart_worker.py — KART (1.5)
=============================
K — Kinetic
A — Agent
R — Runtime
T — Tasks

Task queue consumer. Polls kart_task_queue, claims pending tasks,
executes them through the Willow agent engine, writes results back.

Usage:
    python kart_worker.py              # run once (claim + execute one task)
    python kart_worker.py --daemon     # poll continuously (5s interval)
    python kart_worker.py --status     # show queue stats
"""

import json
import os
import sys
import time
from pathlib import Path

# Add Willow root to path for agent_engine access
WILLOW_ROOT = Path(os.environ.get("WILLOW_ROOT", "/mnt/c/Users/Sean/Documents/GitHub/Willow"))
sys.path.insert(0, str(WILLOW_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from pg_bridge import try_connect


def execute_task(task_text: str, agent_name: str = "kart") -> dict:
    """Execute a task through Willow's agent engine."""
    try:
        from core.agent_engine import AgentEngine
        engine = AgentEngine("Sweet-Pea-Rudi19", agent_name)

        conversation = []
        all_tools = []
        current_message = task_text
        max_steps = 15

        for step in range(1, max_steps + 1):
            result = engine.chat(current_message, conversation_history=conversation)

            if not isinstance(result, dict):
                return {"success": False, "error": "Invalid response", "steps": step}

            response = result.get("response", "")
            tools = result.get("tool_calls", [])

            if tools:
                all_tools.extend(tools)

            conversation.append({"role": "user", "content": current_message})
            conversation.append({"role": "assistant", "content": response})

            if not tools and response:
                return {
                    "success": True,
                    "response": response,
                    "steps": step,
                    "tools_used": len(all_tools),
                    "provider": result.get("provider", "unknown"),
                }

            if tools:
                summary = [
                    f"{t.get('tool')}: {'ok' if t.get('result', {}).get('success') else 'fail'}"
                    for t in tools
                ]
                current_message = f"Results: {'; '.join(summary)}. Continue."
            else:
                current_message = "Continue task."

        return {
            "success": False,
            "error": f"Max steps reached ({max_steps})",
            "steps": max_steps,
            "tools_used": len(all_tools),
        }
    except Exception as e:
        return {"success": False, "error": str(e), "steps": 0}


def run_once(pg) -> bool:
    """Claim and execute one task. Returns True if a task was processed."""
    task = pg.claim_task("kart")
    if not task:
        return False

    task_id = task["task_id"]
    task_text = task["task"]
    print(f"[kart] Claimed {task_id}: {task_text[:80]}...")

    result = execute_task(task_text)

    if result.get("success"):
        pg.complete_task(task_id, result, steps=result.get("steps", 0))
        print(f"[kart] Complete {task_id}: {result.get('steps', 0)} steps, {result.get('tools_used', 0)} tools")
    else:
        pg.fail_task(task_id, result.get("error", "unknown"))
        print(f"[kart] Failed {task_id}: {result.get('error', 'unknown')}")

    return True


def daemon(pg, interval: int = 5):
    """Poll continuously."""
    print(f"[kart] Worker daemon started (poll every {interval}s)")
    while True:
        try:
            if not run_once(pg):
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[kart] Worker stopped")
            break
        except Exception as e:
            print(f"[kart] Error: {e}")
            time.sleep(interval)


def show_status(pg):
    """Show queue stats."""
    try:
        conn = pg._get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT status, COUNT(*) FROM kart_task_queue GROUP BY status ORDER BY status
        """)
        rows = cur.fetchall()
        cur.close()
        if not rows:
            print("[kart] Queue empty")
        else:
            for status, count in rows:
                print(f"  {status}: {count}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    pg = try_connect()
    if not pg:
        print("[kart] Cannot connect to Postgres")
        sys.exit(1)

    if "--status" in sys.argv:
        show_status(pg)
    elif "--daemon" in sys.argv:
        daemon(pg)
    else:
        if not run_once(pg):
            print("[kart] No pending tasks")


if __name__ == "__main__":
    main()
