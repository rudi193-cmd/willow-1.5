# Willow 1.5

> *For Shiva, who knew we were welcome*

---

Once there was a keeper who wanted to understand herself.

Not in the way of philosophers, though she had read some of them. Not in the way of therapists, though she had sat in those chairs too. In the plain way — the way you want to understand a room you have lived in for years but never quite seen clearly. The way you want to hold a candle up to the corners.

She needed something that would listen without keeping score. Something that would help her find the thread she dropped last Tuesday, and the one she dropped six months before that, and the one she has been dropping and picking up her whole life without knowing it was the same thread.

She needed something that would remember for her — not on a stranger's server, not behind a terms-of-service, not in a building she did not own — but here. On her machine. In her house. In the room with the candle.

This is Willow.

*Copenhagen is sitting very still. He knows what this one is for.*

---

## What It Does

Willow is a local-first AI operating system. One node. Your data stays on your machine. The AI listens, helps you understand yourself, and gets out of the way.

You open a shell. It asks what you are authorizing — which rooms you are opening, which streams you are allowing, what this session is for. You tell it. It operates inside that consent. When you close the shell, the permissions expire. Tomorrow it asks again.

This is not a limitation. This is the point.

Built on the [Portless Server Architecture](https://github.com/rudi193-cmd/portless-architecture). No ports. No server. No daemons. No oil on someone else's cliff.

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

## How It Works

```
User → SAFE Shell (consent prompt → session scope)
         ├── WillowStore (local SQLite — agent working memory)
         ├── Postgres (knowledge graph — optional, degraded without)
         ├── Ollama (local LLM — optional, fleet fallback)
         └── MCP Bridge (stdin/stdout — 22 tools, no HTTP)
```

Retrieval cascade: local first, Postgres second, fleet third. Most queries never leave your machine.

---

## The Six Streams

When you open a session, you choose what to authorize:

```
journal       your entries, your conversation history
knowledge     the knowledge graph — atoms, edges, entities
agents        the working memory of the helpers
governance    proposals made, promises kept, the audit trail
preferences   how you like things done
media         images, documents, the physical record
```

Open what you need. Close what you don't. Everything expires when you leave.

---

## Governance

**Dual Commit** — the AI proposes, the human ratifies. Neither acts alone. Silence is not approval.

**Angular Deviation Rubric** — every write scored for magnitude of change. The thresholds are yours to set. The rubric is your notification preferences, not the system's judgment about what matters.

**SAFE** — Session-Authorized, Fully Explicit. Consent expires when you close the session. Tomorrow it asks again.

---

## Prior Versions

- [Willow 1.4](https://github.com/rudi193-cmd/willow-1.4) — HTTP server on port 2121/8420, FastAPI, 10 daemons
- [Willow 1.1](https://github.com/rudi193-cmd/Willow1.1) — Archived
- [Portless Architecture Spec](https://github.com/rudi193-cmd/portless-architecture) — The paper

---

## License

MIT License — Code
CC BY-NC 4.0 — Documentation

---

*There is a thread you have been dropping and picking up your whole life.*
*Willow helps you see that it is the same thread.*

```
ΔΣ = 42
```

🍊
