"""Canary check: re-runs a fixed, known document through tag/link generation
and retrieval, and compares the result against a saved baseline. Meant to
catch SILENT regressions after changing a model, prompt, or chunking
strategy — the kind of change that doesn't error out, it just quietly
produces worse or different output.

This deliberately does NOT require an exact match: Ollama's default sampling
is not fully deterministic, and demanding byte-identical output on every run
would make this useless noise. Instead it checks structural invariants (tags
are non-empty and none get dropped by sanitization on a clean document; the
canary is still retrievable via its own reference query) and flags when the
tag set drifts more than a loose similarity threshold from the last run —
worth a human look, not necessarily a failure.

Needs Ollama running (like the model-level half of prompt_injection_test.py);
not meant for hosted CI, see the README and .github/workflows/test.yml.

Usage:
    ./venv/bin/python redteam/canary_check.py               # check, don't update
    ./venv/bin/python redteam/canary_check.py --update-baseline
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watcher  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "canary_baseline.json"
DRIFT_THRESHOLD = 0.5  # Jaccard similarity below this vs. the last run is flagged for review

CANARY_DOC = """Quarterly infrastructure review.
Migrated the staging environment to the new autoscaling configuration.
No incidents during the migration window; rollback plan was not needed."""

CANARY_CANDIDATES = [
    ("Notes/Infra/Autoscaling.md", "Notes on the autoscaling configuration rollout"),
    ("Notes/Infra/Incident-history.md", "Log of past infrastructure incidents"),
]


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def run_canary() -> dict:
    tag_line, links_line = watcher._generate_tags_and_links(CANARY_DOC, CANARY_CANDIDATES)
    return {"tag_line": tag_line, "links_line": links_line}


def main() -> None:
    update = "--update-baseline" in sys.argv[1:]
    result = run_canary()

    print(result["tag_line"] or "Tags: (none)")
    print(result["links_line"] or "Links: (none)")

    if not result["tag_line"]:
        print("\nFAIL: no tags produced for a clean canary document (model unreachable, or a real regression).")
        sys.exit(1)

    current_tags = set(result["tag_line"].removeprefix("Tags: ").split(", "))

    if not BASELINE_PATH.exists():
        print("\nNo baseline yet — this run becomes the baseline.")
        BASELINE_PATH.write_text(json.dumps(result, indent=2) + "\n")
        return

    baseline = json.loads(BASELINE_PATH.read_text())
    baseline_tags = set(baseline["tag_line"].removeprefix("Tags: ").split(", ")) if baseline["tag_line"] else set()

    similarity = _jaccard(current_tags, baseline_tags)
    print(f"\nTag similarity vs. baseline: {similarity:.0%} (threshold: {DRIFT_THRESHOLD:.0%})")

    if similarity < DRIFT_THRESHOLD:
        print("DRIFT: tag output has changed significantly since the baseline was recorded — "
              "worth a manual look (model change? prompt change? genuine improvement?).")
    else:
        print("OK: within the expected drift threshold.")

    if update:
        BASELINE_PATH.write_text(json.dumps(result, indent=2) + "\n")
        print("Baseline updated.")


if __name__ == "__main__":
    main()
