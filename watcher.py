"""Real-time watcher for the Obsidian vault: indexes notes into Chroma and
generates tag/link suggestions. For notes generated automatically from a
source document (generated_from_source: true) suggestions are appended to the
note itself (it's already our own generated content, safe to enrich further);
for hand-written notes they stay in _Suggestions/, so the user's original
content is never touched."""
import hashlib
import re
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

import common

log = common.setup_logging("watcher")

DEBOUNCE_SECONDS = 2.0
_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()


def suggestions_path(rel: str) -> Path:
    return common.VAULT_DIR / "_Suggestions" / rel


SUGGESTIONS_MARKER = "<!-- second-brain:suggestions"


def _split_content_and_suggestions(text: str) -> tuple[str, str | None]:
    """Splits the real content from any suggestions block already appended to
    the note, and extracts its hash (to tell whether the content changed since
    last time without having to call the model again)."""
    idx = text.find(SUGGESTIONS_MARKER)
    if idx == -1:
        return text.rstrip() + "\n", None
    content = re.sub(r"\n+-{3,}\s*$", "", text[:idx].rstrip()).rstrip() + "\n"
    line_end = text.find("\n", idx)
    marker_line = text[idx: line_end if line_end != -1 else len(text)]
    match = re.search(r"hash=(\w+)", marker_line)
    return content, (match.group(1) if match else None)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def index_note(path: Path) -> None:
    rel = common.relpath(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception:
        log.exception("Could not read %s", rel)
        return

    is_generated = bool(re.search(r"^generated_from_source:\s*true", raw_text, flags=re.MULTILINE))
    content, stored_hash = _split_content_and_suggestions(raw_text) if is_generated else (raw_text, None)

    chunks = common.chunk_markdown(content)
    collection = common.get_collection()
    collection.delete(where={"path": rel})

    if not chunks:
        log.info("Empty note, removed from index: %s", rel)
        if not is_generated:
            _remove_suggestions(rel)
        return

    mtime = path.stat().st_mtime
    ids, embeddings, documents, metadatas = [], [], [], []
    for i, chunk in enumerate(chunks):
        embedding = common.ollama_embed(chunk)
        if embedding is None:
            continue
        ids.append(f"{rel}::{i}")
        embeddings.append(embedding)
        documents.append(chunk)
        metadatas.append({"path": rel, "chunk_index": i, "mtime": mtime})

    if not ids:
        log.warning("No embeddings obtained for %s (is Ollama reachable?)", rel)
        return

    collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    log.info("Indexed %s (%d chunks)", rel, len(ids))

    if is_generated:
        current_hash = _content_hash(content)
        if stored_hash != current_hash:
            _write_suggestions_inline(path, content, current_hash, embeddings[0])
            # Rewriting the note changes its mtime: update the value stored in
            # the index to the final (post-write) one, otherwise the next
            # restart would always see a "newer mtime" and needlessly
            # reprocess the note (the content itself hasn't really changed).
            new_mtime = path.stat().st_mtime
            collection.update(ids=ids, metadatas=[{**m, "mtime": new_mtime} for m in metadatas])
        # Otherwise the content hasn't really changed (e.g. we just rewrote the
        # same suggestions block): don't regenerate, to avoid a rewrite loop on
        # every watcher cycle.
    else:
        _write_suggestions(rel, content, embeddings[0])


def _remove_suggestions(rel: str) -> None:
    sp = suggestions_path(rel)
    if sp.exists():
        sp.unlink()


def _obsidian_link_name(rel_path: str) -> str:
    """The name Obsidian resolves a wikilink to: the filename with only the
    .md extension stripped (for 'X.pptx.md' that's 'X.pptx', not 'X' —
    otherwise the link wouldn't point to any real file)."""
    return Path(rel_path).stem


def _obsidian_link_target(rel_path: str) -> str:
    """Like _obsidian_link_name, but disambiguates when several notes in the
    vault share the same filename in different folders (happens when different
    documents are saved under the same name): a name-only link would then be
    ambiguous in Obsidian (it could resolve to either note depending on which
    one it finds first), so the full path is used instead."""
    name = _obsidian_link_name(rel_path)
    collisions = sum(
        1 for p in common.NOTES_DIR.rglob("*.md") if _obsidian_link_name(common.relpath(p)) == name
    )
    if collisions > 1:
        return rel_path[len("Notes/"):-len(".md")] if rel_path.startswith("Notes/") else rel_path[:-len(".md")]
    return name


def _find_related_candidates(rel: str, note_embedding: list[float]) -> list[tuple[str, str]]:
    collection = common.get_collection()
    results = collection.query(query_embeddings=[note_embedding], n_results=15)
    seen_paths: list[str] = []
    candidates: list[tuple[str, str]] = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        other_path = meta["path"]
        if other_path == rel or other_path in seen_paths:
            continue
        seen_paths.append(other_path)
        candidates.append((other_path, doc[:200]))
        if len(candidates) >= 5:
            break
    return candidates


_TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "relevant_indices": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["tags", "relevant_indices"],
}

