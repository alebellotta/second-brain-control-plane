"""A minimal interface for read-only external document sources, so
index_external_folders.py's filesystem-based approach doesn't have to be the
only possible one.

This defines the extension point and ships ONE reference implementation
(FilesystemConnector, matching what index_external_folders.py already does)
— proof the interface actually fits a real case, not a speculative
abstraction with no user. It deliberately does NOT ship API-based connectors
for SharePoint/OneDrive-online, Google Drive, Notion, or Slack/Teams
exports: each of those needs its own auth flow, pagination, and rate-limit
handling — real, separate projects, not a variation on this one. Wiring a
future connector into the actual ingestion pipeline (today,
index_external_folders.py calls the filesystem directly rather than going
through this Protocol) is future work too; this module is the contract a
new connector would need to satisfy, not a working plugin system yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Connector(Protocol):
    """A read-only source of documents to ingest. Mirrors the existing
    Sources/ contract (something with a path/identifier and extractable
    bytes) without assuming the source is a local filesystem."""

    def list_documents(self) -> list[str]:
        """Returns an identifier for every document currently available from
        this source — a file path, an item ID, whatever is stable enough to
        pass back into read_document()/source_uri()."""
        ...

    def read_document(self, document_id: str) -> bytes:
        """Returns the raw bytes of one document, given an identifier
        returned by list_documents()."""
        ...

    def source_uri(self, document_id: str) -> str:
        """Returns a human-readable, stable reference to where this document
        actually lives — recorded in the note's manifest as source_file so
        provenance survives even if the connector's internal ID scheme
        doesn't mean anything to a human."""
        ...


class FilesystemConnector:
    """Reference implementation: the filesystem-based approach
    index_external_folders.py already uses in practice (read a local or
    locally-synced folder, read-only, never write back to it)."""

    def __init__(self, root: Path):
        self.root = root

    def list_documents(self) -> list[str]:
        return [str(p) for p in self.root.rglob("*") if p.is_file()]

    def read_document(self, document_id: str) -> bytes:
        return Path(document_id).read_bytes()

    def source_uri(self, document_id: str) -> str:
        return document_id
