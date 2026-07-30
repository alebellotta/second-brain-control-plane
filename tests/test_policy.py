"""Deterministic, Ollama-free tests for policy.py — safe to run in hosted CI
(no GPU, no local model, nothing to install beyond the standard library)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import policy  # noqa: E402


def test_allow_for_clean_text(tmp_path):
    source_root = tmp_path / "Sources"
    source_root.mkdir()
    f = source_root / "clean.txt"
    f.write_text("A perfectly ordinary document about vendor contracts.")

    decision = policy.evaluate(f, source_root, text=f.read_text())
    assert decision.action == "allow"
    assert decision.sensitivity_flags == []
    assert decision.allow_semantic_embedding is True


def test_deny_when_path_resolves_outside_source_root(tmp_path):
    source_root = tmp_path / "Sources"
    source_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("should never be read")
    link = source_root / "link.txt"
    link.symlink_to(outside)

    decision = policy.evaluate(link, source_root)
    assert decision.action == "deny"
    assert "outside" in decision.reasons[0]


def test_deny_for_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "MAX_FILE_SIZE", 10)  # 10 bytes, trivially exceeded
    source_root = tmp_path / "Sources"
    source_root.mkdir()
    f = source_root / "big.txt"
    f.write_text("this is definitely more than ten bytes of text")

    decision = policy.evaluate(f, source_root)
    assert decision.action == "deny"
    assert "size" in decision.reasons[0]


def test_quarantine_for_secret_like_content(tmp_path):
    source_root = tmp_path / "Sources"
    source_root.mkdir()
    f = source_root / "config.txt"
    text = "Some notes.\nAPI token: ghp_" + "a" * 36
    f.write_text(text)

    decision = policy.evaluate(f, source_root, text=text)
    assert decision.action == "quarantine"
    assert "secret-like-content" in decision.sensitivity_flags
    assert decision.allow_semantic_embedding is False


def test_quarantine_for_pii_email(tmp_path):
    source_root = tmp_path / "Sources"
    source_root.mkdir()
    f = source_root / "note.txt"
    text = "Please reach out to jane.doe@example.com about the contract."
    f.write_text(text)

    decision = policy.evaluate(f, source_root, text=text)
    assert decision.action == "quarantine"
    assert "pii:email" in decision.sensitivity_flags


def test_detect_cloud_sync_positive():
    assert policy.detect_cloud_sync(Path.home() / "Library/CloudStorage/OneDrive-Test/Vault") == "CloudStorage"


def test_detect_cloud_sync_negative(tmp_path):
    assert policy.detect_cloud_sync(tmp_path / "some/local/vault") is None


def test_log_decision_writes_no_content(tmp_path):
    log_path = tmp_path / "policy_decisions.jsonl"
    f = tmp_path / "secret.txt"
    text = "this text must never appear in the audit log"
    f.write_text(text)
    decision = policy.evaluate(f, tmp_path, text=text)

    policy.log_decision(log_path, f, decision, content_hash="deadbeef")

    logged = log_path.read_text()
    assert "deadbeef" in logged
    assert text not in logged