MAX_TAG_LENGTH = 40


def _sanitize_tag(raw: str) -> str | None:
    """Tags arrive as free-text strings from the model: the JSON schema
    constrains the SHAPE of the response (an array of strings) but not the
    CONTENT of each string. Without this check, a tag like "x]]\\n\\n##
    Section" written verbatim into tag_line would act as an injection into
    the note: a newline breaks out of the intended "Tags: ..." line and
    injects arbitrary markdown (headings, frontmatter, even a forged
    <!-- second-brain:... --> marker), while "[[...]]" creates a real,
    clickable Obsidian wikilink — bypassing the "links are built by code from
    numbered indices" safeguard, which protects links_line but not tag_line.
    A tag containing one of these sequences is dropped entirely (not
    truncated/cleaned in place: if it's already been tampered with, losing it
    is safer than keeping a mangled, still-suspicious version)."""
    tag = raw.strip()
    if not tag or len(tag) > MAX_TAG_LENGTH:
        return None
    if any(seq in tag for seq in ("\n", "\r", "[[", "]]", "<!--", "-->")):
        return None
    return tag


def _generate_tags_and_links(text: str, candidates: list[tuple[str, str]]) -> tuple[str, str]:
    """Asks the model, with output constrained to a JSON schema natively
    supported by Ollama, for tags and which (by number) of the listed related
    notes are relevant. The code still builds the wikilinks from the
    candidates' real paths, not the model: this avoids the model
    inventing/garbling paths that don't correspond to any note (this happened
    in practice with long names like 'Folder/Subfolder/File.pptx.md'). The
    JSON schema also removes the free-text parsing ("Tags: ..." / "Links:
    ...") that could previously fail if the model didn't follow the exact
    requested format. Returns (tag_line, links_line); both empty if there's
    nothing useful to suggest."""
    if not candidates:
        return "", ""

    numbered = "\n".join(
        f"{i + 1}. {_obsidian_link_name(p)}: {snippet}" for i, (p, snippet) in enumerate(candidates)
    )

    prompt = f"""You help organize a personal knowledge base ("second brain").
Given the note below and a NUMBERED list of related notes found by semantic similarity:
1. Propose 3-6 short tags for the note.
2. List the NUMBERS (not the names) of the related notes below that are genuinely relevant — leave it empty if none are.

NOTE:
{text[:1500]}

RELATED NOTES (numbered):
{numbered}"""

    result = common.ollama_generate_json(common.TAG_MODEL, prompt, _TAGS_SCHEMA)
    if not result:
        return "", ""

    raw_tags = [str(t) for t in result.get("tags", [])][:10]  # cap in code too, not just in the schema
    tags = [t for t in (_sanitize_tag(rt) for rt in raw_tags) if t]
    if len(tags) < len(raw_tags):
        log.warning("Dropped %d invalid/suspicious tag(s) from the model's response", len(raw_tags) - len(tags))
    tag_line = "Tags: " + ", ".join(tags) if tags else ""

    raw_indices = result.get("relevant_indices", [])
    indices = [i for i in raw_indices if isinstance(i, int) and 1 <= i <= len(candidates)]

    links: list[str] = []
    seen_targets: set[str] = set()
    for i in dict.fromkeys(indices):
        target = _obsidian_link_target(candidates[i - 1][0])
        if target in seen_targets:
            continue  # two different candidates that resolve to the same link (rare, but happened)
        seen_targets.add(target)
        links.append(f"[[{target}]]")
    links_line = "Links: " + (" ".join(links) if links else "(none relevant)")
    return tag_line, links_line


