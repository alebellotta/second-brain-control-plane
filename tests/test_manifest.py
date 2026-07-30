"""Regression test for the extended manifest written by convert_source().
No Ollama/Chroma involved: convert_source() only extracts text and evaluates
policy, it never embeds anything — safe to run in hosted CI."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

REQUIRED_KEYS = [
    "source_file", "source_type", "source_mtime", "generated_from_source",
    "content_hash", "resolved_path", "mime_type", "extractor_version",
    "embedding_model", "chunking_version", "indexed_at", "policy_decision",
    "sensitivity_flags",
]


def test_manifest_has_all_required_keys(tmp_path, monkeypatch):
    sources_dir = tmp_path / "Sources"
    notes_dir = tmp_path / "Notes"
    sources_dir.mkdir()
    monkeypatch.setattr(common, "SOURCES_DIR", sources_dir)
    monkeypatch.setattr(common, "NOTES_DIR", notes_dir)

    doc = sources_dir / "note.txt"
    doc.write_text("A short test document about quarterly planning.")

    out_path = common.convert_source(doc)
    assert out_path is not None
    frontmatter = out_path.read_text()

    for key in REQUIRED_KEYS:
        assert re.search(rf"^{key}:", frontmatter, flags=re.MULTILINE), f"missing manifest key: {key}"

    assert 'policy_decision: "allow"' in frontmatter
    assert re.search(r"^content_hash: \"[0-9a-f]{16}\"$", frontmatter, flags=re.MULTILINE)


def test_manifest_reflects_quarantine(tmp_path, monkeypatch):
    sources_dir = tmp_path / "Sources"
    notes_dir = tmp_path / "Notes"
    sources_dir.mkdir()
    monkeypatch.setattr(common, "SOURCES_DIR", sources_dir)
    monkeypatch.setattr(common, "NOTES_DIR", notes_dir)

    doc = sources_dir / "config.txt"
    doc.write_text("Token: ghp_" + "b" * 36)

    out_path = common.convert_source(doc)
    frontmatter = out_path.read_text()

    assert 'policy_decision: "quarantine"' in frontmatter
    assert "secret-like-content" in frontmatter


def test_denied_symlink_writes_no_note(tmp_path, monkeypatch):
    sources_dir = tmp_path / "Sources"
    notes_dir = tmp_path / "Notes"
    sources_dir.mkdir()
    monkeypatch.setattr(common, "SOURCES_DIR", sources_dir)
    monkeypatch.setattr(common, "NOTES_DIR", notes_dir)

    outside = tmp_path / "outside.txt"
    outside.write_text("should never be indexed")
    link = sources_dir / "link.txt"
    link.symlink_to(outside)

    assert common.convert_source(link) is None
    assert not notes_dir.exists() or not any(notes_dir.rglob("*.md"))
