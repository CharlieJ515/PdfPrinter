"""Dialogs: Zotero picker, user guide, about box and print errors.

Every dialog here is drawn with the design system in :mod:`theme`: the
GROUND background, Cormorant titles, small-caps section labels and the
accent-outlined primary button. The application-wide stylesheet (set in
``main``) already styles the plain controls; the local stylesheets below
only cover what it deliberately leaves generic (the frameless panes
inside the picker and the accent-outlined primary button).
"""

from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import QPointF, QRect, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, theme, zotero
from .help import HELP_HTML
from .widgets import ElidedLabel

HOMEPAGE = "https://github.com/CharlieJ515/PdfPrinter"

# --------------------------------------------------------------------------
# small shared building blocks
# --------------------------------------------------------------------------


def _rule(horizontal: bool = True) -> QFrame:
    """A 1 px hairline separator (styled by the app-wide QSS)."""
    line = QFrame()
    line.setFrameShape(
        QFrame.Shape.HLine if horizontal else QFrame.Shape.VLine
    )
    line.setFrameShadow(QFrame.Shadow.Plain)
    if horizontal:
        line.setFixedHeight(1)
    else:
        line.setFixedWidth(1)
    return line




def _accent_button(text: str, icon_name: str | None = None) -> QPushButton:
    """The accent-outlined primary button of the artboards."""
    button = QPushButton()
    button.setObjectName("accentButton")  # styled by the app QSS
    button.setFont(theme.body_font())
    button.setMinimumHeight(theme.CONTROL_HEIGHT)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        button.setIcon(theme.icon(icon_name, theme.ACCENT_TEXT, 13))
        button.setIconSize(QSize(13, 13))
        button.setText(f" {text}")  # Qt leaves no room after an icon
    else:
        button.setText(text)
    return button


def _plain_button(text: str) -> QPushButton:
    """A neutral (secondary) dialog button."""
    button = QPushButton(text)
    button.setFont(theme.body_font())
    button.setMinimumHeight(theme.CONTROL_HEIGHT)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button




class _AccentRowDelegate(QStyledItemDelegate):
    """Roomy rows whose selection carries one accent edge, not one per cell.

    The app-wide QSS gives ``QTreeView::item:selected`` a 2 px accent
    border-left, which Qt draws for *every* column — a multi-column view
    then looks striped. The picker's views switch that border off and let
    this delegate draw a single edge down the left of the whole row.
    """

    def __init__(self, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._height = height

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), self._height))
        return size

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if index.column() != 0 or not selected:
            return
        painter.save()
        painter.setClipping(False)
        painter.fillRect(
            QRect(0, option.rect.top(), 2, option.rect.height()),
            theme.qcolor(theme.ACCENT),
        )
        painter.restore()


def _format_added(mtime: float) -> str:
    """``3 Sep`` this year, ``Sep 2024`` in any other."""
    if not mtime:
        return ""
    try:
        stamp = datetime.fromtimestamp(mtime)
    except (OverflowError, OSError, ValueError):
        return ""
    if stamp.year == datetime.now().year:
        return f"{stamp.day} {stamp:%b}"
    return f"{stamp:%b %Y}"


# --------------------------------------------------------------------------
# Zotero picker
# --------------------------------------------------------------------------

_PANE_QSS = f"""
QTreeWidget {{
    background-color: {theme.GROUND};
    border: none;
    border-radius: 0px;
}}
QTreeWidget::item {{
    border-bottom: none;
    border-left: none;
    padding: 3px 12px 3px 5px;
}}
QTreeWidget::item:selected {{
    border-left: none;
}}
"""

#: Height of the two bands under the panes (the machine-only note and the
#: path bar) — equal so their hairlines read as one line across the dialog.
_FOOTNOTE_HEIGHT = 48

_LIST_QSS = f"""
QTreeWidget {{
    background-color: {theme.GROUND};
    border: none;
    border-radius: 0px;
}}
QTreeWidget::item {{
    padding: 3px 10px;
}}
QTreeWidget::item:selected {{
    border-left: none;
    border-top: 1px solid {theme.ACCENT};
    border-bottom: 1px solid {theme.ACCENT};
}}
QHeaderView::section {{
    background-color: {theme.GROUND};
    padding: 7px 10px;
}}
"""


