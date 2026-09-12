"""Main window: PDF preview on the left, print options on the right."""

from __future__ import annotations

import os
import shutil
import tempfile

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPoint,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsColorizeEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPinchGesture,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import printing, zotero

DUPLEX_CHOICES = [
    ("One-sided", "one-sided"),
    ("Double-sided (long edge)", "two-sided-long-edge"),
    ("Double-sided (short edge)", "two-sided-short-edge"),
]
COLOR_CHOICES = [
    ("Printer default", ""),
    ("Color", "color"),
    ("Grayscale", "monochrome"),
]
MEDIA_CHOICES = [
    ("Printer default", ""),
    ("A4", "A4"),
    ("A5", "A5"),
    ("Letter", "Letter"),
    ("Legal", "Legal"),
]
ORIENTATION_CHOICES = [
    ("Portrait", False),
    ("Landscape", True),
]
NUP_CHOICES = [("1", 1), ("2", 2), ("4", 4), ("6", 6), ("9", 9), ("16", 16)]
NUP_LAYOUT_CHOICES = [
    ("Across, then down (default)", ""),
    ("Down, then across", "tblr"),
    ("Right to left", "rltb"),
    ("Bottom up", "btlr"),
]
PAGE_SET_CHOICES = [
    ("All pages", ""),
    ("Odd pages only", "odd"),
    ("Even pages only", "even"),
]
SCALING_CHOICES = [
    ("Automatic (default)", ""),
    ("Fit to page", "fit"),
    ("Fill page", "fill"),
    ("No scaling", "none"),
]

ZOOM_STEP = 1.25
MIN_ZOOM = 0.2
MAX_ZOOM = 8.0
SCROLL_MULTIPLIER = 2.0  # touchpad pixel deltas feel too small at 1:1
WHEEL_STEP_PX = 220  # scroll distance per mouse-wheel notch


