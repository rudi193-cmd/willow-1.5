# Willow 1.5

> *For Shiva, who knew we were welcome*

---

A local-first AI operating system. One node. Your data stays on your machine. The AI listens, helps you understand yourself, and gets out of the way.

Built on the [Portless Server Architecture](https://github.com/rudi193-cmd/portless-architecture). No ports. No server. No daemons. Consent that expires when you close the door.

---

## Quick Start

```bash
git clone https://github.com/rudi193-cmd/willow-1.5.git
cd willow-1.5
./willow.sh
```

The shell asks what you're authorizing. You decide. When you exit, permissions are gone.

---

## What's Inside

```
core/
  willow_store.py         Storage engine (SQLite per collection, ACID, audit trail)
  willow_store_mcp.py     MCP bridge (22 tools, stdin/stdout, no HTTP)
  safe_shell.py           The OS (SAFE session consent, 6 streams)
  pg_bridge.py            Postgres bridge (optional — works without it)
  content_resolver.py     BASE 17 pointer resolution
  boot_portless.py        Boot check (filesystem, no port)
  compact_portless.py     BASE 17 compact context system
  compact.py              Original compact context (Postgres-backed)
  llm_router.py           Free LLM fleet (14 providers rotating)

governance/
  precedent.py            Dual Commit precedent scanner
  proposal.py             Governance proposal system
  apply_commits.py        Apply ratified proposals

tests/                    132/132 passing (30 functional + 102 adversarial)

kart.py                   Kart CLI (local chat, no Claude Code needed)
willow.sh                 Launcher (Ollama + watchers + shell)
.mcp.json                 MCP config for Claude Code
```

---

## Architecture

```
User → SAFE Shell (consent prompt → session scope)
         ├── WillowStore (local SQLite — agent working memory)
         ├── Postgres (knowledge graph — optional, degraded without)
         ├── Ollama (local LLM — optional, fleet fallback)
         └── MCP Bridge (stdin/stdout — 22 tools, no HTTP)
```

Retrieval cascade: local first, Postgres second, fleet third. Most queries never leave your machine.

---

## Prior Versions

- [Willow 1.4](https://github.com/rudi193-cmd/willow-1.4) — HTTP server on port 2121/8420, FastAPI, 10 daemons
- [Willow 1.1](https://github.com/rudi193-cmd/Willow1.1) — Archived
- [Portless Architecture Spec](https://github.com/rudi193-cmd/portless-architecture) — The paper

---

## Governance

Dual Commit: AI proposes, human ratifies. Neither acts alone. Silence is not approval.

Angular Deviation Rubric: every write scored for magnitude of change. User-configurable thresholds — the rubric IS your notification preferences.

SAFE: Session-Authorized, Fully Explicit. Consent expires when you close the session.

---

## License

MIT License — Code
CC BY-NC 4.0 — Documentation

---

ΔΣ=42