class ZoteroPickerDialog(QDialog):
    """Zotero-style picker: collections tree plus title/creator/added list.

    The dialog is meant to be *reused*: the window keeps one instance and
    calls :meth:`exec` again, so the search text, the picked collection,
    the selection and the scroll position survive between openings.
    """

    def __init__(self, storage_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open from Zotero")
        self.resize(1000, 620)
        self.setMinimumSize(720, 420)
        self.selected_path: str | None = None
        self._data_dir = os.path.dirname(os.path.abspath(storage_dir))

        self._load(storage_dir)
        self._build()
        self._repopulate()
        self.filter_edit.setFocus()

    # ---------- data ----------

    def _load(self, storage_dir: str) -> None:
        library = zotero.load_library(storage_dir)
        if library is not None:
            self._collections, self._items = library
        else:
            # metadata unavailable: flat file listing as fallback
            self._collections = []
            self._items = []
            for name, path in zotero.list_pdfs(storage_dir):
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0
                self._items.append(
                    zotero.ZoteroItem(
                        title=name, creators="", path=path, mtime=mtime
                    )
                )
        self._children: dict[int | None, list[zotero.ZoteroCollection]] = {}
        for collection in self._collections:
            self._children.setdefault(collection.parent, []).append(collection)
        # every collection's own id plus its descendants', computed once
        self._subtrees: dict[int, set[int]] = {}
        for collection in self._collections:
            self._subtrees[collection.id] = self._descendants(collection.id)
        self._counts: dict[int, int] = {
            cid: sum(
                1 for item in self._items if item.collections & subtree
            )
            for cid, subtree in self._subtrees.items()
        }

    def _descendants(self, collection_id: int) -> set[int]:
        ids = {collection_id}
        pending = [collection_id]
        while pending:
            for child in self._children.get(pending.pop(), []):
                ids.add(child.id)
                pending.append(child.id)
        return ids

    # ---------- construction ----------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())
        layout.addWidget(_rule())
        layout.addWidget(self._body(), 1)
        layout.addWidget(_rule())
        layout.addWidget(self._footer())

        self.setTabOrder(self.filter_edit, self.tree)
        self.setTabOrder(self.tree, self.list)
        self.setTabOrder(self.list, self._cancel_button)
        self.setTabOrder(self._cancel_button, self._open_button)

    def _header(self) -> QWidget:
        bar = theme.styled_panel(QWidget())
        bar.setObjectName("headerBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(theme.PAD_SECTION, 13, theme.PAD_SECTION, 13)
        row.setSpacing(theme.GAP_ICON + 4)

        mark = QLabel()
        mark.setPixmap(theme.pixmap("zotero", theme.ACCENT, 19))
        mark.setFixedWidth(21)
        title = QLabel("Open from Zotero")
        title.setFont(theme.serif_font(16.0))
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setFont(theme.body_font())
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setFixedHeight(theme.CONTROL_HEIGHT)
        self.filter_edit.setMinimumWidth(240)
        self.filter_edit.setMaximumWidth(400)
        self.filter_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.filter_edit.addAction(
            theme.icon("search", theme.TEXT_FAINT, 14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.filter_edit.textChanged.connect(self._repopulate)
        row.addWidget(self.filter_edit, 1)
        return bar

    def _body(self) -> QWidget:
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._collections_pane(), 0)
        row.addWidget(_rule(horizontal=False))
        row.addWidget(self._items_pane(), 1)
        return body

    def _collections_pane(self) -> QWidget:
        pane = QWidget()
        pane.setFixedWidth(260)
        column = QVBoxLayout(pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        heading = theme.style_section_label(QLabel("Collections"))
        heading.setContentsMargins(theme.PAD_SECTION, 13, theme.PAD_SECTION, 9)
        column.addWidget(heading)

        self.tree = QTreeWidget()
        self.tree.setObjectName("collectionsTree")
        self.tree.setStyleSheet(_PANE_QSS)
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(15)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setIconSize(QSize(14, 14))
        self.tree.setFont(theme.body_font())
        self.tree.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tree.setItemDelegate(_AccentRowDelegate(28, self.tree))

        root = QTreeWidgetItem(["My Library", str(len(self._items))])
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        root.setIcon(0, theme.icon("zotero", theme.ACCENT, 14))
        self._style_count(root)
        self.tree.addTopLevelItem(root)
        self._add_collection_items(root, None)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self.tree.currentItemChanged.connect(self._repopulate)

        header = self.tree.header()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        column.addWidget(self.tree, 1)

        column.addWidget(_rule())
        note_box = QWidget()
        note_box.setFixedHeight(_FOOTNOTE_HEIGHT)
        note_layout = QVBoxLayout(note_box)
        note_layout.setContentsMargins(theme.PAD_SECTION, 8, theme.PAD_SECTION, 8)
        note_layout.setSpacing(0)
        note = theme.style_hint(
            QLabel("Only PDFs stored on this machine are listed.")
        )
        note.setWordWrap(True)
        note_layout.addWidget(note, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addWidget(note_box)
        return pane

    def _style_count(self, item: QTreeWidgetItem) -> None:
        item.setTextAlignment(
            1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        item.setForeground(1, theme.qcolor(theme.TEXT_FAINT))
        item.setFont(1, theme.body_font(theme.SMALL_POINT_SIZE, tabular=True))

    def _add_collection_items(
        self, parent_item: QTreeWidgetItem, parent_id: int | None
    ) -> None:
        for collection in self._children.get(parent_id, []):
            count = self._counts.get(collection.id, 0)
            item = QTreeWidgetItem([collection.name, str(count)])
            item.setData(0, Qt.ItemDataRole.UserRole, collection.id)
            item.setIcon(0, theme.icon("folder", theme.TEXT_MUTED, 14))
            self._style_count(item)
            parent_item.addChild(item)
            self._add_collection_items(item, collection.id)

    def _items_pane(self) -> QWidget:
        pane = QWidget()
        column = QVBoxLayout(pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.list = QTreeWidget()
        self.list.setObjectName("resultsList")
        self.list.setStyleSheet(_LIST_QSS)
        self.list.setHeaderLabels(["Title", "Creator", "Added"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setAllColumnsShowFocus(True)
        self.list.setIconSize(QSize(14, 14))
        self.list.setFont(theme.body_font())
        self.list.setItemDelegate(_AccentRowDelegate(32, self.list))

        header = theme.style_header_view(self.list.header())
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.Fixed)
        header.setSectionResizeMode(2, header.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        self.list.setColumnWidth(1, 180)
        self.list.setColumnWidth(2, 96)

        self.list.itemDoubleClicked.connect(lambda _item, _col: self.accept())
        self.list.currentItemChanged.connect(self._on_current_changed)

        self._empty_label = theme.style_hint(
            QLabel("No PDFs match this search.", self.list.viewport())
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overlay = QVBoxLayout(self.list.viewport())
        overlay.setContentsMargins(0, 0, 0, 0)
        overlay.addWidget(self._empty_label)
        self._empty_label.hide()

        column.addWidget(self.list, 1)
        column.addWidget(_rule())

        path_row = QWidget()
        path_row.setFixedHeight(_FOOTNOTE_HEIGHT)
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(theme.PAD_SECTION, 8, theme.PAD_SECTION, 8)
        path_layout.setSpacing(theme.GAP_ICON + 3)
        self._path_icon = QLabel()
        self._path_icon.setPixmap(theme.pixmap("doc", theme.TEXT_FAINT, 14))
        self._path_icon.setFixedWidth(16)
        self._path_label = ElidedLabel()
        self._path_label.setObjectName("metaLabel")
        self._path_label.setFont(theme.body_font(theme.SMALL_POINT_SIZE))
        path_layout.addWidget(self._path_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        path_layout.addWidget(self._path_label, 1)
        column.addWidget(path_row)
        return pane

    def _footer(self) -> QWidget:
        footer = QWidget()
        row = QHBoxLayout(footer)
        row.setContentsMargins(theme.PAD_SECTION, 12, theme.PAD_SECTION, 12)
        row.setSpacing(theme.GAP_CONTROL)
        row.addStretch(1)
        self._cancel_button = _plain_button("Cancel")
        self._cancel_button.setAutoDefault(False)
        self._cancel_button.clicked.connect(self.reject)
        self._open_button = _accent_button("Open", "check")
        self._open_button.setDefault(True)
        self._open_button.setAutoDefault(True)
        self._open_button.clicked.connect(self.accept)
        row.addWidget(self._cancel_button)
        row.addWidget(self._open_button)
        return footer

    # ---------- behaviour ----------

    def _selected_collection_ids(self) -> set[int] | None:
        """The picked collection and its descendants; None means all."""
        current = self.tree.currentItem()
        if current is None:
            return None
        collection_id = current.data(0, Qt.ItemDataRole.UserRole)
        if collection_id is None:
            return None
        return self._subtrees.get(collection_id, {collection_id})

    def _matches(self, entry, tokens, collection_ids) -> bool:
        if collection_ids is not None and not (
            entry.collections & collection_ids
        ):
            return False
        if not tokens:
            return True
        haystack = (
            f"{entry.title} {entry.creators} "
            f"{os.path.basename(entry.path)}"
        ).lower()
        return all(token in haystack for token in tokens)

    def _repopulate(self, *_args) -> None:
        tokens = self.filter_edit.text().lower().split()
        collection_ids = self._selected_collection_ids()
        in_scope = sum(
            1 for entry in self._items if self._matches(entry, (), collection_ids)
        )
        self.filter_edit.setPlaceholderText(
            f"Search {in_scope:,} PDF{'' if in_scope == 1 else 's'} "
            "by title or author…"
        )

        self.list.clear()
        doc_icon = theme.icon("doc", theme.TEXT_FAINT, 14)
        for entry in self._items:  # already most-recent-first
            if not self._matches(entry, tokens, collection_ids):
                continue
            item = QTreeWidgetItem(
                [entry.title, entry.creators, _format_added(entry.mtime)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, entry.path)
            item.setIcon(0, doc_icon)
            item.setToolTip(0, f"{entry.title}\n{self._relative(entry.path)}")
            item.setFont(2, theme.body_font(tabular=True))
            item.setForeground(2, theme.qcolor(theme.TEXT_MUTED))
            self.list.addTopLevelItem(item)

        count = self.list.topLevelItemCount()
        self._empty_label.setVisible(count == 0)
        self._open_button.setEnabled(count > 0)
        if count:
            self.list.setCurrentItem(self.list.topLevelItem(0))
        else:
            self._set_path(None)

    def _on_current_changed(self, current, previous) -> None:
        # the document glyph and the (explicitly greyed) date pick up the
        # accent on the selected row, like the rest of its text
        if previous is not None:
            previous.setIcon(0, theme.icon("doc", theme.TEXT_FAINT, 14))
            previous.setForeground(2, theme.qcolor(theme.TEXT_MUTED))
        if current is not None:
            current.setIcon(0, theme.icon("doc", theme.ACCENT_TEXT, 14))
            current.setForeground(2, theme.qcolor(theme.ACCENT_TEXT))
            self._set_path(current.data(0, Qt.ItemDataRole.UserRole))
        else:
            self._set_path(None)

    def _relative(self, path: str) -> str:
        """``storage/KEY/file.pdf`` — the path as Zotero itself stores it."""
        try:
            return os.path.relpath(path, self._data_dir)
        except ValueError:  # different drive/root — show the absolute path
            return path

    def _set_path(self, path: str | None) -> None:
        if not path:
            self._path_label.setFullText("")
            self._path_icon.setVisible(False)
            return
        self._path_icon.setVisible(True)
        self._path_label.setFullText(self._relative(path))

    def accept(self) -> None:  # noqa: A003 (Qt naming)
        item = self.list.currentItem()
        self.selected_path = (
            item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        )
        super().accept()


# --------------------------------------------------------------------------
# user guide
# --------------------------------------------------------------------------

_BROWSER_QSS = f"""
QTextBrowser {{
    background-color: {theme.GROUND};
    border: none;
    border-radius: 0px;
    padding: 4px 20px 16px 20px;
}}
"""


class UserGuideDialog(QDialog):
    """The full guide, set in Lora with small-caps section rules."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Guide")
        self.resize(700, 720)
        self.setMinimumSize(480, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = theme.styled_panel(QWidget())
        bar.setObjectName("headerBar")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(20, 13, 20, 13)
        bar_row.setSpacing(theme.GAP_ICON + 4)
        mark = QLabel()
        mark.setPixmap(theme.pixmap("duplex", theme.ACCENT, 18))
        title = QLabel("User Guide")
        title.setFont(theme.serif_font(16.0))
        bar_row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        bar_row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        bar_row.addStretch(1)
        layout.addWidget(bar)
        layout.addWidget(_rule())

        self.browser = QTextBrowser()
        self.browser.setStyleSheet(_BROWSER_QSS)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setOpenExternalLinks(True)
        self.browser.setFont(theme.body_font())
        self.browser.document().setDocumentMargin(0)
        self.browser.setHtml(HELP_HTML)
        _letterspace_section_labels(self.browser)
        layout.addWidget(self.browser, 1)

        layout.addWidget(_rule())
        footer = QWidget()
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(20, 12, 20, 12)
        footer_row.addStretch(1)
        close = _accent_button("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        footer_row.addWidget(close)
        layout.addWidget(footer)
        close.setFocus()


def _letterspace_section_labels(browser: QTextBrowser) -> None:
    """Letterspace the guide's small-caps headings.

    Qt's rich-text CSS has no ``letter-spacing``, so the section labels —
    short, all-uppercase paragraphs — get the design system's 112 %
    spacing applied to the document after parsing.
    """
    document = browser.document()
    block = document.begin()
    while block.isValid():
        text = block.text().strip()
        if text and len(text) < 60 and text == text.upper() and text[0].isalpha():
            cursor = QTextCursor(block)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            fmt = QTextCharFormat()
            fmt.setFontLetterSpacingType(QFont.SpacingType.PercentageSpacing)
            fmt.setFontLetterSpacing(theme.SECTION_LETTER_SPACING)
            cursor.mergeCharFormat(fmt)
        block = block.next()


# --------------------------------------------------------------------------
# about
# --------------------------------------------------------------------------


class AboutDialog(QDialog):
    """Centred title card: icon, name, version, tagline, link."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About PDF Printer")
        self.setFixedSize(420, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 26)
        layout.setSpacing(0)
        center = Qt.AlignmentFlag.AlignHCenter

        mark = QLabel()
        mark.setPixmap(theme.pixmap("print", theme.ACCENT, 28))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark, 0, center)
        layout.addSpacing(14)

        title = QLabel("PDF Printer")
        title.setFont(theme.serif_font(24.0))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title, 0, center)
        layout.addSpacing(6)

        version = theme.style_section_label(QLabel(f"Version {__version__}"))
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version, 0, center)
        layout.addSpacing(14)

        hairline = _rule()
        hairline.setFixedWidth(96)
        layout.addWidget(hairline, 0, center)
        layout.addSpacing(14)

        tagline = QLabel("Prints PDFs via CUPS with a true print preview.")
        tagline.setFont(theme.serif_font(14.0))
        tagline.setWordWrap(True)
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tagline)
        layout.addSpacing(12)

        link = QLabel(
            f'<a href="{HOMEPAGE}" style="color:{theme.ACCENT_TEXT};">'
            "github.com/CharlieJ515/PdfPrinter</a>"
        )
        link.setFont(theme.body_font())
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setTextFormat(Qt.TextFormat.RichText)
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        palette = link.palette()
        palette.setColor(palette.ColorRole.Link, theme.qcolor(theme.ACCENT_TEXT))
        link.setPalette(palette)
        layout.addWidget(link, 0, center)
        layout.addSpacing(6)

        license_label = theme.style_hint(QLabel("MIT license"))
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(license_label, 0, center)

        layout.addStretch(1)
        close = _accent_button("Close")
        close.setDefault(True)
        close.setMinimumWidth(110)
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, center)
        close.setFocus()


# --------------------------------------------------------------------------
# print failure
# --------------------------------------------------------------------------


class _PrintErrorDialog(QDialog):
    """``Print failed`` — message, optional technical detail, two actions."""

    REFRESH = 2

    def __init__(self, parent, message: str, detail: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Print failed")
        self.setMinimumWidth(460)
        self.setMaximumWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme.GAP_ICON + 5)
        mark = QLabel()
        mark.setPixmap(theme.pixmap("warning", theme.ERROR, 22))
        mark.setFixedSize(24, 24)
        title = QLabel("Print failed")
        title.setFont(theme.serif_font(16.0))
        head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        layout.addLayout(head)
        layout.addSpacing(12)

        body = QVBoxLayout()
        body.setContentsMargins(24 + theme.GAP_ICON + 5, 0, 0, 0)
        body.setSpacing(8)
        self.message_label = QLabel(message)
        self.message_label.setFont(theme.body_font())
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        body.addWidget(self.message_label)
        if detail:
            self.detail_label = QLabel(detail)
            self.detail_label.setObjectName("metaLabel")
            self.detail_label.setFont(theme.body_font(theme.SMALL_POINT_SIZE))
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            body.addWidget(self.detail_label)
        layout.addLayout(body)
        layout.addSpacing(24)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.GAP_CONTROL)
        row.addStretch(1)
        self.refresh_button = _plain_button("Refresh printers")
        self.refresh_button.setAutoDefault(False)
        self.refresh_button.clicked.connect(lambda: self.done(self.REFRESH))
        self.ok_button = _accent_button("OK")
        self.ok_button.setMinimumWidth(84)
        self.ok_button.setDefault(True)
        self.ok_button.setAutoDefault(True)
        self.ok_button.clicked.connect(self.accept)
        row.addWidget(self.refresh_button)
        row.addWidget(self.ok_button)
        layout.addLayout(row)
        self.ok_button.setFocus()


def show_print_error(parent, message: str, detail: str = "") -> str:
    """Show a print failure; returns ``"refresh"`` or ``"ok"``."""
    dialog = _PrintErrorDialog(parent, message, detail)
    result = dialog.exec()
    return "refresh" if result == _PrintErrorDialog.REFRESH else "ok"
