"""Conversational RAG chat on top of the existing index: unlike search.py
(one-shot search, raw passages), here you ask a question and get a
synthesized answer with cited sources, and can ask follow-up questions while
keeping the conversation's context.

Interactive usage:
    ./venv/bin/python chat.py
    (type 'exit' or 'quit' to leave; also works non-interactively, e.g.
    echo "question" | python chat.py)

One-shot usage (for the Obsidian plugin): reads a JSON payload from stdin
{"question": "...", "history": [{"q": "...", "a": "..."}, ...]} and prints
{"answer": "...", "sources": [...]} or {"error": "..."} to stdout.
    ./venv/bin/python chat.py --json < request.json
"""
import json
import sys

import common

N_CONTEXT_CHUNKS = 6
MAX_HISTORY_TURNS = 4  # how many previous question/answer pairs to keep in context


def retrieve(query: str) -> list[tuple[str, str, float]]:
    embedding = common.ollama_embed(query)
    if embedding is None:
        return []
    collection = common.get_collection()
    results = collection.query(query_embeddings=[embedding], n_results=N_CONTEXT_CHUNKS)
    return list(zip(
        [m["path"] for m in results["metadatas"][0]],
        results["documents"][0],
        results["distances"][0],
    ))


def build_prompt(question: str, context_chunks: list[tuple[str, str, float]], history: list[tuple[str, str]]) -> str:
    context_text = "\n\n".join(f"[Source: {path}]\n{doc[:600]}" for path, doc, _ in context_chunks)

    parts = []
    if history:
        history_text = "\n\n".join(
            f"Previous question: {q}\nPrevious answer: {a}" for q, a in history[-MAX_HISTORY_TURNS:]
        )
        parts.append(f"PREVIOUS CONVERSATION:\n{history_text}")

    parts.append(f"CONTEXT RETRIEVED FROM YOUR NOTES:\n{context_text}")
    parts.append(
        f"QUESTION: {question}\n\n"
        "Answer based ONLY on the context above. If the context doesn't contain "
        "the requested information, say so explicitly instead of making up a "
        "plausible-sounding answer."
    )
    return "\n\n".join(parts)


def ask(question: str, history: list[tuple[str, str]]) -> tuple[str | None, list[str]]:
    """Returns (answer, list of source paths used). Sources are determined by
    code (the chunks actually retrieved), not by the model: same logic as
    common.py's wikilink handling — an LLM isn't trusted to reproduce an
    exact reference, only to use it as context."""
    context_chunks = retrieve(question)
    if not context_chunks:
        return None, []

    prompt = build_prompt(question, context_chunks, history)
    answer = common.ollama_generate(common.TAG_MODEL, prompt)
    sources = sorted({path for path, _, _ in context_chunks})
    return answer, sources


def main_json() -> None:
    """One-shot mode: one request, one answer, then exit. Used by the
    Obsidian plugin, which keeps the conversation history itself between
    invocations (no long-running process to manage)."""
    try:
        payload = json.loads(sys.stdin.read())
        question = payload["question"]
        history = [(turn["q"], turn["a"]) for turn in payload.get("history", [])]
    except Exception as e:
        print(json.dumps({"error": f"Invalid request: {e}"}))
        return

    answer, sources = ask(question, history)
    if not sources:
        print(json.dumps({"error": "No relevant context found (empty index, or Ollama unreachable?)"}))
        return
    if not answer:
        print(json.dumps({"error": "Error generating the answer."}))
        return

    print(json.dumps({"answer": answer, "sources": sources}, ensure_ascii=False))


def main() -> None:
    print("RAG chat over your second brain. Type 'exit' or 'quit' to leave.\n")
    history: list[tuple[str, str]] = []

    while True:
        try:
            question = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            break

        answer, sources = ask(question, history)
        if not sources:
            print("No relevant context found (empty index, or Ollama unreachable?)\n")
            continue
        if not answer:
            print("Error generating the answer.\n")
            continue

        print(f"\nAssistant> {answer}\n")
        print("Sources used: " + ", ".join(sources) + "\n")
        history.append((question, answer))


if __name__ == "__main__":
    if "--json" in sys.argv[1:]:
        main_json()
    else:
        main()
