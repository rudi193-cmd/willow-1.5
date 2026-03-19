"""
willow_store_mcp.py — Unified MCP bridge (portless)

No ports. No server. stdin/stdout protocol only.
Bridges BOTH local WillowStore (SQLite) AND Willow Postgres.

Local tools: store_put, store_get, store_search, store_search_all, etc.
Postgres tools: willow_knowledge_search, willow_knowledge_ingest, willow_chat,
                willow_agents, willow_status, willow_journal, willow_query,
                willow_system_status
"""

import asyncio
import json
import sys
from pathlib import Path

# MCP SDK
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    print("MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

# WillowStore
sys.path.insert(0, str(Path(__file__).parent))
from willow_store import WillowStore

# Optional: Postgres bridge
try:
    from pg_bridge import try_connect
    pg = try_connect()
except Exception:
    pg = None

# Default store location — override with WILLOW_STORE_ROOT env var
import os
STORE_ROOT = os.environ.get("WILLOW_STORE_ROOT", str(Path(__file__).parent / "merged_test"))

store = WillowStore(STORE_ROOT)
server = Server("willow-store")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="store_put",
            description="Write a record to a collection. Append-only. Returns (id, action) where action is work_quiet/flag/stop from angular deviation rubric.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "description": "e.g. knowledge/atoms, agents/shiva, feedback"},
                    "record": {"type": "object", "description": "The record data (JSON)"},
                    "record_id": {"type": "string", "description": "Optional. Auto-generated if omitted."},
                    "deviation": {"type": "number", "description": "Angular deviation (radians). 0=routine, pi/4=significant, pi/2=major, pi=reversal.", "default": 0.0},
                },
                "required": ["collection", "record"],
            },
        ),
        types.Tool(
            name="store_get",
            description="Read a single record by ID from a collection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "record_id": {"type": "string"},
                },
                "required": ["collection", "record_id"],
            },
        ),
        types.Tool(
            name="store_search",
            description="Text search within a collection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["collection", "query"],
            },
        ),
        types.Tool(
            name="store_search_all",
            description="Search across ALL collections. The 'go ask Willow' pattern.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="store_list",
            description="List all records in a collection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                },
                "required": ["collection"],
            },
        ),
        types.Tool(
            name="store_update",
            description="Update an existing record. Audit-trailed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "record_id": {"type": "string"},
                    "record": {"type": "object"},
                    "deviation": {"type": "number", "default": 0.0},
                },
                "required": ["collection", "record_id", "record"],
            },
        ),
        types.Tool(
            name="store_delete",
            description="Soft-delete a record. Invisible to search/get but audit-trailed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "record_id": {"type": "string"},
                },
                "required": ["collection", "record_id"],
            },
        ),
        types.Tool(
            name="store_add_edge",
            description="Add an edge to the knowledge graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                    "relation": {"type": "string"},
                    "context": {"type": "string", "default": ""},
                },
                "required": ["from_id", "to_id", "relation"],
            },
        ),
        types.Tool(
            name="store_edges_for",
            description="Get all edges involving a record.",
            inputSchema={
                "type": "object",
                "properties": {
                    "record_id": {"type": "string"},
                },
                "required": ["record_id"],
            },
        ),
        types.Tool(
            name="store_stats",
            description="Collection counts and trajectory scores.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="store_audit",
            description="Read recent audit log for a collection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["collection"],
            },
        ),
        # ── Postgres-backed Willow tools ──────────────────────────────
        types.Tool(
            name="willow_knowledge_search",
            description="Search Willow's Postgres knowledge graph (atoms, entities, ganesha). Returns pointers, not content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="willow_knowledge_ingest",
            description="Add an atom to the Willow knowledge graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "source_type": {"type": "string", "default": "mcp"},
                    "source_id": {"type": "string"},
                    "category": {"type": "string", "default": "general"},
                    "domain": {"type": "string"},
                },
                "required": ["title", "summary"],
            },
        ),
        types.Tool(
            name="willow_query",
            description="General search across knowledge graph. Alias for willow_knowledge_search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="willow_agents",
            description="List registered Willow agents and their trust levels.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="willow_status",
            description="Willow system health: local store + Postgres + Ollama.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="willow_system_status",
            description="Full system status including store stats, Postgres stats, and connectivity.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="willow_chat",
            description="Chat with a Willow agent (routes to Ollama local, then fleet).",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "default": "willow", "description": "Agent name: willow, kart, shiva, gerald, etc."},
                    "message": {"type": "string"},
                },
                "required": ["message"],
            },
        ),
        types.Tool(
            name="willow_journal",
            description="Write a journal entry to the knowledge graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entry": {"type": "string", "description": "Journal entry text"},
                    "domain": {"type": "string", "default": "meta"},
                },
                "required": ["entry"],
            },
        ),
        types.Tool(
            name="willow_governance",
            description="Query governance state: pending proposals, recent ratifications.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="willow_persona",
            description="Get agent persona/profile information.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "default": "willow"},
                },
                "required": ["agent"],
            },
        ),
        types.Tool(
            name="willow_speak",
            description="Text-to-speech via Willow TTS router.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string", "default": "default"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="willow_route",
            description="Route a message to the appropriate agent based on content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                },
                "required": ["message"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "store_put":
            rid, action = store.put(
                arguments["collection"],
                arguments["record"],
                record_id=arguments.get("record_id"),
                deviation=arguments.get("deviation", 0.0),
            )
            result = {"id": rid, "action": action}

        elif name == "store_get":
            result = store.get(arguments["collection"], arguments["record_id"])
            if result is None:
                result = {"error": "not_found"}

        elif name == "store_search":
            result = store.search(arguments["collection"], arguments["query"])

        elif name == "store_search_all":
            result = store.search_all(arguments["query"])

        elif name == "store_list":
            result = store.all(arguments["collection"])

        elif name == "store_update":
            rid, action = store.update(
                arguments["collection"],
                arguments["record_id"],
                arguments["record"],
                deviation=arguments.get("deviation", 0.0),
            )
            result = {"id": rid, "action": action}

        elif name == "store_delete":
            ok = store.delete(arguments["collection"], arguments["record_id"])
            result = {"deleted": ok}

        elif name == "store_add_edge":
            rid, action = store.add_edge(
                arguments["from_id"],
                arguments["to_id"],
                arguments["relation"],
                context=arguments.get("context", ""),
            )
            result = {"id": rid, "action": action}

        elif name == "store_edges_for":
            result = store.edges_for(arguments["record_id"])

        elif name == "store_stats":
            result = store.stats()

        elif name == "store_audit":
            result = store.audit_log(
                arguments["collection"],
                limit=arguments.get("limit", 20),
            )

        # ── Postgres-backed tools ─────────────────────────────────────
        elif name in ("willow_knowledge_search", "willow_query"):
            if not pg:
                result = {"error": "not_available", "reason": "Postgres not connected"}
            else:
                query = arguments["query"]
                limit = arguments.get("limit", 20)
                knowledge = pg.search_knowledge(query, limit)
                ganesha = pg.search_ganesha(query, min(limit, 5))
                entities = pg.search_entities(query, min(limit, 5))
                result = {
                    "knowledge": knowledge,
                    "ganesha_atoms": ganesha,
                    "entities": entities,
                    "total": len(knowledge) + len(ganesha) + len(entities),
                }

        elif name == "willow_knowledge_ingest":
            if not pg:
                result = {"error": "not_available", "reason": "Postgres not connected"}
            else:
                atom_id = pg.ingest_atom(
                    title=arguments["title"],
                    summary=arguments["summary"],
                    source_type=arguments.get("source_type", "mcp"),
                    source_id=arguments.get("source_id", ""),
                    category=arguments.get("category", "general"),
                    domain=arguments.get("domain"),
                )
                result = {"id": atom_id, "status": "ingested" if atom_id else "failed"}

        elif name == "willow_agents":
            agents = [
                {"name": "willow", "trust": "OPERATOR", "role": "Primary interface"},
                {"name": "kart", "trust": "ENGINEER", "role": "Infrastructure, multi-step tasks"},
                {"name": "ada", "trust": "OPERATOR", "role": "Systems admin, continuity"},
                {"name": "shiva", "trust": "ENGINEER", "role": "Bridge Ring, SAFE face"},
                {"name": "ganesha", "trust": "ENGINEER", "role": "Diagnostic, obstacle removal"},
                {"name": "gerald", "trust": "WORKER", "role": "Acting Dean, philosophical"},
                {"name": "riggs", "trust": "WORKER", "role": "Applied reality engineering"},
                {"name": "steve", "trust": "OPERATOR", "role": "Prime node, coordinator"},
                {"name": "pigeon", "trust": "WORKER", "role": "Carrier, connector"},
                {"name": "hanz", "trust": "WORKER", "role": "Code, holds Copenhagen"},
                {"name": "ofshield", "trust": "WORKER", "role": "Keeper of the Gate"},
                {"name": "jeles", "trust": "WORKER", "role": "Librarian, special collections"},
                {"name": "binder", "trust": "WORKER", "role": "Records, filing"},
            ]
            result = {"agents": agents, "count": len(agents)}

        elif name in ("willow_status", "willow_system_status"):
            local_stats = store.stats()
            local_count = sum(s["count"] for s in local_stats.values()) if local_stats else 0
            pg_stats = pg.stats() if pg else {}
            result = {
                "local_store": {"collections": len(local_stats), "records": local_count},
                "postgres": pg_stats if pg_stats else "not_connected",
                "ollama": _check_ollama(),
                "mode": "portless",
            }

        elif name == "willow_chat":
            agent = arguments.get("agent", "willow")
            message = arguments["message"]
            response = _chat_ollama(agent, message)
            if not response:
                response = f"[{agent}] Inference unavailable. Ollama not running."
            result = {"agent": agent, "response": response}

        elif name == "willow_journal":
            entry = arguments["entry"]
            domain = arguments.get("domain", "meta")
            if pg:
                atom_id = pg.ingest_ganesha_atom(entry, domain=domain, depth=1)
                result = {"status": "logged", "atom_id": atom_id}
            else:
                rid, action = store.put("journal/entries", {"text": entry})
                result = {"status": "logged_local", "id": rid}

        elif name == "willow_governance":
            result = {"status": "portless_mode", "note": "Governance runs via Dual Commit proposals in governance/commits/"}

        elif name == "willow_persona":
            agent = arguments.get("agent", "willow")
            result = {"agent": agent, "note": f"Persona profiles at agents/{agent}/AGENT_PROFILE.md"}

        elif name == "willow_speak":
            result = {"status": "not_available", "reason": "TTS not wired in portless mode"}

        elif name == "willow_route":
            result = {"routed_to": "willow", "note": "Message routing defaults to willow in portless mode"}

        else:
            result = {"error": f"Unknown tool: {name}"}

        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]


def _check_ollama() -> dict:
    """Check if Ollama is running."""
    try:
        import urllib.request
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/tags"
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return {"running": True, "models": models}
    except Exception:
        return {"running": False}


def _chat_ollama(agent: str, message: str) -> str | None:
    """Try local Ollama chat."""
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


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