class ZoomablePdfView(QPdfView):
    """QPdfView with anchored zoom, inertial scrolling and pinch-to-zoom.

    - Zoom keeps the anchor point (viewport center, cursor, or pinch
      center) fixed on screen instead of zooming around the top-left.
    - Mouse-wheel steps are animated; touchpad scrolling follows the
      fingers and glides to a stop when they lift (macOS-style).
    - Touchpad pinch arrives as a native zoom gesture (Wayland/X11) or a
      touch pinch gesture; both zoom around the gesture center.
    """

    zoom_changed = pyqtSignal(float)  # new factor; 0.0 means fit-width

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grabGesture(Qt.GestureType.PinchGesture)

        def _make_anim() -> QVariantAnimation:
            anim = QVariantAnimation(self)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.valueChanged.connect(
                lambda v: self.verticalScrollBar().setValue(round(v))
            )
            return anim

        self._wheel_anim = _make_anim()  # discrete mouse-wheel steps
        self._glide_anim = _make_anim()  # touchpad lift-off inertia
        self._scroll_clock = QElapsedTimer()
        self._scroll_velocity = 0.0  # px/ms of recent touchpad motion

        # pinch events arrive far faster than QPdfView can re-render;
        # throttle them so the view doesn't flash on every event
        self._pinch_pending = 1.0
        self._pinch_anchor: QPoint | None = None
        self._pinch_timer = QTimer(self)
        self._pinch_timer.setSingleShot(True)
        self._pinch_timer.setInterval(50)
        self._pinch_timer.timeout.connect(self._flush_pinch)

        # margin guides (left, top, right, bottom) in PDF points
        self._margin_guides: tuple[float, float, float, float] | None = None
        self._mirror_guides = False

    # ---------- margin guides ----------

    def set_margin_guides(
        self,
        guides: tuple[float, float, float, float] | None,
        mirror: bool = False,
    ) -> None:
        """Show dashed guides inset by (left, top, right, bottom) points.

        With mirror=True, even pages swap left and right (binding).
        """
        if guides is not None and not any(guides):
            guides = None
        self._margin_guides = guides
        self._mirror_guides = mirror
        self.viewport().update()

    def _page_scale(self) -> float:
        """Pixels per PDF point at the current zoom."""
        doc = self.document()
        if doc is None or doc.pageCount() == 0:
            return 1.0
        if self.zoomMode() == QPdfView.ZoomMode.Custom:
            return self.zoomFactor() * self.logicalDpiX() / 72.0
        widest = max(
            doc.pagePointSize(p).width() for p in range(doc.pageCount())
        )
        margins = self.documentMargins()
        available = self.viewport().width() - margins.left() - margins.right()
        return max(available, 1) / widest

    def _page_rects(self) -> list[QRectF]:
        """Each page's rectangle in viewport coordinates."""
        doc = self.document()
        if doc is None or doc.pageCount() == 0:
            return []
        scale = self._page_scale()
        margins = self.documentMargins()
        spacing = self.pageSpacing()
        widest = max(
            doc.pagePointSize(p).width() for p in range(doc.pageCount())
        )
        content_w = max(
            self.viewport().width(),
            widest * scale + margins.left() + margins.right(),
        )
        offset_x = self.horizontalScrollBar().value()
        offset_y = self.verticalScrollBar().value()
        rects = []
        y = float(margins.top())
        for page in range(doc.pageCount()):
            size = doc.pagePointSize(page)
            w, h = size.width() * scale, size.height() * scale
            rects.append(QRectF((content_w - w) / 2 - offset_x, y - offset_y, w, h))
            y += h + spacing
        return rects

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        if self._margin_guides is None:
            return
        left, top, right, bottom = self._margin_guides
        scale = self._page_scale()
        painter = QPainter(self.viewport())
        pen = QPen(QColor(220, 60, 60, 170))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        viewport_rect = QRectF(self.viewport().rect())
        for index, rect in enumerate(self._page_rects()):
            if not rect.intersects(viewport_rect):
                continue
            if self._mirror_guides and index % 2 == 1:  # even page number
                page_left, page_right = right, left
            else:
                page_left, page_right = left, right
            painter.drawRect(
                rect.adjusted(
                    page_left * scale,
                    top * scale,
                    -page_right * scale,
                    -bottom * scale,
                )
            )

    # ---------- zoom ----------

    def current_zoom(self) -> float:
        if self.zoomMode() == QPdfView.ZoomMode.Custom:
            return self.zoomFactor()
        # estimate the effective fit-to-width factor so the first manual
        # zoom step starts from what is on screen instead of jumping to 1.0
        doc = self.document()
        if doc is not None and doc.pageCount() > 0:
            page_px = doc.pagePointSize(0).width() / 72.0 * self.logicalDpiX()
            if page_px > 0:
                return max(MIN_ZOOM, (self.viewport().width() - 20) / page_px)
        return 1.0

    def apply_zoom(self, factor: float, anchor: QPoint | None = None) -> None:
        old = self.current_zoom()
        factor = max(MIN_ZOOM, min(MAX_ZOOM, factor))
        if anchor is None:
            anchor = self.viewport().rect().center()
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        # document coordinates currently under the anchor point
        doc_x = hbar.value() + anchor.x()
        doc_y = vbar.value() + anchor.y()
        self._stop_scroll_anims()
        # suppress repaints between the zoom and the scroll correction so
        # the view doesn't briefly show the wrongly-positioned content
        self.setUpdatesEnabled(False)
        try:
            self.setZoomMode(QPdfView.ZoomMode.Custom)
            self.setZoomFactor(factor)
            ratio = factor / old
            hbar.setValue(round(doc_x * ratio - anchor.x()))
            vbar.setValue(round(doc_y * ratio - anchor.y()))
        finally:
            self.setUpdatesEnabled(True)
        self.zoom_changed.emit(factor)

    def zoom_in(self, anchor: QPoint | None = None) -> None:
        self.apply_zoom(self.current_zoom() * ZOOM_STEP, anchor)

    def zoom_out(self, anchor: QPoint | None = None) -> None:
        self.apply_zoom(self.current_zoom() / ZOOM_STEP, anchor)

    def fit_width(self) -> None:
        self._stop_scroll_anims()
        self.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.zoom_changed.emit(0.0)

    # ---------- pinch-to-zoom ----------

    def event(self, e):
        if e.type() == QEvent.Type.NativeGesture:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self._on_pinch(1.0 + e.value(), e.position().toPoint())
                return True
        elif e.type() == QEvent.Type.Gesture:
            pinch = e.gesture(Qt.GestureType.PinchGesture)
            if isinstance(pinch, QPinchGesture):
                if (
                    pinch.changeFlags()
                    & QPinchGesture.ChangeFlag.ScaleFactorChanged
                ):
                    anchor = self.mapFromGlobal(pinch.centerPoint().toPoint())
                    self._on_pinch(pinch.scaleFactor(), anchor)
                return True
        return super().event(e)

    def _on_pinch(self, scale: float, anchor: QPoint) -> None:
        self._pinch_pending *= scale
        self._pinch_anchor = anchor
        if not self._pinch_timer.isActive():
            self._flush_pinch()
            self._pinch_timer.start()

    def _flush_pinch(self) -> None:
        if abs(self._pinch_pending - 1.0) > 1e-4:
            self.apply_zoom(
                self.current_zoom() * self._pinch_pending, self._pinch_anchor
            )
        self._pinch_pending = 1.0

    # ---------- scrolling ----------

    def _stop_scroll_anims(self) -> None:
        self._wheel_anim.stop()
        self._glide_anim.stop()
        self._scroll_velocity = 0.0

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # proportional to the delta so touchpad Ctrl+scroll zooms
            # smoothly instead of one full step per event
            dy = event.angleDelta().y()
            if dy:
                scale = ZOOM_STEP ** (dy / 120.0)
                self._on_pinch(scale, event.position().toPoint())
            event.accept()
            return

        pixel_delta = event.pixelDelta() * SCROLL_MULTIPLIER
        phase = event.phase()
        if not pixel_delta.isNull() or phase != Qt.ScrollPhase.NoScrollPhase:
            # touchpad: track the fingers, then glide on lift-off
            self._wheel_anim.stop()
            self._glide_anim.stop()
            vbar = self.verticalScrollBar()
            hbar = self.horizontalScrollBar()
            if pixel_delta.y():
                vbar.setValue(vbar.value() - pixel_delta.y())
            if pixel_delta.x():
                hbar.setValue(hbar.value() - pixel_delta.x())

            if phase == Qt.ScrollPhase.ScrollBegin:
                self._scroll_clock.start()
                self._scroll_velocity = 0.0
            elif phase == Qt.ScrollPhase.ScrollUpdate:
                if self._scroll_clock.isValid():
                    ms = max(1, self._scroll_clock.restart())
                else:
                    self._scroll_clock.start()
                    ms = 16
                # low-pass filter so one jittery event doesn't set the glide
                velocity = pixel_delta.y() / ms
                self._scroll_velocity = (
                    0.6 * self._scroll_velocity + 0.4 * velocity
                )
            elif phase == Qt.ScrollPhase.ScrollEnd:
                self._start_glide()
            event.accept()
            return

        # classic mouse wheel: animate the step instead of jumping
        steps = event.angleDelta().y() / 120.0
        if steps:
            self._animate_wheel_step(steps * WHEEL_STEP_PX)
            event.accept()
        else:
            super().wheelEvent(event)

    def _animate_wheel_step(self, delta_px: float) -> None:
        bar = self.verticalScrollBar()
        if self._wheel_anim.state() == QAbstractAnimation.State.Running:
            target = float(self._wheel_anim.endValue())
        else:
            target = float(bar.value())
        target = min(bar.maximum(), max(bar.minimum(), target - delta_px))
        self._glide_anim.stop()
        self._wheel_anim.stop()
        self._wheel_anim.setStartValue(float(bar.value()))
        self._wheel_anim.setEndValue(target)
        self._wheel_anim.setDuration(300)
        self._wheel_anim.start()

    def _start_glide(self) -> None:
        velocity = self._scroll_velocity  # px/ms at lift-off
        self._scroll_velocity = 0.0
        if abs(velocity) < 0.05:
            return
        bar = self.verticalScrollBar()
        distance = max(-12000.0, min(12000.0, velocity * 1000))
        target = min(bar.maximum(), max(bar.minimum(), bar.value() - distance))
        duration = int(min(2500, max(600, abs(distance) * 1.1)))
        self._glide_anim.stop()
        self._glide_anim.setStartValue(float(bar.value()))
        self._glide_anim.setEndValue(float(target))
        self._glide_anim.setDuration(duration)
        self._glide_anim.start()


