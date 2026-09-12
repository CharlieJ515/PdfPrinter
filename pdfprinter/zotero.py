"""Find PDFs in a Zotero library.

Zotero stores attachments as storage/<8-char key>/<file>.pdf inside its
data directory, which is painful to navigate in a file dialog. This
locates the storage directory (from Zotero's own prefs.js, falling back
to the default ~/Zotero) and lists the PDFs in it.
"""

from __future__ import annotations

import glob
import os
import re


def find_storage_dir() -> str | None:
    override = os.environ.get("PDFPRINTER_ZOTERO_DIR")
    if override:
        return override if os.path.isdir(override) else None
    for prefs in glob.glob(os.path.expanduser("~/.zotero/zotero/*/prefs.js")):
        try:
            with open(prefs, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        match = re.search(
            r'user_pref\("extensions\.zotero\.dataDir",\s*"([^"]+)"\)', text
        )
        if match:
            storage = os.path.join(match.group(1), "storage")
            if os.path.isdir(storage):
                return storage
    default = os.path.expanduser("~/Zotero/storage")
    return default if os.path.isdir(default) else None


def list_pdfs(storage_dir: str) -> list[tuple[str, str]]:
    """Return (filename, path) for every PDF, most recently modified first."""
    entries: list[tuple[float, str, str]] = []
    try:
        key_dirs = list(os.scandir(storage_dir))
    except OSError:
        return []
    for key_dir in key_dirs:
        if not key_dir.is_dir():
            continue
        try:
            files = list(os.scandir(key_dir.path))
        except OSError:
            continue
        for entry in files:
            if entry.is_file() and entry.name.lower().endswith(".pdf"):
                entries.append((entry.stat().st_mtime, entry.name, entry.path))
    entries.sort(reverse=True)
    return [(name, path) for _, name, path in entries]
