#!/usr/bin/env python3
"""
safe_shell.py — SAFE Shell: Session-Authorized, Fully Explicit

A login shell where the OS IS the consent model.
No ports. No daemons. No server.
Session starts → consent prompt. Session ends → permissions gone.
Every command runs inside the consent scope.

Built on: WillowStore (portless storage) + SAFE consent model.
"""

import cmd
import json
import os
import readline
import signal
import sys
import uuid
from datetime import datetime
from pathlib import Path


# ── Import WillowStore ───────────────────────────────────────────────
# Resolve relative to this file so it works from anywhere
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from willow_store import WillowStore, angular_action, net_trajectory, Rubric, PI

# Optional: Postgres bridge (shell works without it)
try:
    from pg_bridge import try_connect as _try_pg
except ImportError:
    _try_pg = None

# Optional: Content resolver
try:
    from content_resolver import resolve_with_fallback as _resolve_content
except ImportError:
    _resolve_content = None


# ── SAFE Session ─────────────────────────────────────────────────────

# Data streams that can be authorized per session
STREAMS = {
    "journal":      "Journal entries and conversation history",
    "knowledge":    "Knowledge graph (atoms, edges, entities)",
    "agents":       "Agent working memory and state",
    "governance":   "Governance proposals and audit trail",
    "preferences":  "User preferences and settings",
    "media":        "Images, documents, and file references",
}

# Hard stops — cannot be authorized regardless of consent
HARD_STOPS = {
    "export_raw":   "HS-SHELL-001: Cannot export raw data outside session scope",
    "delete_audit": "HS-SHELL-002: Cannot delete audit trail",
    "self_grant":   "HS-SHELL-003: Shell cannot grant its own permissions",
    "bypass_consent": "HS-SHELL-004: Cannot bypass consent prompt on session start",
}


