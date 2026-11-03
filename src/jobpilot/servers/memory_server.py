"""MCP server: long-term memory with provenance and conflict handling."""

from __future__ import annotations

from jobpilot.servers.mcp_compat import create_server, run_server
from jobpilot.services.memory import MemoryService

server = create_server(
    "jobpilot-memory",
    instructions=(
        "Store and recall durable facts, preferences and episodes. Profile-affecting "
        "memories are returned as pending and require user confirmation."
    ),
)


@server.tool()
def remember(content: str, kind: str = "semantic") -> dict:
    """Propose a durable memory. Profile facts return a pending confirmation."""
    result = MemoryService().remember(content, kind=kind, provenance="mcp:memory")
    return {
        "memory": result.memory.model_dump(mode="json") if result.memory else None,
        "action": result.action,
        "message": result.message,
    }


@server.tool()
def recall(query: str, limit: int = 6) -> dict:
    """Recall memories by embedding similarity, recency and importance."""
    return {
        "memories": [
            memory.model_dump(mode="json")
            for memory in MemoryService().recall(query, limit=max(1, min(limit, 20)))
        ]
    }


@server.tool()
def forget(memory_id: str) -> dict:
    """Forget one memory."""
    store = MemoryService()
    memory = store.store.get(memory_id)
    if not memory:
        raise ValueError(f"memory not found: {memory_id}")
    store.forget(memory_id)
    return {"forgotten": memory_id, "content": memory.content}


@server.tool()
def list_memories(kind: str = "", limit: int = 50) -> dict:
    """List active memories, optionally filtered by kind."""
    memories = MemoryService().list(kind=kind or None, limit=min(limit, 200))
    return {"memories": [memory.model_dump(mode="json") for memory in memories]}


def main() -> None:
    run_server(server)


if __name__ == "__main__":
    main()