def _write_suggestions(rel: str, text: str, note_embedding: list[float]) -> None:
    """Hand-written note: suggestions go into a separate file under
    _Suggestions/, so the user's original content is never touched."""
    candidates = _find_related_candidates(rel, note_embedding)
    if not candidates:
        return  # no other notes yet to suggest links to

    tag_line, links_line = _generate_tags_and_links(text, candidates)
    if not tag_line and not links_line:
        return

    own_name = _obsidian_link_name(rel)
    sp = suggestions_path(rel)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(
        f"""---
source: "[[{own_name}]]"
generated: true
---

# Suggestions for {own_name}

{tag_line}
{links_line}
""",
        encoding="utf-8",
    )
    log.info("Suggestions updated for %s", rel)


def _write_suggestions_inline(path: Path, content: str, content_hash: str, note_embedding: list[float]) -> None:
    """Note generated automatically from a source document: suggestions are
    appended to the note itself (it's already our own content, safe to enrich)
    instead of a separate _Suggestions/ file. The content hash in the marker
    prevents a loop: next time around, if the content hasn't changed, nothing
    gets regenerated."""
    rel = common.relpath(path)
    candidates = _find_related_candidates(rel, note_embedding)
    tag_line, links_line = _generate_tags_and_links(content, candidates)
    if not tag_line:
        tag_line = "Tags: "
    if not links_line:
        links_line = "Links: (none relevant)"

    new_text = f"""{content.rstrip()}

---

{SUGGESTIONS_MARKER} hash={content_hash} -->
## 🏷️ Suggested tags and links (AI)

{tag_line}
{links_line}
"""
    path.write_text(new_text, encoding="utf-8")
    log.info("Suggestions updated (inline) for %s", rel)


def delete_note(path: Path) -> None:
    rel = common.relpath(path)
    common.get_collection().delete(where={"path": rel})
    _remove_suggestions(rel)
    log.info("Removed from index (file deleted): %s", rel)


def convert_source_document(path: Path) -> None:
    if not path.exists():
        return
    note_path = common.convert_source(path)
    if note_path:
        log.info("Converted %s -> %s", path.relative_to(common.SOURCES_DIR), common.relpath(note_path))
        # The freshly written .md note gets indexed by the watcher's normal
        # cycle (create event under Notes/), no need to call index_note here.


def convert_source_document_versioned(path: Path) -> None:
    """Like convert_source_document, but first checks "sibling" files in the
    same folder: (1) among different versions of the same document
    (_v01/_v2/_vFINAL..., same extension) it only converts the latest one;
    (2) between a PDF and its PPTX/DOCX counterpart with the same name, it
    keeps only the PDF (lighter, same content). Cleans up the notes of any
    discarded siblings that were already indexed."""
    if not path.exists():
        return

    siblings = [p for p in path.parent.iterdir() if p.is_file() and common.is_source_document(p)]
    after_versions = common.latest_version_only(siblings)
    keep = common.prefer_pdf_duplicates(list(after_versions))

    for p in siblings:
        if p in keep:
            continue
        note_path = common.note_path_for_source(p)
        if note_path.exists():
            note_path.unlink()
            log.info("Removed note for discarded file (superseded version or redundant duplicate): %s", common.relpath(note_path))

    if path in keep:
        convert_source_document(path)
    else:
        log.info("Skipping %s: superseded version or redundant duplicate of another file", common.relpath(path))


def delete_generated_note(path: Path) -> None:
    note_path = common.note_path_for_source(path)
    if note_path.exists():
        note_path.unlink()
        log.info("Removed generated note for deleted source: %s", common.relpath(note_path))
        # index_note/on_deleted for this file will fire from the watcher's normal cycle


def _debounced(path: Path, fn) -> None:
    key = str(path)
    with _timers_lock:
        existing = _timers.get(key)
        if existing:
            existing.cancel()
        timer = threading.Timer(DEBOUNCE_SECONDS, fn, args=(path,))
        _timers[key] = timer
        timer.start()