class SAFESession:
    """A single SAFE session. Consent expires when session ends."""

    def __init__(self, store_root: str, username: str = "local"):
        self.session_id = uuid.uuid4().hex[:12]
        self.username = username
        self.started_at = datetime.now()
        self.ended_at = None
        self.authorized_streams = set()
        self.denied_streams = set()
        self.store = WillowStore(store_root)
        self.audit_entries = []
        self._active = False

    def request_consent(self) -> bool:
        """Consent prompt. Human decides what this session can access."""
        print()
        print("=" * 60)
        print("  SAFE Session Authorization")
        print(f"  Session: {self.session_id}")
        print(f"  User: {self.username}")
        print(f"  Time: {self.started_at.strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        print()
        print("This session requests access to the following data streams.")
        print("You decide what to authorize. Permissions expire when you exit.")
        print()

        for key, desc in STREAMS.items():
            while True:
                response = input(f"  {key}: {desc}\n  Authorize? [y/n]: ").strip().lower()
                if response in ("y", "yes"):
                    self.authorized_streams.add(key)
                    self._audit("CONSENT_GRANTED", key)
                    break
                elif response in ("n", "no"):
                    self.denied_streams.add(key)
                    self._audit("CONSENT_DENIED", key)
                    break
                else:
                    print("  Please answer y or n.")

        if not self.authorized_streams:
            print()
            print("No streams authorized. Session has read-only system access.")

        print()
        print(f"Authorized: {', '.join(sorted(self.authorized_streams)) or 'none'}")
        print(f"Denied: {', '.join(sorted(self.denied_streams)) or 'none'}")
        print()

        self._active = True
        self._audit("SESSION_START", "shell")
        return True

    def check(self, stream: str) -> bool:
        """Check if a stream is authorized for this session."""
        if not self._active:
            return False
        return stream in self.authorized_streams

    def require(self, stream: str) -> bool:
        """Check and print denial if not authorized. Returns True if OK."""
        if self.check(stream):
            return True
        print(f"  Access denied: '{stream}' not authorized this session.")
        self._audit("ACCESS_DENIED", stream)
        return False

    def revoke(self, stream: str) -> bool:
        """Revoke a stream mid-session."""
        if stream in self.authorized_streams:
            self.authorized_streams.discard(stream)
            self.denied_streams.add(stream)
            self._audit("MID_SESSION_REVOKE", stream)
            print(f"  Revoked: {stream}")
            return True
        print(f"  '{stream}' was not authorized.")
        return False

    def end(self):
        """End session. All permissions gone."""
        self.ended_at = datetime.now()
        self._active = False
        self._audit("SESSION_END", "shell")

        # Persist audit to store (audit is always writable)
        try:
            self.store.put("safe/sessions", {
                "session_id": self.session_id,
                "username": self.username,
                "started": self.started_at.isoformat(),
                "ended": self.ended_at.isoformat(),
                "authorized": sorted(self.authorized_streams | self.denied_streams),
                "granted": sorted(self.authorized_streams),
                "denied": sorted(self.denied_streams),
                "audit": self.audit_entries,
            }, record_id=self.session_id)
        except Exception:
            pass  # Don't block exit on audit write failure

        self.authorized_streams.clear()
        self.denied_streams.clear()
        self.store.close()

    def _audit(self, event: str, target: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session": self.session_id,
            "event": event,
            "target": target,
            "user": self.username,
        }
        self.audit_entries.append(entry)


# ── Shell ─────────────────────────────────────────────────────────────

class SAFEShell(cmd.Cmd):
    """SAFE Shell — the OS is the consent model."""

    intro = None  # We handle our own intro after consent
    prompt = "safe> "

    def __init__(self, store_root: str = None, username: str = "local"):
        super().__init__()
        root = store_root or os.environ.get("WILLOW_STORE", str(Path.home() / ".willow" / "store"))
        self.session = SAFESession(root, username)
        self.store = None  # Set after consent
        self.pg = None  # Set in preloop if Postgres available

        # History
        histfile = Path.home() / ".willow" / ".safe_history"
        histfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            readline.read_history_file(str(histfile))
        except FileNotFoundError:
            pass
        self._histfile = histfile

        # Handle Ctrl+C gracefully
        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, sig, frame):
        print("\n  Use 'exit' to end session properly (revokes all permissions).")
        print(self.prompt, end="", flush=True)

    def preloop(self):
        """Consent before anything else."""
        ok = self.session.request_consent()
        if not ok:
            print("Session cancelled.")
            sys.exit(0)
        self.store = self.session.store

        # Try Postgres bridge
        if _try_pg:
            self.pg = _try_pg()
            if self.pg:
                stats = self.pg.stats()
                print(f"  Postgres: connected ({stats.get('knowledge', '?')} atoms, {stats.get('edges', '?')} edges)")
            else:
                print("  Postgres: unavailable (standalone mode)")
        else:
            print("  Postgres: not installed (standalone mode)")

        print("Type 'help' for commands. Type 'exit' to end session.\n")

    def postloop(self):
        """Session end — permissions gone."""
        self.session.end()
        try:
            readline.write_history_file(str(self._histfile))
        except Exception:
            pass
        print()
        print(f"Session {self.session.session_id} ended.")
        print("All permissions revoked. Audit logged.")

    def emptyline(self):
        pass

    def default(self, line):
        print(f"  Unknown command: {line}")
        print("  Type 'help' for available commands.")

    # ── Store commands ────────────────────────────────────────────────

    def do_put(self, arg):
        """put <collection> <key> <json> — Write a record"""
        parts = arg.split(None, 2)
        if len(parts) < 3:
            print("  Usage: put <collection> <key> <json>")
            return
        collection, key, data_str = parts

        stream = self._stream_for_collection(collection)
        if not self.session.require(stream):
            return

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            # Treat as plain text
            data = {"text": data_str}

        try:
            rid, action = self.store.put(collection, data, record_id=key)
            print(f"  {rid} [{action}]")
        except ValueError as e:
            print(f"  Error: {e}")

    def do_get(self, arg):
        """get <collection> <key> — Read a record"""
        parts = arg.split()
        if len(parts) != 2:
            print("  Usage: get <collection> <key>")
            return
        collection, key = parts

        stream = self._stream_for_collection(collection)
        if not self.session.require(stream):
            return

        record = self.store.get(collection, key)
        if record:
            print(json.dumps(record, indent=2, default=str, ensure_ascii=False))
        else:
            print(f"  Not found: {key}")

    def do_search(self, arg):
        """search <collection> <query> — Search within a collection"""
        parts = arg.split(None, 1)
        if len(parts) < 2:
            print("  Usage: search <collection> <query>")
            return
        collection, query = parts

        stream = self._stream_for_collection(collection)
        if not self.session.require(stream):
            return

        results = self.store.search(collection, query)
        if results:
            for r in results:
                print(f"  {r.get('_id', '?')}: {json.dumps({k: v for k, v in r.items() if not k.startswith('_')}, default=str)}")
        else:
            print("  No results.")

    def do_ask(self, arg):
        """ask <query> — Retrieval cascade: local → Postgres → (future: fleet)"""
        if not arg.strip():
            print("  Usage: ask <query>")
            return

        query = arg.strip()
        total_shown = 0

        # Tier 1: Local WillowStore
        results = self.store.search_all(query)
        visible = []
        for r in results:
            col = r.get("_collection", "")
            stream = self._stream_for_collection(col)
            if self.session.check(stream):
                visible.append(r)

        if visible:
            print("  — Local store:")
            for r in visible:
                col = r.get("_collection", "?")
                rid = r.get("_id", "?")
                preview = json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, default=str, ensure_ascii=False)
                if len(preview) > 120:
                    preview = preview[:117] + "..."
                print(f"    [{col}] {rid}: {preview}")
            total_shown += len(visible)
            hidden = len(results) - len(visible)
            if hidden:
                print(f"    ({hidden} hidden — streams not authorized)")

        # Tier 2: Postgres knowledge graph
        if self.pg and self.session.check("knowledge"):
            pg_results = self.pg.search_knowledge(query, limit=10)
            if pg_results:
                print("  — Knowledge graph:")
                for r in pg_results:
                    title = r.get("title", "?")[:60]
                    cat = r.get("category", "?")
                    src = r.get("source_id", "?")[:30]
                    print(f"    [{cat}] {title} (src:{src})")
                total_shown += len(pg_results)

            # Also search ganesha atoms
            ga_results = self.pg.search_ganesha(query, limit=5)
            if ga_results:
                print("  — Ganesha atoms:")
                for r in ga_results:
                    title = r.get("title", "?")[:60]
                    domain = r.get("domain", "?")
                    print(f"    [{domain}] {title}")
                total_shown += len(ga_results)

            # Entity search
            ent_results = self.pg.search_entities(query, limit=5)
            if ent_results:
                print("  — Entities:")
                for e in ent_results:
                    print(f"    {e['name']} ({e.get('entity_type', '?')}, {e.get('mention_count', 0)} mentions)")
                total_shown += len(ent_results)

        if total_shown == 0:
            print("  No results.")
        else:
            print(f"\n  {total_shown} result(s) total.")

    def do_ls(self, arg):
        """ls [collection] — List record IDs, or list collections"""
        if not arg.strip():
            # List all collections
            stats = self.store.stats()
            if stats:
                for col, info in sorted(stats.items()):
                    stream = self._stream_for_collection(col)
                    authorized = "+" if self.session.check(stream) else "-"
                    print(f"  {authorized} {col} ({info['count']} records, {info['trajectory']})")
                print()
                print("  + = authorized this session, - = not authorized")
            else:
                print("  No collections.")
            return

        collection = arg.strip()
        stream = self._stream_for_collection(collection)
        if not self.session.require(stream):
            return

        ids = self.store.list_ids(collection)
        if ids:
            for rid in ids:
                print(f"  {rid}")
        else:
            print("  Empty collection.")

    def do_update(self, arg):
        """update <collection> <key> <json> — Update an existing record"""
        parts = arg.split(None, 2)
        if len(parts) < 3:
            print("  Usage: update <collection> <key> <json>")
            return
        collection, key, data_str = parts

        stream = self._stream_for_collection(collection)
        if not self.session.require(stream):
            return

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = {"text": data_str}

        try:
            rid, action = self.store.update(collection, key, data)
            print(f"  {rid} [{action}]")
        except ValueError as e:
            print(f"  Error: {e}")

    def do_delete(self, arg):
        """delete <collection> <key> — Soft-delete a record"""
        parts = arg.split()
        if len(parts) != 2:
            print("  Usage: delete <collection> <key>")
            return
        collection, key = parts

        stream = self._stream_for_collection(collection)
        if not self.session.require(stream):
            return

        if self.store.delete(collection, key):
            print(f"  Deleted: {key}")
        else:
            print(f"  Not found: {key}")

    # ── Edge commands ─────────────────────────────────────────────────

    def do_edge(self, arg):
        """edge <from> <relation> <to> [context] — Add a knowledge edge"""
        if not self.session.require("knowledge"):
            return

        parts = arg.split(None, 3)
        if len(parts) < 3:
            print("  Usage: edge <from_id> <relation> <to_id> [context]")
            return

        from_id, relation, to_id = parts[0], parts[1], parts[2]
        context = parts[3] if len(parts) > 3 else ""

        try:
            rid, action = self.store.add_edge(from_id, to_id, relation, context)
            print(f"  {rid} [{action}]")
        except ValueError as e:
            print(f"  Error: {e}")

    def do_edges(self, arg):
        """edges <id> — Show all edges for a record"""
        if not self.session.require("knowledge"):
            return

        if not arg.strip():
            print("  Usage: edges <record_id>")
            return

        results = self.store.edges_for(arg.strip())
        if results:
            for r in results:
                print(f"  {r.get('from', '?')} --[{r.get('relation', '?')}]--> {r.get('to', '?')}")
        else:
            print("  No edges found.")

    # ── Chat & Ingest ────────────────────────────────────────────────

    def do_chat(self, arg):
        """chat <agent> <message> — Talk to an agent (Ollama local → fleet)"""
        if not self.session.require("agents"):
            return

        parts = arg.split(None, 1)
        if len(parts) < 2:
            print("  Usage: chat <agent> <message>")
            print("  Agents: willow, kart, shiva, gerald, riggs, ada, steve")
            return

        agent_name, message = parts[0].lower(), parts[1]

        # Try local Ollama first
        response = self._chat_ollama(agent_name, message)
        if response:
            print(f"  [{agent_name}] {response}")
            # Log to local store
            try:
                self.store.put(f"agents/{agent_name}/conversations", {
                    "user": message, "agent": response,
                    "session": self.session.session_id,
                }, record_id=None)
            except Exception:
                pass
            return

        # Fleet fallback (if available)
        response = self._chat_fleet(agent_name, message)
        if response:
            print(f"  [{agent_name}] {response}")
            return

        print(f"  No inference available. Ollama not running, fleet not reachable.")

    def _chat_ollama(self, agent: str, message: str) -> str | None:
        """Try local Ollama."""
        try:
            import urllib.request
            data = json.dumps({
                "model": os.environ.get("WILLOW_OLLAMA_MODEL", "llama3.2"),
                "messages": [
                    {"role": "system", "content": f"You are {agent}, a Willow agent. Be concise."},
                    {"role": "user", "content": message},
                ],
                "stream": False,
            }).encode()
            url = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                return result.get("message", {}).get("content", "")
        except Exception:
            return None

    def _chat_fleet(self, agent: str, message: str) -> str | None:
        """Try free fleet via llm_router."""
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "llm_router",
                "/mnt/c/Users/Sean/Documents/GitHub/Willow/core/llm_router.py"
            )
            if not spec:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.load_keys_from_json()
            response = mod.ask(
                f"[{agent}] {message}",
                preferred_tier="free",
                task_type="conversation",
            )
            return response.content if response else None
        except Exception:
            return None

    def do_ingest(self, arg):
        """ingest <title> | <summary> — Add atom to knowledge graph"""
        if not self.session.require("knowledge"):
            return
        if not self.pg:
            print("  Postgres not available. Cannot ingest to knowledge graph.")
            return

        parts = arg.split("|", 1)
        if len(parts) < 2:
            print("  Usage: ingest <title> | <summary>")
            return

        title = parts[0].strip()
        summary = parts[1].strip()

        atom_id = self.pg.ingest_atom(
            title=title, summary=summary,
            source_type="shell", source_id=f"safe-shell-{self.session.session_id}",
            category="manual",
        )
        if atom_id:
            print(f"  Ingested: atom #{atom_id} — {title}")
        else:
            print("  Failed to ingest atom.")

    # ── Session commands ──────────────────────────────────────────────

    def do_session(self, arg):
        """session — Show current session info"""
        print(f"  Session: {self.session.session_id}")
        print(f"  User: {self.session.username}")
        print(f"  Started: {self.session.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Authorized: {', '.join(sorted(self.session.authorized_streams)) or 'none'}")
        print(f"  Denied: {', '.join(sorted(self.session.denied_streams)) or 'none'}")

    def do_revoke(self, arg):
        """revoke <stream> — Revoke a stream mid-session"""
        if not arg.strip():
            print("  Usage: revoke <stream>")
            print(f"  Active: {', '.join(sorted(self.session.authorized_streams))}")
            return
        self.session.revoke(arg.strip())

    def do_audit(self, arg):
        """audit [collection] — Show audit log"""
        # Audit is always readable (it's the consent proof)
        if arg.strip():
            entries = self.store.audit_log(arg.strip())
            for e in entries:
                print(f"  {e['timestamp']} | {e['operation']} | {e['record_id']} [{e.get('action', '')}]")
        else:
            # Show session audit
            for e in self.session.audit_entries:
                print(f"  {e['timestamp']} | {e['event']} | {e['target']}")

    def do_stats(self, arg):
        """stats — Show local + Postgres statistics"""
        print("  — Local store:")
        stats = self.store.stats()
        if stats:
            total = 0
            for col, info in sorted(stats.items()):
                total += info["count"]
                print(f"    {col}: {info['count']} records ({info['trajectory']})")
            print(f"    Total: {total} records across {len(stats)} collections")
        else:
            print("    Empty.")

        if self.pg:
            print("  — Postgres:")
            pg_stats = self.pg.stats()
            for k, v in pg_stats.items():
                print(f"    {k}: {v:,}")
        else:
            print("  — Postgres: not connected")

    # ── System commands ───────────────────────────────────────────────

    def do_streams(self, arg):
        """streams — List available data streams"""
        for key, desc in STREAMS.items():
            status = "AUTHORIZED" if self.session.check(key) else "denied"
            print(f"  [{status:>10}] {key}: {desc}")

    def do_hardstops(self, arg):
        """hardstops — Show hard stops (things the system will never do)"""
        for code, desc in HARD_STOPS.items():
            print(f"  {desc}")

    def do_rubric(self, arg):
        """rubric [verbose|default|quiet] or rubric <quiet> <flag> — View or set notification thresholds"""
        import math
        rubric = self.store.rubric

        if not arg.strip():
            print(f"  quiet below: {rubric.quiet_below:.4f} rad ({math.degrees(rubric.quiet_below):.1f}°)")
            print(f"  flag  below: {rubric.flag_below:.4f} rad ({math.degrees(rubric.flag_below):.1f}°)")
            print(f"  stop  above: {rubric.flag_below:.4f} rad ({math.degrees(rubric.flag_below):.1f}°)")
            if rubric.hard_stops:
                print(f"  hard stops:  {sorted(rubric.hard_stops)}")
            print(f"\n  Presets: verbose (π/8, π/4), default (π/4, π/2), quiet (π/2, 3π/4)")
            return

        parts = arg.strip().split()
        if parts[0] == "verbose":
            self.store.rubric = Rubric.verbose()
            print("  Rubric set to verbose (π/8 quiet, π/4 flag)")
        elif parts[0] == "default":
            self.store.rubric = Rubric.default()
            print("  Rubric set to default (π/4 quiet, π/2 flag)")
        elif parts[0] == "quiet":
            self.store.rubric = Rubric.quiet()
            print("  Rubric set to quiet (π/2 quiet, 3π/4 flag)")
        elif len(parts) >= 2:
            try:
                q = float(parts[0])
                f = float(parts[1])
                self.store.rubric = Rubric(quiet_below=q, flag_below=f)
                print(f"  Rubric set: quiet below {q:.4f}, flag below {f:.4f}")
            except (ValueError, Exception) as e:
                print(f"  Error: {e}")
        else:
            print("  Usage: rubric [verbose|default|quiet] or rubric <quiet_rad> <flag_rad>")

    def do_exit(self, arg):
        """exit — End session, revoke all permissions"""
        return True

    def do_quit(self, arg):
        """quit — End session, revoke all permissions"""
        return True

    do_EOF = do_exit

    # ── Stream mapping ────────────────────────────────────────────────

    def _stream_for_collection(self, collection: str) -> str:
        """Map a collection path to a SAFE stream."""
        col = collection.lower()
        if col.startswith("journal") or col.startswith("conversation"):
            return "journal"
        if col.startswith("knowledge") or col.startswith("atoms") or col.startswith("edges"):
            return "knowledge"
        if col.startswith("agent") or col.startswith("working"):
            return "agents"
        if col.startswith("governance") or col.startswith("safe"):
            return "governance"
        if col.startswith("pref") or col.startswith("settings") or col.startswith("config"):
            return "preferences"
        if col.startswith("media") or col.startswith("files") or col.startswith("images"):
            return "media"
        # Default: require knowledge (most restrictive common stream)
        return "knowledge"


# ── Entry point ───────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="SAFE Shell — Session-Authorized, Fully Explicit",
        epilog="The OS is the consent model. Permissions expire when you exit.",
    )
    parser.add_argument("--store", default=None,
                        help="Store root directory (default: ~/.willow/store)")
    parser.add_argument("--user", default=os.environ.get("USER", "local"),
                        help="Username for session (default: $USER)")
    args = parser.parse_args()

    shell = SAFEShell(store_root=args.store, username=args.user)
    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        shell.session.end()
        print("\nSession terminated.")


if __name__ == "__main__":
    main()
