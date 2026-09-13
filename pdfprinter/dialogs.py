"""Dialogs: Zotero picker, user guide, about, print errors."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMessageBox,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from . import __version__, zotero
from .help import HELP_HTML


class ZoteroPickerDialog(QDialog):
    """Zotero-style picker: collections tree plus title/creator list."""

    def __init__(self, storage_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open from Zotero")
        self.resize(900, 520)
        self.selected_path: str | None = None

        library = zotero.load_library(storage_dir)
        if library is not None:
            self._collections, self._items = library
        else:
            # metadata unavailable: flat file listing as fallback
            self._collections = []
            self._items = [
                zotero.ZoteroItem(title=name, creators="", path=path, mtime=0)
                for name, path in zotero.list_pdfs(storage_dir)
            ]
        self._children: dict[int | None, list[zotero.ZoteroCollection]] = {}
        for collection in self._collections:
            self._children.setdefault(collection.parent, []).append(collection)

        layout = QVBoxLayout(self)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            f"Search {len(self._items)} PDFs by title or author…"
        )
        self.filter_edit.textChanged.connect(self._repopulate)
        layout.addWidget(self.filter_edit)

        splitter = QSplitter(self)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        root = QTreeWidgetItem(["My Library"])
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        self.tree.addTopLevelItem(root)
        self._add_collection_items(root, None)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self.tree.currentItemChanged.connect(self._repopulate)
        splitter.addWidget(self.tree)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Title", "Creator"])
        self.list.setRootIsDecorated(False)
        self.list.setColumnWidth(0, 460)
        self.list.itemDoubleClicked.connect(lambda _item, _col: self.accept())
        splitter.addWidget(self.list)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._repopulate()
        self.filter_edit.setFocus()

    def _add_collection_items(
        self, parent_item: QTreeWidgetItem, parent_id: int | None
    ) -> None:
        for collection in self._children.get(parent_id, []):
            item = QTreeWidgetItem([collection.name])
            item.setData(0, Qt.ItemDataRole.UserRole, collection.id)
            parent_item.addChild(item)
            self._add_collection_items(item, collection.id)

    def _selected_collection_ids(self) -> set[int] | None:
        """The picked collection and its descendants; None means all."""
        current = self.tree.currentItem()
        if current is None:
            return None
        collection_id = current.data(0, Qt.ItemDataRole.UserRole)
        if collection_id is None:
            return None
        ids = {collection_id}
        pending = [collection_id]
        while pending:
            for child in self._children.get(pending.pop(), []):
                ids.add(child.id)
                pending.append(child.id)
        return ids

    def _repopulate(self, *_args) -> None:
        tokens = self.filter_edit.text().lower().split()
        collection_ids = self._selected_collection_ids()
        self.list.clear()
        for entry in self._items:
            if collection_ids is not None and not (
                entry.collections & collection_ids
            ):
                continue
            haystack = (
                f"{entry.title} {entry.creators} "
                f"{os.path.basename(entry.path)}".lower()
            )
            if not all(token in haystack for token in tokens):
                continue
            item = QTreeWidgetItem([entry.title, entry.creators])
            item.setData(0, Qt.ItemDataRole.UserRole, entry.path)
            item.setToolTip(0, entry.path)
            self.list.addTopLevelItem(item)
        if self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))

    def accept(self) -> None:  # noqa: A003 (Qt naming)
        item = self.list.currentItem()
        if item is not None:
            self.selected_path = item.data(0, Qt.ItemDataRole.UserRole)
        super().accept()


class UserGuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Guide")
        self.resize(620, 640)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About PDF Printer")
        QMessageBox.about(
            parent,
            "About PDF Printer",
            f"<b>PDF Printer</b> {__version__}<br>"
            "Prints PDFs via CUPS with a true print preview.<br><br>"
            '<a href="https://github.com/CharlieJ515/PdfPrinter">'
            "github.com/CharlieJ515/PdfPrinter</a><br>MIT license",
        )

    def exec(self):  # the message box already ran
        return QDialog.DialogCode.Accepted


def show_print_error(parent, message: str, detail: str = "") -> str:
    """Show a print failure; returns "refresh" or "ok"."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("Print failed")
    box.setText(message)
    if detail:
        box.setInformativeText(detail)
    refresh = box.addButton("Refresh printers", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.exec()
    return "refresh" if box.clickedButton() is refresh else "ok"
