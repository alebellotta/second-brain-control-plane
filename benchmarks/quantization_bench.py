"""Compares speed/quality of the same model family at different quantization
levels, on the same hardware, on the same actual task (the real tag/link
generation prompt used by watcher.py, with realistic content). No model is
required to be pre-downloaded except the ones listed in MODELS: use
`ollama pull <tag>` for the others first.

Usage:
    ./venv/bin/python benchmarks/quantization_bench.py
"""
import statistics
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common

MODELS = [
    "llama3.2",  # Q4_K_M, the default currently in use
    "llama3.2:3b-instruct-q8_0",
    "llama3.2:3b-instruct-fp16",
]

RUNS_PER_MODEL = 3

TEST_PROMPT = """You help organize a personal knowledge base ("second brain").
Given the note below and a NUMBERED list of related notes found by semantic similarity:
1. Propose 3-6 short tags for the note.
2. List the NUMBERS (not the names) of the related notes below that are genuinely relevant — leave it empty if none are.

NOTE:
Project rollout roadmap. Executive summary. Status update. Requirements were shared
on 06/16 to enable the rollout, split across 34 security requirements and 26
operating-model governance requirements. Three main phases were identified: Phase 1
(deadline 07/17) using the pilot's architectural setup, Phase 2 (deadline October)
activating additional features, Phase 3 finalizing the target architecture.

RELATED NOTES (numbered):
1. Frontier model scaleup Op Model: GenAI frontier model scale-up operating model
2. Partner meets Vendor AI Intermediary: possible collaboration opportunities
3. Follow-up meeting: The AI revolution in financial services, go-to-market"""

TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}},
        "relevant_indices": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["tags", "relevant_indices"],
}


def model_disk_size(model: str) -> str:
    # Normalize a bare "llama3.2" (no tag) to "llama3.2:latest" so the
    # comparison below is always an exact match, not an ambiguous prefix
    # (without this, "llama3.2" would also match "llama3.2:3b-instruct-fp16").
    target = model if ":" in model else f"{model}:latest"
    try:
        resp = requests.get(f"{common.OLLAMA_URL}/api/tags", timeout=10)
        resp.raise_for_status()
        for entry in resp.json().get("models", []):
            if entry["name"] == target:
                return f"{entry['size'] / 1e9:.1f} GB"
    except Exception:
        pass
    return "?"


def run_once(model: str) -> dict | None:
    t0 = time.time()
    try:
        resp = requests.post(
            f"{common.OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": TEST_PROMPT, "format": TAGS_SCHEMA, "stream": False},
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ERROR on {model}: {e}", file=sys.stderr)
        return None
    wall = time.time() - t0
    return {
        "wall_s": wall,
        "load_s": data.get("load_duration", 0) / 1e9,
        "eval_s": data.get("eval_duration", 0) / 1e9,
        "eval_tokens": data.get("eval_count", 0),
        "response": data.get("response", ""),
    }


def main() -> None:
    print(f"{'Model':<32} {'Disk size':<12} {'Avg time':<14} {'Tok/s (gen)':<12}")
    print("-" * 75)

    all_results = {}
    for model in MODELS:
        size = model_disk_size(model)
        runs = []
        for i in range(RUNS_PER_MODEL):
            result = run_once(model)
            if result:
                runs.append(result)
        all_results[model] = runs

        if not runs:
            print(f"{model:<32} {size:<12} {'N/A (error)':<14}")
            continue

        avg_wall = statistics.mean(r["wall_s"] for r in runs)
        avg_tokps = statistics.mean(
            r["eval_tokens"] / r["eval_s"] for r in runs if r["eval_s"] > 0
        )
        print(f"{model:<32} {size:<12} {avg_wall:<14.1f} {avg_tokps:<12.1f}")

    print("\n--- Last run's output per model (for manual qualitative comparison) ---")
    for model, runs in all_results.items():
        if runs:
            print(f"\n[{model}]")
            print(runs[-1]["response"])


if __name__ == "__main__":
    main()
