"""Indexes external folders (e.g. cloud drives, shared libraries) WITHOUT
cloning them: reads documents read-only, extracts the text, and writes only
the resulting note into Notes/<name>/, never copying/duplicating the original
file to disk. Meant to run once a day (e.g. via a scheduler) rather than in
real time: these are typically shared folders edited by other people, so
immediate reactivity isn't needed.

Notes generated here are NEVER automatically deleted if the external file
disappears (unlike the Sources/ pipeline): they act as an archive.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import common
import policy

try:
    from external_folders_config import EXTERNAL_FOLDERS
except ImportError:
    raise SystemExit(
        "Missing external_folders_config.py: copy external_folders_config.example.py "
        "and customize it with the real paths of your external folders."
    )

log = common.setup_logging("index_external")


def index_one(source: Path, notes_name: str) -> None:
    if not source.exists():
        log.warning("Source folder not found, skipping: %s", source)
        return

    notes_root = common.NOTES_DIR / notes_name
    indexed = 0
    skipped_by_policy = 0  # oversized, or a symlink escaping this specific external folder
    skipped_redundant = 0

    candidates = []
    for src_file in source.rglob("*"):
        if not src_file.is_file() or src_file.name.startswith("."):
            continue
        if src_file.suffix.lower() not in common.SOURCE_EXTRACTORS:
            continue
        pre_decision = policy.evaluate(src_file, source)
        policy.log_decision(common.POLICY_LOG_PATH, src_file, pre_decision)
        if pre_decision.action == "deny":
            skipped_by_policy += 1
            continue
        candidates.append(src_file)

    # Among different versions of the same document (_v01/_v2/_vFINAL...) keep
    # only the latest, applied ONLY to files that already passed the size
    # filter (if the latest version is too big, the older-but-processable one
    # is better than nothing). Then, between a PDF and its PPTX/DOCX
    # counterpart with the same name, keep only the PDF (lighter, same
    # exported content).
    after_versions = common.latest_version_only(candidates)
    keep = common.prefer_pdf_duplicates(list(after_versions))

    for src_file in candidates:
        rel = src_file.relative_to(source)
        out_path = notes_root / rel.with_name(rel.name + ".md")

        if src_file not in keep:
            skipped_redundant += 1
            if out_path.exists():
                out_path.unlink()
                log.info("Removed note for discarded file (superseded version or redundant duplicate): %s", rel)
            continue

        src_stat = src_file.stat()

        if out_path.exists():
            stored_mtime = common._stored_source_mtime(out_path)
            if stored_mtime is not None and stored_mtime >= src_stat.st_mtime:
                continue

        if not common._wait_until_stable(src_file):
            log.warning("File unstable/still copying, skipping for now: %s", rel)
            continue

        extractor = common.SOURCE_EXTRACTORS[src_file.suffix.lower()]
        try:
            text, extraction_meta = extractor(src_file)
        except Exception:
            log.exception("Extraction failed for %s", src_file)
            continue

        if not text.strip():
            log.warning("No text extracted from %s", rel)
            continue

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        decision = policy.evaluate(src_file, source, text=text)
        policy.log_decision(common.POLICY_LOG_PATH, src_file, decision, content_hash=content_hash)
        if decision.action == "quarantine":
            log.warning(
                "Note for %s flagged for quarantine (%s): written but not embedded semantically",
                rel, ", ".join(decision.sensitivity_flags),
            )

        extra_frontmatter = "".join(f"{key}: {json.dumps(value)}\n" for key, value in extraction_meta.items())

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            f"""---
source_file: "{src_file}"
source_type: "{src_file.suffix.lower().lstrip('.')}"
source_mtime: {src_stat.st_mtime}
generated_from_source: true
external_source: true
content_hash: "{content_hash}"
resolved_path: "{src_file.resolve().as_posix()}"
mime_type: "{decision.mime_type or 'unknown'}"
extractor_version: "{common.EXTRACTOR_VERSIONS.get(src_file.suffix.lower(), 'unknown')}"
embedding_model: "{common.EMBED_MODEL}"
chunking_version: "{common.CHUNKING_VERSION}"
indexed_at: "{datetime.now(timezone.utc).isoformat()}"
policy_decision: "{decision.action}"
sensitivity_flags: {json.dumps(decision.sensitivity_flags)}
{extra_frontmatter}---

# {src_file.stem}

{text.strip()}
""",
            encoding="utf-8",
        )
        indexed += 1
        log.info("Indexed (without copying) %s -> Notes/%s/%s", rel, notes_name, rel.with_name(rel.name + ".md"))

    log.info(
        "External indexing '%s': %d documents updated, %d skipped by policy (oversized/symlink), %d skipped (superseded version/redundant duplicate)",
        source.name, indexed, skipped_by_policy, skipped_redundant,
    )


def main() -> None:
    common.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    for entry in EXTERNAL_FOLDERS:
        index_one(entry["source"], entry["notes_name"])


if __name__ == "__main__":
    main()
