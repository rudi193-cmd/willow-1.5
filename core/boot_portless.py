#!/usr/bin/env python3
"""
boot_portless.py — Pre-flight check for portless Willow

No port needed. Checks:
1. WillowStore root accessible (required)
2. Postgres reachable (optional — degraded without)
3. Ollama running (optional — fleet fallback)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def boot_check() -> dict:
    """Pre-flight for portless mode."""
    store_root = os.environ.get("WILLOW_STORE_ROOT", str(Path.home() / ".willow" / "store"))
    result = {
        "mode": "portless",
        "store": _check_store(store_root),
        "postgres": _check_postgres(),
        "ollama": _check_ollama(),
    }
    result["ready"] = result["store"]["ok"]  # Store is the only hard requirement
    result["degraded"] = not result["postgres"]["ok"] or not result["ollama"]["ok"]
    return result


def _check_store(root: str) -> dict:
    """Check if WillowStore root is accessible."""
    p = Path(root)
    if p.exists():
        dbs = list(p.rglob("store.db"))
        return {"ok": True, "root": root, "collections": len(dbs)}
    # Try to create it
    try:
        p.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "root": root, "collections": 0, "created": True}
    except Exception as e:
        return {"ok": False, "root": root, "error": str(e)}


def _check_postgres() -> dict:
    """Check if Postgres is reachable."""
    try:
        from pg_bridge import try_connect
        bridge = try_connect()
        if bridge:
            stats = bridge.stats()
            bridge.close()
            return {"ok": True, "atoms": stats.get("knowledge", 0), "edges": stats.get("edges", 0)}
        return {"ok": False, "reason": "connection failed"}
    except ImportError:
        return {"ok": False, "reason": "pg_bridge not available"}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _check_ollama() -> dict:
    """Check if Ollama is running."""
    try:
        import urllib.request
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/tags"
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return {"ok": True, "models": models}
    except Exception:
        return {"ok": False, "reason": "not running"}


def main():
    result = boot_check()
    print(json.dumps(result, indent=2))

    # Summary
    store = result["store"]
    pg = result["postgres"]
    ollama = result["ollama"]

    print(f"\nStore:    {'OK' if store['ok'] else 'FAIL'} — {store.get('root', '?')} ({store.get('collections', 0)} collections)")
    print(f"Postgres: {'OK' if pg['ok'] else 'DEGRADED'} — {pg.get('atoms', pg.get('reason', '?'))} atoms")
    print(f"Ollama:   {'OK' if ollama['ok'] else 'DEGRADED'} — {', '.join(ollama.get('models', [])) or ollama.get('reason', '?')}")
    print(f"\nReady: {result['ready']}  Degraded: {result['degraded']}")


if __name__ == "__main__":
    main()
