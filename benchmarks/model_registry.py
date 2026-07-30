"""A minimal model registry: records which Ollama models this project
actually relies on, their digest (Ollama already computes and exposes this —
no custom hashing needed), and their role, so "which exact model produced
this note's embeddings/tags" is answerable later without guessing.

Complements benchmarks/quantization_bench.py rather than replacing it: this
script records *which* models are approved/roles/fallbacks; the benchmark
script measures *how they perform*. Run the benchmark first, then run this to
record the outcome.

Usage:
    ./venv/bin/python benchmarks/model_registry.py
    (writes/updates models_registry.json in the repo root; re-running
    refreshes digests/sizes but preserves any "notes" you've added by hand)
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "models_registry.json"

# The models this project actually uses, and the role they play. Extend this
# list (not the code below) when you approve an additional model or a
# fallback for one of these roles.
TRACKED_MODELS = [
    {"name": common.EMBED_MODEL, "role": "embedding", "fallback": None},
    {"name": common.TAG_MODEL, "role": "tag-link-generation", "fallback": None},
    {"name": common.DIGEST_MODEL, "role": "daily-digest", "fallback": None},
]


def fetch_installed_models() -> dict[str, dict]:
    """Returns {model_name: {"digest": ..., "size": ...}} for every model
    Ollama currently has installed, keyed by the exact name Ollama reports."""
    resp = requests.get(f"{common.OLLAMA_URL}/api/tags", timeout=10)
    resp.raise_for_status()
    return {
        entry["name"]: {"digest": entry["digest"], "size_gb": round(entry["size"] / 1e9, 2)}
        for entry in resp.json().get("models", [])
    }


def _normalize(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def build_registry() -> dict:
    installed = fetch_installed_models()
    existing = {}
    if REGISTRY_PATH.exists():
        existing = {m["name"]: m for m in json.loads(REGISTRY_PATH.read_text()).get("models", [])}

    models = []
    for tracked in TRACKED_MODELS:
        name = tracked["name"]
        info = installed.get(_normalize(name), {})
        prior = existing.get(name, {})
        models.append({
            "name": name,
            "role": tracked["role"],
            "fallback": tracked["fallback"],
            "digest": info.get("digest", prior.get("digest")),
            "size_gb": info.get("size_gb", prior.get("size_gb")),
            "installed": _normalize(name) in installed,
            "notes": prior.get("notes", ""),
        })

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ollama_url": common.OLLAMA_URL,
        "models": models,
    }


def main() -> None:
    registry = build_registry()
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n")
    print(f"Wrote {REGISTRY_PATH}")
    for m in registry["models"]:
        status = "OK" if m["installed"] else "NOT INSTALLED"
        print(f"  {m['name']:<24} [{m['role']:<20}] {m.get('digest', '?')[:19]}  {status}")


if __name__ == "__main__":
    main()
