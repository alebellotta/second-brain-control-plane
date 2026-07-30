"""Exposes this second brain as an MCP server, so MCP-compatible clients
(Claude Desktop, Claude Code, Cursor, ...) can search and ask questions
against the user's own local vault directly. A thin transport layer: every
tool below reuses the existing search.py/chat.py/digest.py logic rather than
reimplementing retrieval or generation a second time.

Usage (stdio transport, the one Claude Desktop/Code expect):
    ./venv/bin/python mcp_server.py

Claude Desktop config (~/Library/Application Support/Claude/claude_desktop_config.json
on macOS) — use absolute paths, since MCP servers are launched without your shell's PATH/venv:
{
  "mcpServers": {
    "second-brain": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/mcp_server.py"]
    }
  }
}
"""
import subprocess
import sys
from datetime import date as date_cls

import requests
from mcp.server.mcpserver import MCPServer

import chat
import common
import search

mcp = MCPServer(
    name="second-brain",
    instructions=(
        "Search and ask questions about the user's local, private Obsidian "
        "second brain. Everything here runs on the user's own machine — "
        "there is no cloud service behind these tools."
    ),
)


@mcp.tool()
def search_notes(query: str, n_results: int = 5) -> list[dict]:
    """Semantic search over the vault. Returns ranked raw passages with their
    source note path and a similarity distance — not a synthesized answer
    (use explain_sources for that)."""
    results = search.run_search(query, n_results)
    if results is None:
        return [{"error": "Could not reach Ollama for embeddings."}]
    return results


@mcp.tool()
def retrieve_note(path: str) -> str:
    """Returns the full raw content of a specific note, given the vault-
    relative path returned by search_notes/explain_sources (e.g.
    "Notes/Projects/Roadmap.md")."""
    note_path = common.VAULT_DIR / path
    try:
        note_path.resolve().relative_to(common.VAULT_DIR.resolve())
    except ValueError:
        return "Error: that path resolves outside the vault."
    if not note_path.is_file():
        return f"Error: no note found at {path}"
    return note_path.read_text(encoding="utf-8", errors="ignore")


@mcp.tool()
def explain_sources(question: str) -> dict:
    """Ask a question in natural language and get a synthesized answer with
    cited sources. The sources are determined by code from what was actually
    retrieved, never by the model. Stateless — no memory of previous calls;
    for a real back-and-forth, ask follow-up questions with the context
    included in your own message."""
    answer, sources = chat.ask(question, history=[])
    if not sources:
        return {"error": "No relevant context found (empty index, or Ollama unreachable?)"}
    if not answer:
        return {"error": "Error generating the answer."}
    return {"answer": answer, "sources": sources}


@mcp.tool()
def daily_digest(day: str | None = None) -> str:
    """Returns the digest note for a given date (YYYY-MM-DD, defaults to
    today) if it exists. Does not generate one on demand — run digest.py for
    that; this tool only reads what's already there."""
    target = day or date_cls.today().isoformat()
    digest_path = common.VAULT_DIR / "Reviews" / f"{target}.md"
    if not digest_path.is_file():
        return f"No digest found for {target}."
    return digest_path.read_text(encoding="utf-8", errors="ignore")


@mcp.tool()
def healthcheck() -> dict:
    """Reports whether the services this second brain depends on are
    reachable: Ollama, the Chroma index, and (on macOS) the watcher
    LaunchAgent — useful to check before assuming a search/chat result
    reflects the current state of the vault."""
    status: dict = {}

    try:
        resp = requests.get(f"{common.OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        status["ollama"] = "reachable"
    except Exception as e:
        status["ollama"] = f"unreachable ({e})"

    try:
        status["indexed_chunks"] = common.get_collection().count()
    except Exception as e:
        status["indexed_chunks"] = f"error ({e})"

    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, timeout=5
            ).stdout
            status["watcher_launchagent"] = "loaded" if "secondbrain.watcher" in out else "not loaded"
        except Exception as e:
            status["watcher_launchagent"] = f"error ({e})"

    return status


if __name__ == "__main__":
    mcp.run(transport="stdio")
