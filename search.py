"""One-shot semantic search over the second brain.

Usage: python search.py "your question" [num_results] [--json]
"""
import json
import sys

import common


def run_search(query: str, n_results: int = 5) -> list[dict] | None:
    """Returns ranked [{"path", "snippet", "distance"}, ...], or None if an
    embedding couldn't be obtained (e.g. Ollama unreachable). Shared by the
    CLI below and mcp_server.py — one retrieval implementation, not two."""
    embedding = common.ollama_embed(query)
    if embedding is None:
        return None

    collection = common.get_collection()
    results = collection.query(query_embeddings=[embedding], n_results=n_results)
    return [
        {"path": meta["path"], "snippet": doc[:300], "distance": dist}
        for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0])
    ]


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv[1:]

    if not args:
        print('Usage: python search.py "your question" [num_results] [--json]')
        sys.exit(1)

    query = args[0]
    n_results = int(args[1]) if len(args) > 1 else 5

    results = run_search(query, n_results)
    if results is None:
        if as_json:
            print(json.dumps({"error": "Could not get an embedding (is Ollama running?)"}))
        else:
            print("Error: could not get an embedding (is Ollama running?)")
        sys.exit(1)

    if as_json:
        print(json.dumps(results, ensure_ascii=False))
        return

    if not results:
        print("No results found. Is the index empty?")
        return

    for r in results:
        print(f"\n[{r['path']}]  (distance: {r['distance']:.3f})")
        print(r["snippet"].replace("\n", " "))


if __name__ == "__main__":
    main()
