"""Prepares a dataset for a light LoRA fine-tune (MLX) from your own vault's
real notes. This is style adaptation (continued pretraining on real text
chunks), not instruction-tuning: a personal vault doesn't naturally contain
question/answer pairs, so the honest format to use is mlx_lm's "text"
format (continued pretraining), not a fabricated "prompt"/"completion" pair.

Usage:
    ./venv/bin/python finetune/prepare_dataset.py
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data"
CHUNK_CHARS = 600
MIN_CHUNK_CHARS = 150
VAL_FRACTION = 0.1
TEST_FRACTION = 0.1
SEED = 42

SUGGESTIONS_MARKER = "<!-- second-brain:suggestions"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _strip_generated_suggestions(text: str) -> str:
    """AI-suggested tags/links are our own generated content, not the user's
    voice: no point training the model to imitate itself."""
    idx = text.find(SUGGESTIONS_MARKER)
    return text[:idx].rstrip() if idx != -1 else text


def _chunks(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if current and len(current) + len(p) > CHUNK_CHARS:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS]


def build_records() -> list[dict]:
    records = []
    for path in sorted(common.NOTES_DIR.rglob("*.md")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        text = _strip_generated_suggestions(_strip_frontmatter(raw))
        records.extend({"text": chunk} for chunk in _chunks(text))
    return records


def main() -> None:
    records = build_records()
    if not records:
        print("No text found under Notes/ — nothing to prepare.")
        return

    random.Random(SEED).shuffle(records)
    n = len(records)
    n_valid = max(1, int(n * VAL_FRACTION))
    n_test = max(1, int(n * TEST_FRACTION))
    n_train = n - n_valid - n_test
    if n_train < 1:
        print(f"Corpus too small ({n} total records) for a sensible train/valid/test split: "
              f"add more notes under Notes/ before trying fine-tuning.")
        return

    splits = {
        "train": records[:n_train],
        "valid": records[n_train:n_train + n_valid],
        "test": records[n_train + n_valid:],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, split_records in splits.items():
        out_path = OUT_DIR / f"{name}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for r in split_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name}: {len(split_records)} records -> {out_path}")

    if n < 200:
        print(f"\nNote: only {n} total records. LoRA can still run, but with a corpus this small "
              f"expect fragment memorization rather than a robust, generalizable style shift — "
              f"that's a data limit, not a technique limit.")


if __name__ == "__main__":
    main()
