#!/usr/bin/env bash
# willow.sh — Portless Willow Launcher
# No server. No ports. No daemons (except watchers).
#
# Starts: Ollama, Pigeon watchers, SAFE shell
# That's it.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WILLOW_DIR="/mnt/c/Users/Sean/Documents/GitHub/Willow"
STORE_ROOT="${WILLOW_STORE_ROOT:-$HOME/.willow/store}"

echo
echo "  W I L L O W"
echo "  Portless OS — No ports. No server."
echo "  ________________________________"
echo

# ── 1. Ollama ─────────────────────────────────────────────────────────
echo "[1/3] Ollama..."
if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "      already running."
else
    echo "      starting..."
    if command -v ollama >/dev/null 2>&1; then
        ollama serve >/dev/null 2>&1 &
        sleep 3
        if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
            echo "      OK"
        else
            echo "      [WARN] Ollama didn't start — fleet fallback active"
        fi
    else
        echo "      [WARN] Ollama not installed — fleet fallback active"
    fi
fi

# ── 2. Pigeon Watchers ───────────────────────────────────────────────
echo "[2/3] Pigeon watchers..."

# Nest watcher (filesystem intake)
if [ -f "$WILLOW_DIR/core/watcher.py" ]; then
    python3 "$WILLOW_DIR/core/watcher.py" --no-consent >> "$HOME/.willow/logs/nest_watcher.log" 2>&1 &
    echo "      nest watcher: PID $!"
fi

# Drive watcher (Google Drive sync)
if [ -f "$WILLOW_DIR/core/pigeon_drive_watcher.py" ]; then
    python3 "$WILLOW_DIR/core/pigeon_drive_watcher.py" --watch --interval 10 >> "$HOME/.willow/logs/drive_watcher.log" 2>&1 &
    echo "      drive watcher: PID $!"
fi

mkdir -p "$HOME/.willow/logs"

# ── 3. Shell ──────────────────────────────────────────────────────────
echo "[3/3] SAFE Shell..."
echo

# If --kart flag, launch Kart REPL instead of SAFE shell
if [ "$1" = "--kart" ]; then
    echo "  Launching Kart CLI (local chat, no Claude Code needed)..."
    echo
    if [ -f "$WILLOW_DIR/kart.py" ]; then
        python3 "$WILLOW_DIR/kart.py"
    else
        echo "  kart.py not found at $WILLOW_DIR/kart.py"
        echo "  Falling back to SAFE shell..."
        python3 "$SCRIPT_DIR/core/safe_shell.py" --store "$STORE_ROOT" --user "${USER:-local}"
    fi
else
    python3 "$SCRIPT_DIR/core/safe_shell.py" --store "$STORE_ROOT" --user "${USER:-local}"
fi

# ── Cleanup ───────────────────────────────────────────────────────────
echo
echo "  Shutting down watchers..."
jobs -p | xargs -r kill 2>/dev/null
echo "  Done. (Ollama left running)"
