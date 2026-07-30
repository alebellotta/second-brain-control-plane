"""One-shot semantic search over the second brain.

Usage: python search.py "your question" [num_results] [--json]
"""
import json
import sys

import common


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv[1:]

    if not args:
        print('Usage: python search.py "your question" [num_results] [--json]')
        sys.exit(1)

    query = args[0]
    n_results = int(args[1]) if len(args) > 1 else 5

    embedding = common.ollama_embed(query)
    if embedding is None:
        if as_json:
            print(json.dumps({"error": "Could not get an embedding (is Ollama running?)"}))
        else:
            print("Error: could not get an embedding (is Ollama running?)")
        sys.exit(1)

    collection = common.get_collection()
    results = collection.query(query_embeddings=[embedding], n_results=n_results)

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    if as_json:
        out = [
            {"path": meta["path"], "snippet": doc[:300], "distance": dist}
            for doc, meta, dist in zip(docs, metas, dists)
        ]
        print(json.dumps(out, ensure_ascii=False))
        return

    if not docs:
        print("No results found. Is the index empty?")
        return

    for doc, meta, dist in zip(docs, metas, dists):
        print(f"\n[{meta['path']}]  (distance: {dist:.3f})")
        print(doc[:300].replace("\n", " "))


if __name__ == "__main__":
    main()
