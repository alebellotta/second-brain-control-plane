"""Ingestion policy: centralizes the "should this file be processed, and how"
decisions that used to be scattered across common.py (the symlink/path-escape
check) and index_external_folders.py (the file-size limit) into one place,
with one audit trail.

Deliberately takes no dependency on common.py, so it stays independently
testable and reusable outside this project's specific vault/Ollama setup:
callers pass in whatever root/limits apply to their situation.

Two-phase design, mirroring how a file actually becomes a note:
- `evaluate(path, source_root)` — called BEFORE reading the file: path
  resolution (symlink escape), size. Cheap, no file content involved yet.
- `evaluate(path, source_root, text=...)` — called AFTER extraction, with the
  extracted text: secret/PII pattern scan. Determines whether the content is
  safe to embed semantically or should be quarantined (indexed by path/hash
  only, never sent to the embedding model).

This is deliberately a set of regex heuristics, not a trained classifier: it
catches the obvious, common cases (a leaked API token, an email address, a
card-like digit run) and says nothing about subtler sensitive content (legal
privilege, board material without a recognizable pattern). Treat a "clean"
result as "nothing obvious was found", not as a guarantee.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("policy")

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB — same default already used by index_external_folders.py

# Folder-name fragments that indicate a path is actually a cloud-sync mount
# rather than genuinely local storage — operationalizes the paper's finding
# that "local processing" and "local storage" are two different guarantees.
CLOUD_SYNC_MARKERS = ("CloudStorage", "OneDrive", "iCloud~", "Google Drive", "Dropbox")

_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

_PII_PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "iban-like": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "card-like": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


@dataclass
class PolicyDecision:
    action: str  # "allow" | "quarantine" | "deny"
    reasons: list[str] = field(default_factory=list)
    sensitivity_flags: list[str] = field(default_factory=list)
    mime_type: str | None = None

    @property
    def allow_semantic_embedding(self) -> bool:
        return self.action == "allow"


def evaluate(path: Path, source_root: Path, text: str | None = None) -> PolicyDecision:
    """See module docstring for the two-phase design. Returns a decision;
    never raises for expected conditions (missing file, oversized file,
    symlink escape) — those are all represented as a "deny" decision."""
    try:
        resolved = path.resolve()
        resolved.relative_to(source_root.resolve())
    except ValueError:
        return PolicyDecision(
            action="deny",
            reasons=["resolved path falls outside the trusted source root (likely a symlink)"],
        )

    try:
        size = path.stat().st_size
    except OSError:
        return PolicyDecision(action="deny", reasons=["could not stat file"])
    if size > MAX_FILE_SIZE:
        return PolicyDecision(action="deny", reasons=[f"file size {size} exceeds MAX_FILE_SIZE ({MAX_FILE_SIZE})"])

    mime_type, _ = mimetypes.guess_type(str(path))

    flags: list[str] = []
    if text is not None:
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            flags.append("secret-like-content")
        for name, pattern in _PII_PATTERNS.items():
            if pattern.search(text):
                flags.append(f"pii:{name}")

    if flags:
        return PolicyDecision(
            action="quarantine",
            reasons=["sensitive content pattern matched — see sensitivity_flags"],
            sensitivity_flags=flags,
            mime_type=mime_type,
        )

    return PolicyDecision(action="allow", mime_type=mime_type)


def detect_cloud_sync(vault_dir: Path) -> str | None:
    """Section 5.6 of the paper, operationalized: checks whether the vault's
    real path resolves through a known cloud-sync mount, instead of relying
    on someone noticing by hand (which is how it was originally discovered
    in this project). Returns the matched marker, or None."""
    resolved = str(vault_dir.resolve())
    for marker in CLOUD_SYNC_MARKERS:
        if marker in resolved:
            return marker
    return None


def log_decision(log_path: Path, path: Path, decision: PolicyDecision, content_hash: str | None = None) -> None:
    """Appends one JSON Lines record to the local audit log. Never the file's
    content — only path, hash, and the decision itself."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "path": str(path),
            "content_hash": content_hash,
            "action": decision.action,
            "reasons": decision.reasons,
            "sensitivity_flags": decision.sensitivity_flags,
            "mime_type": decision.mime_type,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Could not write to the policy decision log at %s", log_path)
