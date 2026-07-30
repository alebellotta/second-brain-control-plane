"""Verifies FilesystemConnector actually satisfies the Connector protocol
(not just by convention) and behaves correctly against a real temp folder."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import connectors  # noqa: E402


def test_filesystem_connector_satisfies_protocol():
    conn = connectors.FilesystemConnector(Path("."))
    assert isinstance(conn, connectors.Connector)


def test_filesystem_connector_lists_and_reads_documents(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")

    conn = connectors.FilesystemConnector(tmp_path)
    docs = conn.list_documents()
    assert len(docs) == 2

    contents = {conn.read_document(d) for d in docs}
    assert contents == {b"hello", b"world"}

    for d in docs:
        assert conn.source_uri(d) == d