class VaultHandler(FileSystemEventHandler):
    def _relevant(self, path_str: str) -> Path | None:
        path = Path(path_str)
        if path.suffix != ".md" or common.is_ignored_path(path):
            return None
        return path

    def _relevant_source(self, path_str: str) -> Path | None:
        path = Path(path_str)
        if not common.is_source_document(path):
            return None
        return path

    def on_created(self, event):
        if event.is_directory:
            return
        path = self._relevant(event.src_path)
        if path:
            _debounced(path, index_note)
        source_path = self._relevant_source(event.src_path)
        if source_path:
            _debounced(source_path, convert_source_document_versioned)

    def on_modified(self, event):
        if event.is_directory:
            return
        path = self._relevant(event.src_path)
        if path:
            _debounced(path, index_note)
        source_path = self._relevant_source(event.src_path)
        if source_path:
            _debounced(source_path, convert_source_document_versioned)

    def on_deleted(self, event):
        if event.is_directory:
            return
        path = self._relevant(event.src_path)
        if path:
            _debounced(path, delete_note)
        source_path = self._relevant_source(event.src_path)
        if source_path:
            _debounced(source_path, delete_generated_note)

    def on_moved(self, event):
        if event.is_directory:
            return
        old_path = self._relevant(event.src_path)
        if old_path:
            _debounced(old_path, delete_note)
        new_path = self._relevant(event.dest_path)
        if new_path:
            _debounced(new_path, index_note)

        old_source = self._relevant_source(event.src_path)
        if old_source:
            _debounced(old_source, delete_generated_note)
        new_source = self._relevant_source(event.dest_path)
        if new_source:
            _debounced(new_source, convert_source_document_versioned)


def reconcile_deleted_notes() -> None:
    """Removes from the index (and from _Suggestions) any path that no longer
    corresponds to an existing file: happens when a note is deleted while the
    watcher isn't running, or in the short window between a manual deletion
    and a watcher restart (the live event can be missed)."""
    collection = common.get_collection()
    all_entries = collection.get(include=["metadatas"])
    known_paths = {m["path"] for m in all_entries["metadatas"] if m and "path" in m}
    for rel in known_paths:
        if not (common.VAULT_DIR / rel).exists():
            collection.delete(where={"path": rel})
            _remove_suggestions(rel)
            log.info("Reconcile: removed orphaned entry from index (file no longer present): %s", rel)


def reconcile_orphaned_generated_notes() -> None:
    """Deletes notes generated from a source document that no longer exists in
    Sources/ (same scenario: the deletion was missed because it happened while
    the watcher wasn't running). Notes generated from externally-indexed
    folders without cloning (external_source: true, see
    index_external_folders.py) are deliberately excluded: they act as an
    archive and should never be auto-deleted just because the source disappeared."""
    for md_file in common.NOTES_DIR.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if re.search(r'^external_source:\s*true', text, flags=re.MULTILINE):
            continue
        match = re.search(r'^source_file:\s*"([^"]+)"', text, flags=re.MULTILINE)
        if not match:
            continue
        if not (common.SOURCES_DIR / match.group(1)).exists():
            md_file.unlink()
            log.info("Reconcile: removed orphaned generated note (source deleted): %s", common.relpath(md_file))


def reconcile_on_startup() -> None:
    """Reindexes notes that are new or changed, converts source documents that
    are new or changed, and cleans up orphaned entries, from the time the
    watcher wasn't running."""
    common.SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    reconcile_orphaned_generated_notes()

    for source_file in common.SOURCES_DIR.rglob("*"):
        if source_file.is_file() and common.is_source_document(source_file):
            convert_source_document_versioned(source_file)

    collection = common.get_collection()
    for md_file in common.VAULT_DIR.rglob("*.md"):
        if common.is_ignored_path(md_file):
            continue
        rel = common.relpath(md_file)
        existing = collection.get(where={"path": rel}, limit=1)
        current_mtime = md_file.stat().st_mtime
        needs_index = True
        if existing["metadatas"]:
            stored_mtime = existing["metadatas"][0].get("mtime")
            needs_index = stored_mtime is None or current_mtime > stored_mtime
        if needs_index:
            log.info("Reconcile: indexing %s", rel)
            index_note(md_file)

    reconcile_deleted_notes()


def main() -> None:
    common.VAULT_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Starting watcher on %s", common.VAULT_DIR)
    reconcile_on_startup()

    # PollingObserver instead of the default: the native FSEvents backend
    # requires "Full Disk Access" to monitor ~/Documents, a permission that a
    # process launched by launchd (with no graphical session) cannot obtain.
    observer = PollingObserver(timeout=1.5)
    observer.schedule(VaultHandler(), str(common.VAULT_DIR), recursive=True)
    observer.start()
    log.info("Watcher running, listening for real-time changes")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
