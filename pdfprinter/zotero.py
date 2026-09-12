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
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field


@dataclass
class ZoteroCollection:
    id: int
    name: str
    parent: int | None


@dataclass
class ZoteroItem:
    title: str
    creators: str
    path: str
    mtime: float
    collections: set[int] = field(default_factory=set)


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


def load_library(
    storage_dir: str,
) -> tuple[list[ZoteroCollection], list[ZoteroItem]] | None:
    """Read collections and PDF items from zotero.sqlite.

    Works on a copy of the database because Zotero keeps the live one
    locked while running. Only attachments whose file actually exists
    locally are returned (a synced library lists many more). Returns
    None when the database is missing or unreadable.
    """
    data_dir = os.path.dirname(os.path.abspath(storage_dir))
    db_path = os.path.join(data_dir, "zotero.sqlite")
    if not os.path.isfile(db_path):
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="pdfprinter-zotero-") as tmpdir:
            tmp_db = os.path.join(tmpdir, "zotero.sqlite")
            shutil.copy(db_path, tmp_db)
            if os.path.isfile(db_path + "-wal"):
                shutil.copy(db_path + "-wal", tmp_db + "-wal")
            connection = sqlite3.connect(tmp_db)
            try:
                rows = _query_library(connection)
            finally:
                connection.close()
    except (sqlite3.Error, OSError):
        return None
    return _build_library(storage_dir, *rows)


def _query_library(connection: sqlite3.Connection):
    cur = connection.cursor()
    collections = cur.execute(
        "SELECT collectionID, collectionName, parentCollectionID FROM collections"
    ).fetchall()
    attachments = cur.execute(
        """
        SELECT ia.itemID, ia.parentItemID, ia.path, i.key
        FROM itemAttachments ia
        JOIN items i ON i.itemID = ia.itemID
        WHERE ia.contentType = 'application/pdf'
          AND ia.path LIKE 'storage:%'
          AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)
          AND (ia.parentItemID IS NULL
               OR ia.parentItemID NOT IN (SELECT itemID FROM deletedItems))
        """
    ).fetchall()
    titles = dict(
        cur.execute(
            """
            SELECT d.itemID, v.value FROM itemData d
            JOIN itemDataValues v ON v.valueID = d.valueID
            JOIN fields f ON f.fieldID = d.fieldID
            WHERE f.fieldName = 'title'
            """
        ).fetchall()
    )
    creator_rows = cur.execute(
        """
        SELECT ic.itemID, c.lastName FROM itemCreators ic
        JOIN creators c ON c.creatorID = ic.creatorID
        ORDER BY ic.itemID, ic.orderIndex
        """
    ).fetchall()
    collection_items = cur.execute(
        "SELECT collectionID, itemID FROM collectionItems"
    ).fetchall()
    return collections, attachments, titles, creator_rows, collection_items


def _build_library(storage_dir, collections, attachments, titles, creator_rows, collection_items):
    creators: dict[int, list[str]] = {}
    for item_id, last_name in creator_rows:
        creators.setdefault(item_id, []).append(last_name or "")
    item_collections: dict[int, set[int]] = {}
    for collection_id, item_id in collection_items:
        item_collections.setdefault(item_id, set()).add(collection_id)

    items: list[ZoteroItem] = []
    for attachment_id, parent_id, storage_path, key in attachments:
        file_name = storage_path[len("storage:"):]
        path = os.path.join(storage_dir, key, file_name)
        if not os.path.isfile(path):
            continue  # synced entry whose file isn't downloaded here
        owner = parent_id if parent_id is not None else attachment_id
        names = creators.get(owner, [])
        if len(names) > 2:
            creator = f"{names[0]} et al."
        else:
            creator = " & ".join(n for n in names if n)
        items.append(
            ZoteroItem(
                title=titles.get(owner) or file_name,
                creators=creator,
                path=path,
                mtime=os.path.getmtime(path),
                collections=item_collections.get(owner, set()),
            )
        )
    items.sort(key=lambda item: item.mtime, reverse=True)

    used = {c for item in items for c in item.collections}
    # keep collections that (transitively) contain a local PDF
    parents = {cid: parent for cid, _, parent in collections}
    for cid in list(used):
        while cid is not None:
            used.add(cid)
            cid = parents.get(cid)
    kept = [
        ZoteroCollection(cid, name, parent)
        for cid, name, parent in collections
        if cid in used
    ]
    kept.sort(key=lambda c: c.name.lower())
    return kept, items


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
