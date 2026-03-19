#!/usr/bin/env python3
"""
impact-analysis.py — PreToolUse:Edit hook for Willow core/ Python files.

Fires before any Edit to a .py file in core/. Answers:
  1. What functions are being changed?
  2. What calls those functions?
  3. Are any callers silently swallowing exceptions?
  4. Are there downstream silent failure patterns?

Outputs a structured warning to stdout (shown as system reminder to Claude).
Never blocks — exits 0 always. Just surfaces what Claude might miss.
"""

import json
import os
import re
import sys
from pathlib import Path

WILLOW_ROOT = Path("/mnt/c/Users/Sean/Documents/GitHub/Willow")
CORE_PATH   = WILLOW_ROOT / "core"

# Patterns that indicate silent failure
SILENT_PATTERNS = [
    (r"except\s+Exception\s*:\s*\n\s*pass",          "bare except:pass — swallows all errors"),
    (r"except\s*:\s*\n\s*pass",                       "bare except:pass — swallows all errors"),
    (r"except\s+Exception\s+as\s+\w+\s*:\s*\n\s*pass","except Exception as e: pass — swallows error"),
    (r"\.lastrowid\b",                                 "lastrowid may be None — check before use"),
    (r"\.fetchone\(\)\[0\]",                           "fetchone()[0] crashes if row is None"),
    (r"conn\.close\(\)\s*\n\s*return",                 "conn.close() before return — no try/finally"),
]


def extract_function_names(text: str) -> list:
    """Pull function names from a code snippet."""
    names = []
    # def foo( patterns
    for m in re.finditer(r"def\s+(\w+)\s*\(", text):
        names.append(m.group(1))
    # Also catch standalone identifiers that look like function calls
    # e.g. if old_string contains a call like ingest_file_knowledge(
    for m in re.finditer(r"(\w+)\s*\(", text):
        name = m.group(1)
        if len(name) > 4 and name not in ("self", "True", "False", "None", "print", "len", "str", "int", "dict", "list"):
            names.append(name)
    return list(dict.fromkeys(names))  # dedupe, preserve order


def find_callers(func_name: str, skip_file: str) -> list:
    """Find files that call func_name, excluding the file being edited."""
    callers = []
    pattern = re.compile(rf"\b{re.escape(func_name)}\s*\(")

    search_dirs = [WILLOW_ROOT / "core", WILLOW_ROOT / "api", WILLOW_ROOT]
    seen = set()

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        glob = search_dir.glob("*.py") if search_dir == WILLOW_ROOT else search_dir.rglob("*.py")
        for py_file in glob:
            if "__pycache__" in str(py_file):
                continue
            if str(py_file) in seen:
                continue
            if str(py_file) == skip_file:
                continue
            seen.add(str(py_file))
            try:
                text = py_file.read_text(encoding="utf-8", errors="ignore")
                if pattern.search(text):
                    callers.append(str(py_file.relative_to(WILLOW_ROOT)))
            except Exception:
                pass
    return callers


def check_silent_failures(file_path: str) -> list:
    """Scan a file for known silent failure patterns."""
    found = []
    try:
        text = Path(WILLOW_ROOT / file_path).read_text(encoding="utf-8", errors="ignore")
        for pat, desc in SILENT_PATTERNS:
            if re.search(pat, text):
                found.append(desc)
    except Exception:
        pass
    return found


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    file_path = data.get("file_path", "") or data.get("tool_input", {}).get("file_path", "")
    old_string = data.get("old_string", "") or data.get("tool_input", {}).get("old_string", "")

    # Only fire for Python files under Willow core/
    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)
    if str(CORE_PATH) not in file_path and "Willow/core" not in file_path:
        sys.exit(0)

    if not old_string:
        sys.exit(0)

    # What functions are changing?
    func_names = extract_function_names(old_string)
    if not func_names:
        sys.exit(0)

    # Limit to most meaningful names (avoid noise)
    func_names = [f for f in func_names if len(f) > 5][:5]
    if not func_names:
        sys.exit(0)

    rel_path = str(Path(file_path).relative_to(WILLOW_ROOT)) if WILLOW_ROOT.as_posix() in file_path else file_path

    lines = [f"[IMPACT] Editing {rel_path}"]

    any_findings = False

    for func in func_names:
        callers = find_callers(func, file_path)
        if not callers:
            continue

        any_findings = True
        lines.append(f"\n  {func}() is called by:")
        for c in callers[:6]:
            silent = check_silent_failures(c)
            silent_str = f"  ⚠ {', '.join(silent[:2])}" if silent else ""
            lines.append(f"    → {c}{silent_str}")

    if not any_findings:
        sys.exit(0)

    # Check the file being edited for silent patterns too
    edit_silent = check_silent_failures(file_path)
    if edit_silent:
        lines.append(f"\n  Silent failure patterns in {rel_path}:")
        for s in edit_silent[:3]:
            lines.append(f"    ⚠ {s}")

    print("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    main()