class ZoteroDialog(QDialog):
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


class MainWindow(QMainWindow):
    def __init__(self, pdf_path: str | None = None):
        super().__init__()
        self.setWindowTitle("PDF Printer")
        self.resize(1000, 700)
        self.setAcceptDrops(True)

        self.document = QPdfDocument(self)
        self.current_path: str | None = None
        self._subset_dir: tempfile.TemporaryDirectory | None = None
        self._showing_transformed = False
        self._zotero_dialog: ZoteroDialog | None = None

        self._build_ui()
        self._build_menu()

        if pdf_path:
            self.load_pdf(pdf_path)

    # ---------- UI construction ----------

    def _build_ui(self) -> None:
        self.viewer = ZoomablePdfView(self)
        self.viewer.setDocument(self.document)
        self.viewer.setPageMode(QPdfView.PageMode.MultiPage)
        self.viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.viewer.zoom_changed.connect(self._on_zoom_changed)

        zoom_bar = QHBoxLayout()
        zoom_out_button = QPushButton("−")
        zoom_out_button.setFixedWidth(36)
        zoom_out_button.setToolTip("Zoom out (Ctrl+-, Ctrl+scroll)")
        zoom_out_button.clicked.connect(self.zoom_out)
        zoom_in_button = QPushButton("+")
        zoom_in_button.setFixedWidth(36)
        zoom_in_button.setToolTip("Zoom in (Ctrl++, Ctrl+scroll)")
        zoom_in_button.clicked.connect(self.zoom_in)
        fit_button = QPushButton("Fit width")
        fit_button.setToolTip("Fit page to window width (Ctrl+0)")
        fit_button.clicked.connect(self.fit_width)
        zoom_bar.addWidget(zoom_out_button)
        zoom_bar.addWidget(zoom_in_button)
        zoom_bar.addWidget(fit_button)
        zoom_bar.addStretch(1)

        viewer_column = QVBoxLayout()
        viewer_column.addLayout(zoom_bar)
        viewer_column.addWidget(self.viewer, stretch=1)

        side = QWidget(self)
        side.setFixedWidth(300)
        side_layout = QVBoxLayout(side)

        open_button = QPushButton("Open PDF…")
        open_button.clicked.connect(self.open_dialog)
        side_layout.addWidget(open_button)

        self.zotero_storage = zotero.find_storage_dir()
        if self.zotero_storage:
            zotero_button = QPushButton("Open from Zotero…")
            zotero_button.clicked.connect(self.open_zotero_dialog)
            side_layout.addWidget(zotero_button)

        self.file_label = QLabel("No file loaded")
        self.file_label.setWordWrap(True)
        side_layout.addWidget(self.file_label)

        options = QGroupBox("Print options")
        form = QFormLayout(options)

        self.printer_combo = QComboBox()
        form.addRow("Printer", self.printer_combo)

        refresh_button = QPushButton("Refresh printers")
        refresh_button.clicked.connect(self.refresh_printers)
        form.addRow("", refresh_button)

        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 999)
        form.addRow("Copies", self.copies_spin)

        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("All pages (e.g. 1-4,7)")
        form.addRow("Pages", self.range_edit)

        self.duplex_combo = QComboBox()
        for label, value in DUPLEX_CHOICES:
            self.duplex_combo.addItem(label, value)
        form.addRow("Duplex", self.duplex_combo)

        self.color_combo = QComboBox()
        for label, value in COLOR_CHOICES:
            self.color_combo.addItem(label, value)
        self.color_combo.currentIndexChanged.connect(self._apply_color_preview)
        form.addRow("Color", self.color_combo)

        side_layout.addWidget(options)

        self.more_button = QPushButton("Show more options")
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(self._toggle_more_options)
        side_layout.addWidget(self.more_button)

        self.more_group = QGroupBox("More options")
        self.more_group.setVisible(False)
        more_form = QFormLayout(self.more_group)
        form = more_form  # the remaining rows are the advanced ones

        self.media_combo = QComboBox()
        for label, value in MEDIA_CHOICES:
            self.media_combo.addItem(label, value)
        form.addRow("Paper", self.media_combo)

        self.orientation_combo = QComboBox()
        for label, value in ORIENTATION_CHOICES:
            self.orientation_combo.addItem(label, value)
        form.addRow("Orientation", self.orientation_combo)

        self.nup_combo = QComboBox()
        for label, value in NUP_CHOICES:
            self.nup_combo.addItem(label, value)
        form.addRow("Pages/sheet", self.nup_combo)

        self.nup_layout_combo = QComboBox()
        for label, value in NUP_LAYOUT_CHOICES:
            self.nup_layout_combo.addItem(label, value)
        self.nup_layout_combo.setEnabled(False)
        self.nup_combo.currentIndexChanged.connect(
            lambda: self.nup_layout_combo.setEnabled(
                self.nup_combo.currentData() > 1
            )
        )
        form.addRow("N-up order", self.nup_layout_combo)

        # per-printer options, filled by _update_capabilities
        self.quality_combo = QComboBox()
        form.addRow("Quality", self.quality_combo)
        self.source_combo = QComboBox()
        form.addRow("Paper source", self.source_combo)
        self.mediatype_combo = QComboBox()
        form.addRow("Media type", self.mediatype_combo)

        self.scaling_combo = QComboBox()
        for label, value in SCALING_CHOICES:
            self.scaling_combo.addItem(label, value)
        form.addRow("Scaling", self.scaling_combo)

        self.pageset_combo = QComboBox()
        for label, value in PAGE_SET_CHOICES:
            self.pageset_combo.addItem(label, value)
        form.addRow("Page set", self.pageset_combo)

        self.collate_check = QCheckBox("Collate copies")
        self.collate_check.setEnabled(False)  # only meaningful for copies > 1
        self.copies_spin.valueChanged.connect(
            lambda v: self.collate_check.setEnabled(v > 1)
        )
        form.addRow("", self.collate_check)

        self.reverse_check = QCheckBox("Reverse order")
        form.addRow("", self.reverse_check)

        side_layout.addWidget(self.more_group)

        margins_group = QGroupBox("Margins (mm)")
        margins_form = QFormLayout(margins_group)
        self.margin_spins: dict[str, QSpinBox] = {}
        for key, label in (
            ("left", "Left"),
            ("right", "Right"),
            ("top", "Top"),
            ("bottom", "Bottom"),
        ):
            spin = QSpinBox()
            spin.setRange(0, 50)
            spin.setSuffix(" mm")
            spin.setSpecialValueText("Default")
            spin.setToolTip(
                "Minimum distance from the paper edge; the content is "
                "scaled to fit inside the margins"
            )
            self.margin_spins[key] = spin
            margins_form.addRow(label, spin)
        self.mirror_check = QCheckBox("Mirror margins (binding)")
        self.mirror_check.setToolTip(
            "For double-sided printing into a binder: even pages get the "
            "left margin on the right, keeping the binding edge clear on "
            "both sides of the sheet"
        )
        margins_form.addRow("", self.mirror_check)
        self.hole_check = QCheckBox("Punch hole guide (18 mm)")
        self.hole_check.setToolTip(
            "Prints a dashed line 18 mm from the binding edge as a "
            "guide for punching holes; alternates sides when margins "
            "are mirrored"
        )
        margins_form.addRow("", self.hole_check)
        side_layout.addWidget(margins_group)
        side_layout.addStretch(1)

        self.last_button = QPushButton("Use last print's settings")
        self.last_button.setEnabled(printing.load_last_job() is not None)
        self.last_button.clicked.connect(self._load_last_settings)
        side_layout.addWidget(self.last_button)

        self.print_button = QPushButton("Print")
        self.print_button.setDefault(True)
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self.do_print)
        side_layout.addWidget(self.print_button)

        side_scroll = QScrollArea(self)
        side_scroll.setWidget(side)
        side_scroll.setWidgetResizable(True)
        side_scroll.setFixedWidth(320)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)

        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.addLayout(viewer_column, stretch=1)
        layout.addWidget(side_scroll)
        self.setCentralWidget(central)

        self._caps_cache: dict[str, dict[str, printing.PPDOption]] = {}
        self.printer_combo.currentTextChanged.connect(self._update_capabilities)

        # debounce option changes before re-running the preview transform
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._apply_layout_preview)
        self.range_edit.textChanged.connect(self._preview_timer.start)
        for combo in (
            self.media_combo,
            self.orientation_combo,
            self.nup_combo,
            self.nup_layout_combo,
            self.pageset_combo,
            self.scaling_combo,
        ):
            combo.currentIndexChanged.connect(self._preview_timer.start)
        self.reverse_check.toggled.connect(self._preview_timer.start)
        for spin in self.margin_spins.values():
            spin.valueChanged.connect(self._preview_timer.start)
        self.mirror_check.toggled.connect(self._preview_timer.start)
        self.hole_check.toggled.connect(self._preview_timer.start)

        # keep the collapsed "more options" button honest about what's set
        for combo in (
            self.media_combo,
            self.orientation_combo,
            self.nup_combo,
            self.nup_layout_combo,
            self.quality_combo,
            self.source_combo,
            self.mediatype_combo,
            self.scaling_combo,
            self.pageset_combo,
        ):
            combo.currentIndexChanged.connect(self._update_more_button)
        self.collate_check.toggled.connect(self._update_more_button)
        self.reverse_check.toggled.connect(self._update_more_button)

        self.setStatusBar(QStatusBar(self))
        self.refresh_printers()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_dialog)
        file_menu.addAction(open_action)

        if self.zotero_storage:
            zotero_action = QAction("Open from &Zotero…", self)
            zotero_action.setShortcut("Ctrl+Shift+O")
            zotero_action.triggered.connect(self.open_zotero_dialog)
            file_menu.addAction(zotero_action)

        print_action = QAction("&Print", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self.do_print)
        file_menu.addAction(print_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu("&View")

        zoom_in_action = QAction("Zoom &In", self)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_action.triggered.connect(self.zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &Out", self)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_action.triggered.connect(self.zoom_out)
        view_menu.addAction(zoom_out_action)

        fit_action = QAction("&Fit Width", self)
        fit_action.setShortcut("Ctrl+0")
        fit_action.triggered.connect(self.fit_width)
        view_menu.addAction(fit_action)

    # ---------- zoom ----------

    def zoom_in(self) -> None:
        self.viewer.zoom_in()

    def zoom_out(self) -> None:
        self.viewer.zoom_out()

    def fit_width(self) -> None:
        self.viewer.fit_width()

    def _on_zoom_changed(self, factor: float) -> None:
        message = "Zoom: fit width" if factor == 0.0 else f"Zoom: {factor:.0%}"
        self.statusBar().showMessage(message, 2000)

    # ---------- preview mirroring of print options ----------

    def _apply_color_preview(self) -> None:
        # also gray when the printer's own default is grayscale (the
        # selected "Grayscale (default)" entry sends no option)
        grayscale = self.color_combo.currentData() == "monochrome" or (
            self.color_combo.currentData() == ""
            and self.color_combo.currentText().startswith("Grayscale")
        )
        if grayscale:
            effect = QGraphicsColorizeEffect(self.viewer)
            # the effect grayscales by luminance, then SCREENS the tint
            # color over it — black is the identity for screen, leaving
            # the true grayscale (a gray tint would wash everything out)
            effect.setColor(QColor(0, 0, 0))
            effect.setStrength(1.0)
            self.viewer.setGraphicsEffect(effect)
        else:
            self.viewer.setGraphicsEffect(None)

    def _load_preserving_view(self, path: str) -> None:
        """Reload the document without jumping back to the first page.

        Keeps the proportional scroll position, which stays meaningful
        even when the transform changes the page count (e.g. n-up).
        """
        vbar = self.viewer.verticalScrollBar()
        hbar = self.viewer.horizontalScrollBar()
        v_frac = vbar.value() / vbar.maximum() if vbar.maximum() else 0.0
        h_frac = hbar.value() / hbar.maximum() if hbar.maximum() else 0.0
        self.document.load(path)

        def restore() -> None:
            vbar.setValue(round(v_frac * vbar.maximum()))
            hbar.setValue(round(h_frac * hbar.maximum()))

        # the view recalculates its scroll range after the load event
        # settles, so restore once the new range is in place
        QTimer.singleShot(0, restore)

    def _apply_layout_preview(self) -> None:
        """Preview the printed layout by running CUPS's pdftopdf filter.

        The transformed document is what the printer would receive, so
        page ranges, odd/even, n-up, orientation, reverse order and
        scaling all show for real. Falls back to a qpdf page subset if
        pdftopdf is unavailable.
        """
        if not self.current_path:
            return
        job = self._current_job()
        # margins are applied after all other transforms, so guide sides
        # follow the displayed page order and parity is always right
        self.viewer.set_margin_guides(
            (
                job.margin_left * printing.MM_TO_PT,
                job.margin_top * printing.MM_TO_PT,
                job.margin_right * printing.MM_TO_PT,
                job.margin_bottom * printing.MM_TO_PT,
            ),
            mirror=job.mirror_margins,
        )
        options = printing.preview_job_options(job)
        has_margins = printing.needs_gs_pass(job)
        if not options and not has_margins:
            if self._showing_transformed:
                self._load_preserving_view(self.current_path)
                self._showing_transformed = False
                self.statusBar().showMessage("Preview: original document", 3000)
            return
        if self._subset_dir is None:
            self._subset_dir = tempfile.TemporaryDirectory(prefix="pdfprinter-")
        preview_path = os.path.join(self._subset_dir.name, "preview.pdf")

        # same order as print_file: layout first, margins on the result
        source = self.current_path
        if options and printing.pdftopdf_available():
            try:
                printing.transform_for_preview(source, options, preview_path)
            except printing.PrintError as exc:
                self.statusBar().showMessage(f"Preview failed: {exc}", 5000)
                return
            source = preview_path

        if has_margins:
            margined_path = os.path.join(self._subset_dir.name, "margined.pdf")
            try:
                printing.apply_margins(source, margined_path, job)
            except printing.PrintError as exc:
                self.statusBar().showMessage(f"Margin preview failed: {exc}", 5000)
                return
            source = margined_path

        if source != self.current_path:
            self._load_preserving_view(source)
            self._showing_transformed = True
            label = options if options else "margins"
            self.statusBar().showMessage(f"Preview: as printed ({label})", 5000)
            return

        # fallback: at least preview the page selection with qpdf
        text = self.range_edit.text().strip()
        if not text or not printing.validate_page_range(text):
            return
        if shutil.which("qpdf") is None:
            self.statusBar().showMessage(
                "pdftopdf/qpdf not available — preview shows the original", 5000
            )
            return
        try:
            printing.make_page_subset(self.current_path, text, preview_path)
        except printing.PrintError as exc:
            self.statusBar().showMessage(f"Page preview failed: {exc}", 5000)
            return
        self._load_preserving_view(preview_path)
        self._showing_transformed = True
        self.statusBar().showMessage(f"Preview: pages {text}", 3000)

    # ---------- actions ----------

    def refresh_printers(self) -> None:
        self._caps_cache.clear()
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        try:
            printers = printing.list_printers()
        except printing.PrintError as exc:
            self.printer_combo.blockSignals(False)
            self.statusBar().showMessage(str(exc))
            return
        self.printer_combo.addItems(printers)
        default = printing.default_printer()
        if default and default in printers:
            self.printer_combo.setCurrentText(default)
        self.printer_combo.blockSignals(False)
        self._update_capabilities()
        self.statusBar().showMessage(f"{len(printers)} printer(s) found", 5000)

    # ---------- more options ----------

    def _toggle_more_options(self, checked: bool) -> None:
        self.more_group.setVisible(checked)
        self._update_more_button()

    def _advanced_active_count(self) -> int:
        """How many advanced options differ from their default."""
        count = 0
        count += bool(self.media_combo.currentData())
        count += bool(self.orientation_combo.currentData())
        count += self.nup_combo.currentData() > 1
        count += bool(self.nup_layout_combo.currentData())
        for combo in (self.quality_combo, self.source_combo, self.mediatype_combo):
            count += (
                combo.isEnabled()
                and combo.count() > 0
                and "(default)" not in combo.currentText()
            )
        count += bool(self.scaling_combo.currentData())
        count += bool(self.pageset_combo.currentData())
        count += self.collate_check.isEnabled() and self.collate_check.isChecked()
        count += self.reverse_check.isChecked()
        return count

    def _update_more_button(self) -> None:
        if self.more_button.isChecked():
            self.more_button.setText("Hide more options")
            return
        active = self._advanced_active_count()
        suffix = f" ({active} set)" if active else ""
        self.more_button.setText(f"Show more options{suffix}")

    # ---------- per-printer capabilities ----------

    def _update_capabilities(self, _text: str | None = None) -> None:
        """Enable/disable and repopulate options for the selected printer."""
        printer = self.printer_combo.currentText()
        options: dict[str, printing.PPDOption] = {}
        if printer:
            if printer not in self._caps_cache:
                try:
                    self._caps_cache[printer] = printing.printer_options(printer)
                except printing.PrintError:
                    self._caps_cache[printer] = {}
            options = self._caps_cache[printer]

        # gray out duplex on printers without a duplex unit
        has_duplex = not options or printing.supports_duplex(options)
        self.duplex_combo.setEnabled(has_duplex)
        self.duplex_combo.setToolTip(
            "" if has_duplex
            else "Not supported by this printer — use odd/even pages "
            "to print double-sided manually"
        )
        if not has_duplex:
            self.duplex_combo.setCurrentIndex(0)

        self._fill_ppd_combo(
            self.quality_combo,
            printing.find_option(
                options, "cupsprintquality", "printquality", "resolution"
            ),
        )
        self._fill_ppd_combo(
            self.source_combo,
            printing.find_option(options, "inputslot", "mediasource"),
        )
        self._fill_ppd_combo(
            self.mediatype_combo, printing.find_option(options, "mediatype")
        )

        # mark the printer's default directly on the matching choice
        size_option = printing.find_option(options, "pagesize")
        self._rebuild_static_combo(
            self.media_combo,
            MEDIA_CHOICES,
            size_option.default if size_option else None,
        )
        color_option = printing.find_option(
            options, "colormodel", "monocolor", "colormode"
        )
        color_default = None
        if color_option is not None and color_option.default:
            name = color_option.default.lower()
            if any(k in name for k in ("gray", "grey", "mono")):
                color_default = "monochrome"
            elif any(k in name for k in ("color", "rgb", "cmyk")):
                color_default = "color"
        self._rebuild_static_combo(self.color_combo, COLOR_CHOICES, color_default)
        self._apply_color_preview()  # rebuild bypassed the change signal
        self._update_more_button()

    @staticmethod
    def _rebuild_static_combo(
        combo: QComboBox,
        choices: list[tuple[str, str]],
        default_value: str | None,
    ) -> None:
        """Rebuild a static combo, folding the printer's default into it.

        The choice matching the printer default becomes "<label> (default)"
        with an empty value (nothing sent, printer decides); the generic
        first entry is dropped. Falls back to the generic entry when the
        default is unknown or not in the list.
        """
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        values = {value for _, value in choices[1:]}
        if default_value in values:
            for label, value in choices[1:]:
                if value == default_value:
                    combo.addItem(f"{label} (default)", "")
                else:
                    combo.addItem(label, value)
            combo.setCurrentIndex(
                next(
                    i
                    for i, (_, v) in enumerate(choices[1:])
                    if v == default_value
                )
            )
        else:
            for label, value in choices:
                combo.addItem(label, value)
        if previous:
            index = combo.findData(previous)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    @staticmethod
    def _fill_ppd_combo(combo: QComboBox, option: printing.PPDOption | None) -> None:
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        if option is None:
            combo.addItem("Printer default", None)
            combo.setEnabled(False)
            combo.setToolTip("Not supported by this printer")
            combo.blockSignals(False)
            return
        combo.setEnabled(True)
        combo.setToolTip(option.label)
        default_index = 0
        for index, choice in enumerate(option.choices):
            if choice == option.default:
                combo.addItem(f"{choice} (default)", (option.keyword, choice))
                default_index = index
            else:
                combo.addItem(choice, (option.keyword, choice))
        # keep the user's pick across printer switches when it exists
        combo.setCurrentIndex(default_index)
        if previous is not None:
            for index in range(combo.count()):
                data = combo.itemData(index)
                if data is not None and data[1] == previous[1]:
                    combo.setCurrentIndex(index)
                    break
        combo.blockSignals(False)

    def open_zotero_dialog(self) -> None:
        # reuse the dialog so the picked collection, search text and
        # scroll position are right where the user left them
        if self._zotero_dialog is None:
            self._zotero_dialog = ZoteroDialog(self.zotero_storage, self)
        dialog = self._zotero_dialog
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_path:
            self.load_pdf(dialog.selected_path)

    def open_dialog(self) -> None:
        # start where the current document lives (also a Zotero folder)
        directory = os.path.dirname(self.current_path) if self.current_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", directory, "PDF files (*.pdf);;All files (*)"
        )
        if path:
            self.load_pdf(path)

    def load_pdf(self, path: str) -> None:
        err = self.document.load(path)
        if err != QPdfDocument.Error.None_:
            QMessageBox.critical(self, "Error", f"Could not open {path}\n({err.name})")
            return
        self.current_path = path
        self._showing_transformed = False
        self.file_label.setText(
            f"{os.path.basename(path)} — {self.document.pageCount()} page(s)"
        )
        self.print_button.setEnabled(True)
        self.statusBar().showMessage(f"Loaded {path}", 5000)
        self._preview_timer.start()  # re-apply any active layout options

    def _current_job(self) -> printing.PrintJob:
        """Collect the current UI state into a PrintJob."""
        extra_options: dict[str, str] = {}
        for combo in (self.quality_combo, self.source_combo, self.mediatype_combo):
            data = combo.currentData()
            if data is not None:
                keyword, choice = data
                extra_options[keyword] = choice
        return printing.PrintJob(
            printer=self.printer_combo.currentText(),
            copies=self.copies_spin.value(),
            page_range=self.range_edit.text().strip(),
            duplex=self.duplex_combo.currentData(),
            color_mode=self.color_combo.currentData(),
            media=self.media_combo.currentData(),
            landscape=self.orientation_combo.currentData(),
            number_up=self.nup_combo.currentData(),
            number_up_layout=self.nup_layout_combo.currentData(),
            collate=self.collate_check.isChecked(),
            page_set=self.pageset_combo.currentData(),
            reverse=self.reverse_check.isChecked(),
            scaling=self.scaling_combo.currentData(),
            margin_left=self.margin_spins["left"].value(),
            margin_right=self.margin_spins["right"].value(),
            margin_top=self.margin_spins["top"].value(),
            margin_bottom=self.margin_spins["bottom"].value(),
            mirror_margins=self.mirror_check.isChecked(),
            hole_guide=self.hole_check.isChecked(),
            extra_options=extra_options,
        )

    def _load_last_settings(self) -> None:
        job = printing.load_last_job()
        if job is None:
            self.last_button.setEnabled(False)
            self.statusBar().showMessage("No saved print settings found", 5000)
            return
        self._apply_job_to_ui(job)
        self.statusBar().showMessage("Loaded last print's settings", 5000)

    def _apply_job_to_ui(self, job: printing.PrintJob) -> None:
        def pick(combo: QComboBox, value) -> None:
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)

        # printer first: it rebuilds the per-printer combos
        if job.printer:
            index = self.printer_combo.findText(job.printer)
            if index >= 0:
                self.printer_combo.setCurrentIndex(index)
        self.copies_spin.setValue(job.copies)
        self.range_edit.setText(job.page_range)
        pick(self.duplex_combo, job.duplex)
        pick(self.color_combo, job.color_mode)
        pick(self.media_combo, job.media)
        pick(self.orientation_combo, job.landscape)
        pick(self.nup_combo, job.number_up)
        pick(self.nup_layout_combo, job.number_up_layout)
        pick(self.scaling_combo, job.scaling)
        pick(self.pageset_combo, job.page_set)
        self.collate_check.setChecked(job.collate)
        self.reverse_check.setChecked(job.reverse)
        for combo in (self.quality_combo, self.source_combo, self.mediatype_combo):
            for index in range(combo.count()):
                data = combo.itemData(index)
                if data is not None and job.extra_options.get(data[0]) == data[1]:
                    combo.setCurrentIndex(index)
                    break
        self.margin_spins["left"].setValue(round(job.margin_left))
        self.margin_spins["right"].setValue(round(job.margin_right))
        self.margin_spins["top"].setValue(round(job.margin_top))
        self.margin_spins["bottom"].setValue(round(job.margin_bottom))
        self.mirror_check.setChecked(job.mirror_margins)
        self.hole_check.setChecked(job.hole_guide)
        self._preview_timer.start()

    def do_print(self) -> None:
        if not self.current_path:
            self.open_dialog()
            if not self.current_path:
                return
        page_range = self.range_edit.text().strip()
        if not printing.validate_page_range(page_range):
            QMessageBox.warning(
                self, "Invalid pages", "Page range must look like: 1-4,7,10-12"
            )
            return
        job = self._current_job()
        try:
            job_id = printing.print_file(self.current_path, job)
        except printing.PrintError as exc:
            QMessageBox.critical(self, "Print failed", str(exc))
            return
        printing.save_last_job(job)
        self.last_button.setEnabled(True)
        self.statusBar().showMessage(f"Sent to printer: {job_id}", 10000)

    # ---------- drag & drop ----------

    def dragEnterEvent(self, event):  # noqa: N802 (Qt naming)
        if any(u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802 (Qt naming)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.load_pdf(path)
                break
