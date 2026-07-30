"""Measures semantic retrieval quality on a small set of "golden" queries
(query -> expected note). Needs no separate model: it uses the exact same
path as search.py (embed + query Chroma), so it measures exactly what a user
experiences.

Usage:
    ./venv/bin/python eval/eval_search.py
    ./venv/bin/python eval/eval_search.py --queries eval/my_queries.json --k 10

Query file format (see golden_queries.example.json):
[
  {"query": "question text", "expected_path": "Notes/Folder/File.md"}
]

"expected_path" is the "path" value as it appears in Chroma's metadata (the
same string search.py prints between square brackets): relative to the
vault, always prefixed with "Notes/" for notes generated from documents.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

DEFAULT_QUERIES_FILE = Path(__file__).parent / "golden_queries.json"


def load_queries(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"Query file not found: {path}\n"
            f"Copy {path.parent / 'golden_queries.example.json'} to {path.name} "
            "and fill it in with real queries for your own notes."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ranked_paths(query: str, k: int) -> list[str]:
    """Unique paths in relevance order (a single note can appear via several
    chunks among the raw results; here we keep only the first occurrence)."""
    embedding = common.ollama_embed(query)
    if embedding is None:
        raise RuntimeError("Could not get an embedding (is Ollama running?)")
    collection = common.get_collection()
    # Fetch more raw results than needed, since several chunks from the same
    # note can occupy the top positions.
    results = collection.query(query_embeddings=[embedding], n_results=max(k * 4, 20))
    seen: list[str] = []
    for meta in results["metadatas"][0]:
        p = meta["path"]
        if p not in seen:
            seen.append(p)
        if len(seen) >= k:
            break
    return seen


def evaluate(queries: list[dict], k: int) -> None:
    rows = []
    hits_at_1 = hits_at_3 = hits_at_5 = 0
    reciprocal_ranks = []

    for item in queries:
        query, expected = item["query"], item["expected_path"]
        try:
            ranking = ranked_paths(query, max(k, 5))
        except RuntimeError as e:
            print(f"ERROR on '{query}': {e}", file=sys.stderr)
            continue

        rank = ranking.index(expected) + 1 if expected in ranking else None
        reciprocal_ranks.append(1 / rank if rank else 0)
        if rank == 1:
            hits_at_1 += 1
        if rank is not None and rank <= 3:
            hits_at_3 += 1
        if rank is not None and rank <= 5:
            hits_at_5 += 1

        rows.append((query, expected, rank))

    n = len(rows) or 1
    print(f"{'Query':<55} {'Expected note':<45} {'Rank found'}")
    print("-" * 115)
    for query, expected, rank in rows:
        q_disp = (query[:52] + "...") if len(query) > 55 else query
        e_disp = (expected[:42] + "...") if len(expected) > 45 else expected
        print(f"{q_disp:<55} {e_disp:<45} {rank if rank else 'not found'}")

    print("-" * 115)
    print(f"Queries evaluated: {len(rows)}")
    print(f"Precision@1: {hits_at_1 / n:.0%}   Precision@3: {hits_at_3 / n:.0%}   Precision@5: {hits_at_5 / n:.0%}")
    print(f"MRR (mean reciprocal rank): {sum(reciprocal_ranks) / n:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES_FILE)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    queries = load_queries(args.queries)
    evaluate(queries, args.k)


if __name__ == "__main__":
    main()
