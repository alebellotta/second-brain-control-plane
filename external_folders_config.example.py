"""Example configuration for index_external_folders.py.
Copy this file to external_folders_config.py (not version-controlled — it's
gitignored by default) and customize it with the real paths of your external
folders (e.g. a cloud drive, a shared library, a network mount)."""
from pathlib import Path

EXTERNAL_FOLDERS = [
    {
        "source": Path("/absolute/path/to/external/folder"),
        "notes_name": "Folder name under Notes/",
    },
]
