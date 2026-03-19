"""
boot_status.py — AIOS Boot Status Contract

Shared module for the 9 ring hooks + 1 bootloader.
Each hook reports its status. The 10th reads all statuses
and generates the session prompt or presents degraded state.

Status files: /tmp/willow-boot/{session_id}/{ring}-{hook}.json
Atomic writes: write to .tmp, rename.

Authority: Sean Campbell
System: Willow
ΔΣ=42
"""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime

BOOT_DIR = Path("/tmp/willow-boot")

# The 9 hooks, organized by ring
RINGS = {
    "source": ["gate", "observe", "validate"],
    "bridge": ["open", "gate", "learn"],
    "continuity": ["open", "observe", "close"],
}

# All 9 hook names for status checking
ALL_HOOKS = []
for ring, hooks in RINGS.items():
    for hook in hooks:
        ALL_HOOKS.append(f"{ring}-{hook}")


def _session_dir(session_id: str) -> Path:
    """Get or create the boot status directory for this session."""
    d = BOOT_DIR / session_id[:16]
    d.mkdir(parents=True, exist_ok=True)
    return d


def report(session_id: str, ring: str, hook: str, ready: bool,
           detail: str = "", latency_ms: float = 0):
    """
    Report hook status. Atomic write — safe for concurrent hooks.

    ring: source | bridge | continuity
    hook: gate | observe | validate | open | learn | close
    ready: True if subsystem is operational
    detail: human-readable status or error message
    latency_ms: how long the check took
    """
    d = _session_dir(session_id)
    status = {
        "ring": ring,
        "hook": hook,
        "ready": ready,
        "detail": detail,
        "latency_ms": round(latency_ms, 1),
        "timestamp": datetime.now().isoformat(),
    }
    target = d / f"{ring}-{hook}.json"
    # Atomic write: temp file + rename
    fd, tmp_path = tempfile.mkstemp(dir=str(d), suffix=".tmp")
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(status, f)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def read_all(session_id: str) -> dict:
    """
    Read all hook statuses for a session.
    Returns: {
        "source": {"gate": {...}, "observe": {...}, "validate": {...}},
        "bridge": {"open": {...}, "gate": {...}, "learn": {...}},
        "continuity": {"open": {...}, "observe": {...}, "close": {...}},
        "summary": {"ready": 7, "not_ready": 1, "missing": 1, "total": 9}
    }
    """
    d = _session_dir(session_id)
    result = {}
    ready_count = 0
    not_ready_count = 0
    missing_count = 0

    for ring, hooks in RINGS.items():
        result[ring] = {}
        for hook in hooks:
            status_file = d / f"{ring}-{hook}.json"
            if status_file.exists():
                try:
                    with open(status_file) as f:
                        status = json.load(f)
                    result[ring][hook] = status
                    if status.get("ready"):
                        ready_count += 1
                    else:
                        not_ready_count += 1
                except Exception:
                    result[ring][hook] = {"ready": False, "detail": "corrupt status file"}
                    not_ready_count += 1
            else:
                result[ring][hook] = {"ready": False, "detail": "not reported"}
                missing_count += 1

    result["summary"] = {
        "ready": ready_count,
        "not_ready": not_ready_count,
        "missing": missing_count,
        "total": 9,
    }
    return result


def format_status(statuses: dict) -> str:
    """Format boot status for display to user."""
    lines = []
    summary = statuses["summary"]

    for ring_name in ["source", "bridge", "continuity"]:
        ring = statuses[ring_name]
        indicators = []
        for hook_name, status in ring.items():
            if status.get("ready"):
                ms = status.get("latency_ms", 0)
                indicators.append(f"✓ {hook_name} ({ms:.0f}ms)")
            else:
                detail = status.get("detail", "offline")
                indicators.append(f"✗ {hook_name} ({detail})")
        lines.append(f"  {ring_name.title():12s}  {'  '.join(indicators)}")

    ready = summary["ready"]
    total = summary["total"]
    return f"Boot: {ready}/{total} ready\n" + "\n".join(lines)


def cleanup(session_id: str):
    """Remove boot status files for a completed session."""
    d = BOOT_DIR / session_id[:16]
    if d.exists():
        for f in d.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass
