"""Main window: PDF preview on the left, print options on the right."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
)
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsColorizeEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPinchGesture,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from . import printing, theme, zotero
from .dialogs import (
    AboutDialog,
    UserGuideDialog,
    ZoteroPickerDialog,
    show_print_error,
)
from .widgets import (
    CollapsibleSection,
    JobChip,
    PlacementDiagram,
    SegmentedControl,
    Spinner,
)

DUPLEX_CHOICES = [
    ("One-sided", "one-sided"),
    ("Double-sided (long edge)", "two-sided-long-edge"),
    ("Double-sided (short edge)", "two-sided-short-edge"),
]
#: (short label, value, icon, tooltip) for the segmented "Sides" control
DUPLEX_SEGMENTS = [
    ("One", "one-sided", "single", "One-sided"),
    ("Long", "two-sided-long-edge", "duplex", "Double-sided, flipped on the long edge"),
    ("Short", "two-sided-short-edge", "duplex", "Double-sided, flipped on the short edge"),
]
COLOR_CHOICES = [
    ("Printer default", ""),
    ("Color", "color"),
    ("Grayscale", "monochrome"),
]
#: (label, value) for the segmented "Color" control; index 0 is the
#: "let the printer decide" entry whose label carries the driver default
COLOR_SEGMENTS = [("Color", "color"), ("Gray", "monochrome")]
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
ORIENTATION_SEGMENTS = [
    ("Portrait", False, "portrait"),
    ("Landscape", True, "landscape"),
]
NUP_CHOICES = [("1", 1), ("2", 2), ("4", 4), ("6", 6), ("9", 9), ("16", 16)]
NUP_LAYOUT_CHOICES = [
    ("Across, then down", ""),
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
    ("Automatic", ""),
    ("Fit to page", "fit"),
    ("Fill page", "fill"),
    ("No scaling", "none"),
    ("Custom", "custom"),
]
#: caption under the placement diagram, keyed by PrintJob.margin_mode
PLACEMENT_CAPTIONS = {
    "fit": "Shrink to fit the margin box",
    "shift": "Content moves; far edge may clip",
    "hole": (
        "Ink centred between the punch line and the far edge; sides swap "
        "on even pages. Margin values ignored."
    ),
    "hole-clip": "Original size; both edges may clip",
}

DUPLEX_UNSUPPORTED = (
    "Not supported by this printer — use Page set odd/even to print "
    "double-sided manually"
)
RANGE_HINT = "Ranges like 1-4,7 — the preview follows"
RANGE_ERROR = "Must look like 1-4,7,10-12 (or 4- to the end)"
EMPTY_TITLE = "What you see is what prints"
EMPTY_BODY = (
    "Open a PDF to preview it through the real print pipeline — page "
    "ranges, n-up, scaling and binder margins all shown before a sheet "
    "is used."
)
EMPTY_HINT = "…or drop a file anywhere in this window"

ZOOM_STEP = 1.25
MIN_ZOOM = 0.2
MAX_ZOOM = 8.0
SCROLL_MULTIPLIER = 2.0  # touchpad pixel deltas feel too small at 1:1
WHEEL_STEP_PX = 220  # scroll distance per mouse-wheel notch

LABEL_WIDTH = 78  # the sidebar's label column
SIDE_PAD = 16  # sidebar gutter (PAD_SECTION minus room for the scrollbar)
ICON_BUTTON = 30  # square icon-only buttons, CONTROL_HEIGHT tall
SCROLLBAR_ROOM = 8  # width of the always-on sidebar scrollbar


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------


def _repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def _human_size(path: str) -> str:
    try:
        size = float(os.path.getsize(path))
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return ""


def _abbrev_dir(path: str) -> str:
    """The document's directory with $HOME folded back to ``~``."""
    directory = os.path.dirname(os.path.abspath(path))
    home = os.path.expanduser("~")
    if directory == home:
        return "~"
    if directory.startswith(home + os.sep):
        return "~" + directory[len(home) :]
    return directory


def _guide_sample(color: str, width: int = 22, height: int = 10) -> QPixmap:
    """A tiny dashed rule in a guide colour, for the legend bar."""
    dpr = 2.0
    pm = QPixmap(int(width * dpr), int(height * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(theme.guide_pen(color, 2.0))
    painter.drawLine(
        QPointF(0.0, height / 2.0), QPointF(float(width), height / 2.0)
    )
    painter.end()
    return pm


def _solid_sample(
    color: str, alpha: float, width: int = 22, height: int = 10
) -> QPixmap:
    """A flat translucent swatch — the unprintable border reads as fill."""
    dpr = 2.0
    pm = QPixmap(int(width * dpr), int(height * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.qcolor(color, alpha))
    painter.drawRoundedRect(QRectF(0.0, 1.0, width, height - 2.0), 2.0, 2.0)
    painter.end()
    return pm


class _ElidedLabel(QLabel):
    """A QLabel that elides its text instead of forcing the layout wider."""

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._sync()

    def setFullText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self._full = text
        self.setToolTip(text)
        self._sync()

    def fullText(self) -> str:  # noqa: N802 (Qt naming)
        return self._full

    def _sync(self) -> None:
        width = max(60, self.width())
        super().setText(
            QFontMetrics(self.font()).elidedText(self._full, self._mode, width)
        )

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._sync()


class _Chip(QWidget):
    """A pill floating over the preview (binder mode / rendering)."""

    HEIGHT = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        theme.styled_panel(self)
        self.setObjectName("previewChip")
        self.setFixedHeight(self.HEIGHT)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 0, 13, 0)
        self._layout.setSpacing(theme.GAP_ICON + 2)
        self.label = QLabel(self)
        self.label.setFont(theme.body_font(theme.SMALL_POINT_SIZE))
        self.set_accent(False)

    def set_accent(self, accent: bool) -> None:
        border = theme.ACCENT if accent else theme.HAIRLINE_SOLID
        fill = theme.ACCENT_TINT if accent else theme.CONTROL_FILL
        ink = theme.ACCENT_TEXT if accent else theme.INK
        self.setStyleSheet(
            f"QWidget#previewChip {{ background-color: {fill};"
            f" border: 1px solid {border};"
            f" border-radius: {self.HEIGHT // 2}px; }}"
        )
        self.label.setStyleSheet(f"color: {ink};")


class _GuideOverlay(QWidget):
    """Screen-only guides painted over — never inside — the viewport.

    The guides are furniture, not content: the grayscale *print* preview
    (a QGraphicsColorizeEffect on the viewport) must not touch them. So
    this widget is a sibling of the viewport, parented to the view, with
    its geometry kept in sync; it is transparent and lets every mouse
    event through to the view underneath.
    """

    def __init__(self, view: ZoomablePdfView) -> None:
        super().__init__(view)
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._caption_font = theme.body_font(theme.SMALL_POINT_SIZE)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        view = self._view
        if (
            view._margin_guides is None
            and view._hole_guide_pt is None
            and view._hw_margins is None
            and not view._mirror_guides
        ):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        margin_pen = theme.guide_pen(theme.MARGIN_GUIDE)
        hole_pen = theme.guide_pen(theme.PUNCH_GUIDE)
        hw_brush = theme.qcolor(theme.UNPRINTABLE, theme.UNPRINTABLE_ALPHA)
        caption_color = theme.qcolor(theme.TEXT_FAINT)
        own_rect = QRectF(self.rect())
        for index, (rect, scale) in enumerate(view._page_layout()):
            if not rect.intersects(own_rect):
                continue
            if view._hw_margins is not None:
                # shade what the printer physically cannot print
                hw_l, hw_b, hw_r, hw_t = (m * scale for m in view._hw_margins)
                painter.fillRect(
                    QRectF(rect.left(), rect.top(), hw_l, rect.height()),
                    hw_brush,
                )
                painter.fillRect(
                    QRectF(rect.right() - hw_r, rect.top(), hw_r, rect.height()),
                    hw_brush,
                )
                painter.fillRect(
                    QRectF(rect.left() + hw_l, rect.top(),
                           rect.width() - hw_l - hw_r, hw_t),
                    hw_brush,
                )
                painter.fillRect(
                    QRectF(rect.left() + hw_l, rect.bottom() - hw_b,
                           rect.width() - hw_l - hw_r, hw_b),
                    hw_brush,
                )
            even = view._mirror_guides and index % 2 == 1  # even page number
            if view._margin_guides is not None:
                left, top, right, bottom = view._margin_guides
                if even:
                    left, right = right, left
                painter.setPen(margin_pen)
                painter.drawRect(
                    rect.adjusted(
                        left * scale, top * scale, -right * scale, -bottom * scale
                    )
                )
            if view._hole_guide_pt is not None:
                offset = view._hole_guide_pt * scale
                x = rect.right() - offset if even else rect.left() + offset
                painter.setPen(hole_pen)
                painter.drawLine(
                    QPointF(x, rect.top()), QPointF(x, rect.bottom())
                )
            if view._mirror_guides:
                self._draw_caption(painter, rect, index, even, caption_color)
        painter.end()

    def _draw_caption(
        self,
        painter: QPainter,
        rect: QRectF,
        index: int,
        even: bool,
        color: QColor,
    ) -> None:
        """Bottom-corner note telling which edge binds on this sheet."""
        text = (
            f"{index + 1} · {'even' if even else 'odd'} · "
            f"binding {'right' if even else 'left'}"
        )
        painter.setPen(color)
        painter.setFont(self._caption_font)
        metrics = QFontMetrics(self._caption_font)
        width = metrics.horizontalAdvance(text) + 4
        height = metrics.height()
        y = rect.bottom() - 6 - height
        x = rect.right() - 10 - width if even else rect.left() + 10
        painter.drawText(
            QRectF(x, y, width, height),
            int(Qt.AlignmentFlag.AlignVCenter)
            | int(
                Qt.AlignmentFlag.AlignRight
                if even
                else Qt.AlignmentFlag.AlignLeft
            ),
            text,
        )


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

        # the grey behind the paper: QPdfView fills the viewport with the
        # palette's Dark brush. Never touch setBackgroundRole() here — a
        # Dark background role makes every child label inherit the Light
        # foreground role, which paints the floating chips invisible.
        palette = self.palette()
        for role in (
            QPalette.ColorRole.Dark,
            QPalette.ColorRole.Mid,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Window,
        ):
            palette.setColor(role, theme.qcolor(theme.CANVAS))
        self.setPalette(palette)
        self.viewport().setPalette(palette)

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
        # punch-hole guide distance from the binding edge, in points;
        # a screen-only overlay, never part of the printed output
        self._hole_guide_pt: float | None = None
        # printer's unprintable border (left, bottom, right, top) in pt
        self._hw_margins: tuple[float, float, float, float] | None = None

        # the guides live in their own transparent layer above the
        # viewport, so the grayscale print simulation (an effect on the
        # viewport) cannot desaturate them
        self._guide_overlay = _GuideOverlay(self)
        self._sync_guide_overlay()
        self._guide_overlay.show()
        self.viewport().installEventFilter(self)
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.valueChanged.connect(self.update_guides)
            bar.rangeChanged.connect(self.update_guides)
        self.zoom_changed.connect(self.update_guides)

    # ---------- margin guides ----------

    def update_guides(self, *_args) -> None:
        """Repaint the guide layer (page geometry or state changed)."""
        self._guide_overlay.update()

    def _sync_guide_overlay(self) -> None:
        """Keep the guide layer exactly over the viewport."""
        self._guide_overlay.setGeometry(self.viewport().geometry())

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if watched is self.viewport() and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Move,
        ):
            self._sync_guide_overlay()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._sync_guide_overlay()

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
        self.update_guides()

    def set_hole_guide(self, distance_pt: float | None) -> None:
        self._hole_guide_pt = distance_pt
        self.update_guides()

    def set_hw_margins(
        self, margins: tuple[float, float, float, float] | None
    ) -> None:
        """Shade the printer's unprintable border on every page."""
        if margins is not None and not any(margins):
            margins = None
        self._hw_margins = margins
        self.update_guides()

    def _page_layout(self) -> list[tuple[QRectF, float]]:
        """Each page's viewport rectangle and its pixels-per-point scale.

        Mirrors QPdfView's layout: in fit-to-width mode every page is
        scaled INDIVIDUALLY to fill the viewport width (page sizes may
        differ), while custom zoom uses one uniform factor.
        """
        doc = self.document()
        if doc is None or doc.pageCount() == 0:
            return []
        margins = self.documentMargins()
        spacing = self.pageSpacing()
        custom = self.zoomMode() == QPdfView.ZoomMode.Custom
        uniform = self.zoomFactor() * self.logicalDpiX() / 72.0
        available = max(
            self.viewport().width() - margins.left() - margins.right(), 1
        )
        if custom:
            widest = max(
                doc.pagePointSize(p).width() for p in range(doc.pageCount())
            )
            content_w = max(
                self.viewport().width(),
                widest * uniform + margins.left() + margins.right(),
            )
        offset_x = self.horizontalScrollBar().value()
        offset_y = self.verticalScrollBar().value()
        layout = []
        y = float(margins.top())
        for page in range(doc.pageCount()):
            size = doc.pagePointSize(page)
            scale = uniform if custom else available / max(size.width(), 1)
            w, h = size.width() * scale, size.height() * scale
            x = (content_w - w) / 2 if custom else float(margins.left())
            layout.append((QRectF(x - offset_x, y - offset_y, w, h), scale))
            y += h + spacing
        return layout

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


class SuffixSpinBox(QSpinBox):
    """Spin box whose unit suffix is not editable ground.

    Any click on the field (frame or text) selects the entire text so
    typing replaces the value. A bare caret can never sit inside the
    unit suffix; step arrows appear only while the cursor hovers the
    field.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clamping = False
        self.lineEdit().cursorPositionChanged.connect(self._clamp_cursor)
        self.lineEdit().installEventFilter(self)
        # plain field at rest; step arrows appear under the cursor
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        # no blinking caret while the whole value is selected
        self.lineEdit().setProperty("hideCaretWhenSelected", True)
        self.lineEdit().selectionChanged.connect(self.lineEdit().update)

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        super().enterEvent(event)
        if self.isEnabled():
            self.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        super().leaveEvent(event)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)

    def _cursor_limit(self) -> int:
        text = self.lineEdit().text()
        suffix = self.suffix()
        if suffix and text.endswith(suffix) and (
            not self.specialValueText() or self.value() != self.minimum()
        ):
            return len(text) - len(suffix)
        return len(text)

    def _clamp_cursor(self, _old: int, new: int) -> None:
        if self._clamping:
            return
        edit = self.lineEdit()
        if edit.hasSelectedText():
            return  # selections may span the suffix (select-all)
        limit = self._cursor_limit()
        if new > limit:
            self._clamping = True
            try:
                edit.setCursorPosition(limit)
            finally:
                self._clamping = False

    def _clamp_now(self) -> None:
        edit = self.lineEdit()
        self._clamp_cursor(edit.cursorPosition(), edit.cursorPosition())

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        # clicks and focus land on the child line edit, not on the spin
        # box itself, so watch them there
        if obj is self.lineEdit() and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
        ):
            QTimer.singleShot(0, self.selectAll)
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        # clicks on the frame around the line edit select too
        super().mousePressEvent(event)
        QTimer.singleShot(0, self.selectAll)

    def stepBy(self, steps: int) -> None:  # noqa: N802 (Qt naming)
        super().stepBy(steps)
        QTimer.singleShot(0, self._clamp_now)

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        # these fields show no step arrows at rest, so wheel-stepping
        # would be an invisible affordance; the wheel scrolls the pane
        event.ignore()


class _TransformWorker(QThread):
    """Runs the preview transform pipeline off the GUI thread."""

    done = pyqtSignal(object, object)  # (result path | None, error | None)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task

    def run(self):  # noqa: A003 (Qt naming)
        try:
            self.done.emit(self._task(), None)
        except printing.PrintError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 — an exception escaping a
            # QThread aborts the whole process, so nothing may get through
            self.done.emit(None, f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self, pdf_path: str | None = None):
        super().__init__()
        self.setWindowTitle("PDF Printer")
        self.resize(1180, 780)
        self.setAcceptDrops(True)

        self.document = QPdfDocument(self)
        self.current_path: str | None = None
        self._source_pages = 0  # page count of the ORIGINAL document
        self._subset_dir: tempfile.TemporaryDirectory | None = None
        self._showing_transformed = False
        self._zotero_dialog: ZoteroPickerDialog | None = None
        self._preview_worker: _TransformWorker | None = None
        self._preview_dirty = False
        self._printer_restored = False
        self._print_worker: _TransformWorker | None = None
        self._print_stage = "idle"
        self._color_default: str | None = None
        self._color_user_set = False
        self._printer_hw: tuple[float, float, float, float] | None = None
        self._tracked_job: str | None = None
        self._track_polls = 0
        self._job_timer = QTimer(self)
        self._job_timer.setInterval(2000)
        self._job_timer.timeout.connect(self._poll_job)
        self._chip_timer = QTimer(self)
        self._chip_timer.setSingleShot(True)
        self._chip_timer.timeout.connect(lambda: self.job_chip.hide())

        self._build_ui()
        self._build_menu()

        if pdf_path:
            self.load_pdf(pdf_path)

    # ---------- UI construction: shared pieces ----------

    @staticmethod
    def _section_header(text: str, rule: bool = True) -> QWidget:
        """A SECTION LABEL, optionally trailed by a hairline rule."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.GAP_CONTROL)
        layout.addWidget(theme.style_section_label(QLabel(text)), 0)
        if rule:
            line = QWidget()
            line.setObjectName("chipSep")
            theme.styled_panel(line)
            line.setFixedHeight(1)
            line.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            layout.addWidget(line, 1, Qt.AlignmentFlag.AlignVCenter)
        else:
            layout.addStretch(1)
        return widget

    @staticmethod
    def _combo(tooltip: str = "", chars: int = 6) -> QComboBox:
        """A sidebar combo that may shrink below its longest entry."""
        combo = QComboBox()
        combo.setMinimumHeight(theme.CONTROL_HEIGHT)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(chars)
        combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        if tooltip:
            combo.setToolTip(tooltip)
        return combo

    @staticmethod
    def _icon_button(
        name: str, tooltip: str, size: int = ICON_BUTTON
    ) -> QPushButton:
        button = QPushButton()
        button.setIcon(theme.icon(name, theme.INK, 15))
        button.setIconSize(QSize(15, 15))
        button.setFixedSize(size, size)
        button.setToolTip(tooltip)
        button.setStyleSheet("padding: 0px;")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _row(
        self, text: str, *widgets, label_width: int = LABEL_WIDTH
    ) -> tuple[QHBoxLayout, QLabel]:
        """One ``Label   [control…]`` line of the sidebar."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.GAP_CONTROL)
        label = QLabel(text)
        label.setFont(theme.body_font())
        label.setFixedWidth(label_width)
        row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        for widget, stretch in widgets:
            row.addWidget(widget, stretch, Qt.AlignmentFlag.AlignVCenter)
        return row, label

    # ---------- UI construction ----------

    def _build_ui(self) -> None:
        self.zotero_storage = zotero.find_storage_dir()

        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_preview_column(), 1)
        body.addWidget(self._build_sidebar(), 0)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)

        self._build_status_bar()
        self._connect_signals()
        self._update_header()
        self._update_placement_diagram()
        self._set_print_stage("idle")
        self.refresh_printers()
        self._refresh_last_tooltip()

    # ----- header bar -----

    def _build_header(self) -> QWidget:
        bar = theme.styled_panel(QWidget(self))
        bar.setObjectName("headerBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.PAD_SECTION, 10, theme.PAD_SECTION, 10)
        layout.setSpacing(theme.GAP_CONTROL + 3)

        title = QLabel("PDF Printer")
        title.setFont(theme.serif_font(16.0))
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFixedHeight(28)
        layout.addWidget(separator, 0, Qt.AlignmentFlag.AlignVCenter)

        self.header_icon = QLabel()
        self.header_icon.setPixmap(theme.pixmap("doc", theme.TEXT_MUTED, 16))
        self.header_icon.setFixedWidth(16)
        layout.addWidget(self.header_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        self.header_name = _ElidedLabel("No document")
        self.header_name.setFont(theme.body_font())
        self.header_meta = _ElidedLabel("", Qt.TextElideMode.ElideMiddle)
        self.header_meta.setObjectName("metaLabel")
        self.header_meta.setFont(theme.body_font(theme.SMALL_POINT_SIZE))
        column.addWidget(self.header_name)
        column.addWidget(self.header_meta)
        layout.addLayout(column, 1)

        self.open_button = QPushButton(" Open PDF…")
        self.open_button.setIcon(theme.icon("open", theme.INK, 15))
        self.open_button.setIconSize(QSize(15, 15))
        self.open_button.setMinimumHeight(theme.CONTROL_HEIGHT)
        self.open_button.clicked.connect(self.open_dialog)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.zotero_button = QPushButton(" Zotero")
        self.zotero_button.setIcon(theme.icon("zotero", theme.INK, 15))
        self.zotero_button.setIconSize(QSize(15, 15))
        self.zotero_button.setMinimumHeight(theme.CONTROL_HEIGHT)
        self.zotero_button.setToolTip("Open a PDF from the Zotero library")
        self.zotero_button.clicked.connect(self.open_zotero_dialog)
        self.zotero_button.setVisible(bool(self.zotero_storage))
        layout.addWidget(self.zotero_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    # ----- preview column -----

    def _build_preview_column(self) -> QWidget:
        column = QWidget(self)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.viewer = ZoomablePdfView(self)
        self.viewer.setDocument(self.document)
        self.viewer.setPageMode(QPdfView.PageMode.MultiPage)
        self.viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.viewer.setFrameShape(QFrame.Shape.NoFrame)
        self.viewer.zoom_changed.connect(self._on_zoom_changed)

        self.preview_stack = QStackedWidget(self)
        self.preview_stack.addWidget(self._build_empty_state())
        self.preview_stack.addWidget(self.viewer)
        layout.addWidget(self.preview_stack, 1)

        self._build_overlays()
        self.viewer.installEventFilter(self)

        self.legend_bar = self._build_legend()
        self.legend_bar.setVisible(False)
        layout.addWidget(self.legend_bar, 0)
        return column

    def _build_empty_state(self) -> QWidget:
        page = theme.styled_panel(QWidget(self))
        page.setObjectName("emptyPage")
        page.setStyleSheet(
            f"QWidget#emptyPage {{ background-color: {theme.CANVAS}; }}"
        )
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 24, 24, 24)

        card = QFrame()
        card.setObjectName("emptyCard")
        card.setMaximumWidth(520)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(40, 38, 40, 34)
        layout.setSpacing(0)

        mark = QLabel()
        mark.setPixmap(theme.pixmap("doc-plus", theme.ACCENT, 34))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(mark)
        layout.addSpacing(18)

        title = QLabel(EMPTY_TITLE)
        title.setFont(theme.serif_font(22.0))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addSpacing(14)

        body = QLabel(EMPTY_BODY)
        body.setFont(theme.body_font())
        body.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addSpacing(24)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.GAP_CONTROL + 3)
        buttons.addStretch(1)
        open_button = QPushButton(" Open PDF…")
        open_button.setIcon(theme.icon("open", theme.ACCENT_TEXT, 15))
        open_button.setIconSize(QSize(15, 15))
        open_button.setMinimumHeight(34)
        open_button.setStyleSheet(
            f"QPushButton {{ border: 1px solid {theme.ACCENT};"
            f" color: {theme.ACCENT_TEXT}; padding: 0px 18px; }}"
        )
        open_button.clicked.connect(self.open_dialog)
        buttons.addWidget(open_button)
        self.empty_zotero_button = QPushButton(" Open from Zotero…")
        self.empty_zotero_button.setIcon(theme.icon("zotero", theme.INK, 15))
        self.empty_zotero_button.setIconSize(QSize(15, 15))
        self.empty_zotero_button.setMinimumHeight(34)
        self.empty_zotero_button.setStyleSheet("padding: 0px 18px;")
        self.empty_zotero_button.clicked.connect(self.open_zotero_dialog)
        self.empty_zotero_button.setVisible(bool(self.zotero_storage))
        buttons.addWidget(self.empty_zotero_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addSpacing(20)

        hint = QLabel(EMPTY_HINT)
        hint_font = theme.body_font(theme.SMALL_POINT_SIZE)
        hint_font.setItalic(True)
        hint.setFont(hint_font)
        hint.setStyleSheet(f"color: {theme.TEXT_FAINT};")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        return page

    def _build_overlays(self) -> None:
        """The zoom pill and the two chips floating over the preview."""
        pill = QFrame(self.viewer)
        pill.setObjectName("pillToolbar")
        pill.setFixedHeight(38)  # == 2 × the stylesheet's 19 px radius
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        zoom_out = QPushButton()
        zoom_out.setIcon(theme.icon("zoom-out", theme.INK, 14))
        zoom_out.setIconSize(QSize(14, 14))
        zoom_out.setToolTip("Zoom out (Ctrl+-, Ctrl+scroll)")
        zoom_out.clicked.connect(self.zoom_out)
        layout.addWidget(zoom_out)

        self.zoom_pill_label = QLabel("Fit")
        self.zoom_pill_label.setFont(theme.body_font(tabular=True))
        self.zoom_pill_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_pill_label.setMinimumWidth(46)
        layout.addWidget(self.zoom_pill_label)

        zoom_in = QPushButton()
        zoom_in.setIcon(theme.icon("zoom-in", theme.INK, 14))
        zoom_in.setIconSize(QSize(14, 14))
        zoom_in.setToolTip("Zoom in (Ctrl++, Ctrl+scroll)")
        zoom_in.clicked.connect(self.zoom_in)
        layout.addWidget(zoom_in)

        separator = QWidget()
        separator.setObjectName("chipSep")
        theme.styled_panel(separator)
        separator.setFixedWidth(1)
        separator.setFixedHeight(18)
        layout.addWidget(separator)

        fit_button = QPushButton(" Fit width")
        fit_button.setIcon(theme.icon("fit-width", theme.ACCENT_TEXT, 14))
        fit_button.setIconSize(QSize(14, 14))
        fit_button.setToolTip("Fit page to window width (Ctrl+0)")
        fit_button.setStyleSheet(f"color: {theme.ACCENT_TEXT};")
        fit_button.clicked.connect(self.fit_width)
        layout.addWidget(fit_button)
        self.zoom_pill = pill

        self.binder_chip = _Chip(self.viewer)
        self.binder_chip.icon_label = QLabel(self.binder_chip)
        self.binder_chip.icon_label.setPixmap(
            theme.pixmap("mirror", theme.ACCENT_TEXT, 14)
        )
        self.binder_chip._layout.addWidget(self.binder_chip.icon_label)
        self.binder_chip._layout.addWidget(self.binder_chip.label)
        self.binder_chip.set_accent(True)
        self.binder_chip.hide()

        self.render_chip = _Chip(self.viewer)
        self.render_chip.spinner = Spinner(13, theme.TEXT_MUTED, self.render_chip)
        self.render_chip._layout.addWidget(self.render_chip.spinner)
        self.render_chip._layout.addWidget(self.render_chip.label)
        self.render_chip.label.setText("Rendering preview…")
        self.render_chip.hide()

    def _build_legend(self) -> QWidget:
        bar = theme.styled_panel(QWidget(self))
        bar.setObjectName("legendBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.PAD_SECTION, 7, theme.PAD_SECTION, 7)
        layout.setSpacing(theme.GAP_CONTROL + 9)
        layout.addWidget(
            theme.style_section_label(QLabel("SCREEN GUIDES")),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        def item(pix: QPixmap) -> tuple[QWidget, QLabel]:
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme.GAP_ICON + 2)
            mark = QLabel()
            mark.setPixmap(pix)
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
            text = QLabel()
            text.setFont(theme.body_font(theme.SMALL_POINT_SIZE, tabular=True))
            row.addWidget(text, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
            return widget, text

        self.legend_margins, self.legend_margins_text = item(
            _guide_sample(theme.MARGIN_GUIDE)
        )
        self.legend_punch, self.legend_punch_text = item(
            _guide_sample(theme.PUNCH_GUIDE)
        )
        self.legend_hw, self.legend_hw_text = item(
            _solid_sample(theme.UNPRINTABLE, 0.45)
        )
        layout.addStretch(1)

        never = QLabel("Never printed")
        never_font = theme.body_font(theme.SMALL_POINT_SIZE)
        never_font.setItalic(True)
        never.setFont(never_font)
        never.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(never, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    # ----- sidebar -----

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(
            SIDE_PAD, theme.PAD_SECTION, SIDE_PAD, theme.PAD_SECTION
        )
        side_layout.setSpacing(theme.GAP_CONTROL)

        self._build_print_section(side_layout)
        side_layout.addSpacing(theme.GAP_GROUP - theme.GAP_CONTROL)
        self._build_more_section(side_layout)
        side_layout.addSpacing(theme.GAP_GROUP - theme.GAP_CONTROL)
        self._build_margins_section(side_layout)
        side_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        # the scrollbar is always reserved so the viewport width never
        # changes: no control reflow when it appears, and the content
        # always fits exactly (no sideways panning)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        column = theme.styled_panel(QWidget(self))
        column.setObjectName("sidebar")
        column.setStyleSheet(
            f"QWidget#sidebar {{ background-color: {theme.GROUND};"
            f" border-left: 1px solid {theme.HAIRLINE_SOFT}; }}"
        )
        column.setFixedWidth(theme.SIDEBAR_WIDTH)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll, 1)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(rule)
        layout.addWidget(self._build_actions())
        return column

    def _build_print_section(self, side_layout: QVBoxLayout) -> None:
        side_layout.addWidget(self._section_header("PRINT", rule=False))

        # this section's labels are all short; a column sized for the
        # longest of them keeps the controls close instead of inheriting
        # the wider column the other sections need
        metrics = QFontMetrics(theme.body_font())
        print_label_w = (
            max(
                metrics.horizontalAdvance(t)
                for t in ("Printer", "Copies", "Pages", "Sides", "Color")
            )
            + 4
        )

        self.printer_combo = self._combo("The CUPS queue the job goes to", 8)
        refresh_button = self._icon_button("refresh", "Refresh the printer list")
        refresh_button.clicked.connect(self.refresh_printers)
        row, _ = self._row(
            "Printer", (self.printer_combo, 1), (refresh_button, 0),
            label_width=print_label_w,
        )
        side_layout.addLayout(row)

        self.copies_spin = QSpinBox()
        self.copies_spin.setToolTip("Number of copies to print")
        self.copies_spin.setRange(1, 999)
        self.copies_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.copies_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.copies_spin.setFixedWidth(60)
        self.copies_spin.setFixedHeight(theme.CONTROL_HEIGHT)
        self.copies_spin.setStyleSheet(
            "QSpinBox { border-radius: 0px; padding: 0px 4px; }"
        )
        minus = QPushButton()
        minus.setObjectName("segment")
        minus.setProperty("edge", "first")
        minus.setIcon(theme.icon("zoom-out", theme.INK, 13))
        minus.setIconSize(QSize(13, 13))
        minus.setFixedSize(ICON_BUTTON, theme.CONTROL_HEIGHT)
        minus.setToolTip("One copy fewer")
        minus.clicked.connect(self.copies_spin.stepDown)
        plus = QPushButton()
        plus.setObjectName("segment")
        plus.setProperty("edge", "last")
        plus.setIcon(theme.icon("zoom-in", theme.INK, 13))
        plus.setIconSize(QSize(13, 13))
        plus.setFixedSize(ICON_BUTTON, theme.CONTROL_HEIGHT)
        plus.setToolTip("One copy more")
        plus.clicked.connect(self.copies_spin.stepUp)
        stepper = QWidget()
        stepper_layout = QHBoxLayout(stepper)
        stepper_layout.setContentsMargins(0, 0, 0, 0)
        stepper_layout.setSpacing(-1)  # the three parts share their border
        stepper_layout.addWidget(minus)
        stepper_layout.addWidget(self.copies_spin)
        stepper_layout.addWidget(plus)
        stepper.setFixedWidth(ICON_BUTTON * 2 + 60 - 2)
        row, _ = self._row("Copies", (stepper, 0), label_width=print_label_w)
        row.addStretch(1)
        side_layout.addLayout(row)

        self.range_edit = QLineEdit()
        self.range_edit.setToolTip(
            "Pages to print, e.g. 1-4,7 — the preview shows the selection"
        )
        self.range_edit.setPlaceholderText("All pages")
        self.range_edit.setMinimumHeight(theme.CONTROL_HEIGHT)
        self.range_edit.setFont(theme.body_font(tabular=True))
        row, _ = self._row("Pages", (self.range_edit, 1), label_width=print_label_w)
        side_layout.addLayout(row)

        self.range_hint = theme.style_hint(QLabel(RANGE_HINT))
        self.range_hint.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.range_error = QWidget()
        error_row = QHBoxLayout(self.range_error)
        error_row.setContentsMargins(0, 0, 0, 0)
        error_row.setSpacing(theme.GAP_ICON + 1)
        error_mark = QLabel()
        error_mark.setPixmap(theme.pixmap("alert", theme.ERROR, 13))
        error_row.addWidget(error_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        error_row.addWidget(
            theme.style_error(QLabel(RANGE_ERROR)), 0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        error_row.addStretch(1)
        self.range_error.setVisible(False)
        hint_holder = QHBoxLayout()
        hint_holder.setContentsMargins(0, 0, 0, 0)
        hint_holder.setSpacing(0)
        hint_holder.addSpacing(print_label_w + theme.GAP_CONTROL)
        hint_holder.addWidget(self.range_hint, 1)
        hint_holder.addWidget(self.range_error, 1)
        side_layout.addLayout(hint_holder)

        self.duplex_combo = SegmentedControl()
        # the sidebar is narrow: trim the segment padding so three
        # segments plus the label column still fit without clipping
        self.duplex_combo.setStyleSheet(
            "QPushButton#segment { padding: 0px 7px; }"
        )
        for label, value, icon_name, tip in DUPLEX_SEGMENTS:
            button = self.duplex_combo.addItem(
                label, value, theme.icon(icon_name, theme.INK, 14)
            )
            button.setToolTip(tip)
        self.duplex_info = QLabel()
        self.duplex_info.setPixmap(theme.pixmap("info", theme.TEXT_MUTED, 15))
        self.duplex_info.setFixedWidth(15)
        self.duplex_info.setVisible(False)
        row, self.duplex_label = self._row(
            "Sides", (self.duplex_combo, 1), (self.duplex_info, 0),
            label_width=print_label_w,
        )
        side_layout.addLayout(row)

        self.color_combo = SegmentedControl()
        self.color_combo.setStyleSheet(
            "QPushButton#segment { padding: 0px 6px; }"
        )
        for label, value in COLOR_SEGMENTS:
            self.color_combo.addItem(label, value)
        for idx in range(self.color_combo.count()):
            self.color_combo.button(idx).clicked.connect(
                self._mark_color_user_set
            )
        self.color_combo.setToolTip(
            "Color or grayscale output; grayscale is previewed too"
        )
        row, _ = self._row("Color", (self.color_combo, 1), label_width=print_label_w)
        side_layout.addLayout(row)

    def _build_more_section(self, side_layout: QVBoxLayout) -> None:
        self.more_group = QWidget()
        form = QVBoxLayout(self.more_group)
        form.setContentsMargins(0, theme.GAP_ICON, 0, 0)
        form.setSpacing(theme.GAP_CONTROL)

        self.media_combo = self._combo("Paper size sent to the printer")
        for label, value in MEDIA_CHOICES:
            self.media_combo.addItem(label, value)
        row, _ = self._row("Paper", (self.media_combo, 1))
        form.addLayout(row)

        self.orientation_combo = SegmentedControl()
        self.orientation_combo.setStyleSheet(
            "QPushButton#segment { padding: 0px 7px; }"
        )
        for label, value, icon_name in ORIENTATION_SEGMENTS:
            self.orientation_combo.addItem(
                label, value, theme.icon(icon_name, theme.INK, 14)
            )
        self.orientation_combo.setToolTip("Rotate the layout to landscape")
        row, _ = self._row("Orientation", (self.orientation_combo, 1))
        form.addLayout(row)

        self.nup_combo = self._combo("Print several pages on each sheet", 2)
        for label, value in NUP_CHOICES:
            self.nup_combo.addItem(label, value)
        self.nup_layout_combo = self._combo(
            "Order in which pages fill the sheet (pages/sheet > 1)", 8
        )
        for label, value in NUP_LAYOUT_CHOICES:
            self.nup_layout_combo.addItem(label, value)
        self.nup_layout_combo.setEnabled(False)
        self.nup_combo.currentIndexChanged.connect(
            lambda: self.nup_layout_combo.setEnabled(
                self.nup_combo.currentData() > 1
            )
        )
        self.nup_combo.setFixedWidth(66)
        row, _ = self._row(
            "Pages/sheet", (self.nup_combo, 0), (self.nup_layout_combo, 1)
        )
        form.addLayout(row)

        # per-printer options, filled by _update_capabilities
        self.quality_combo = self._combo()
        row, _ = self._row("Quality", (self.quality_combo, 1))
        form.addLayout(row)
        self.source_combo = self._combo()
        row, _ = self._row("Paper source", (self.source_combo, 1))
        form.addLayout(row)
        self.mediatype_combo = self._combo()
        row, _ = self._row("Media type", (self.mediatype_combo, 1))
        form.addLayout(row)

        self.scaling_combo = self._combo(
            "How content is scaled to the paper; Custom enables the\n"
            "percentage beside it"
        )
        for label, value in SCALING_CHOICES:
            self.scaling_combo.addItem(label, value)
        self.scale_spin = SuffixSpinBox()
        self.scale_spin.setRange(25, 400)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setValue(100)
        self.scale_spin.setEnabled(False)  # only with Scaling = Custom
        self.scale_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.scale_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.scale_spin.setFixedWidth(74)
        self.scale_spin.setMinimumHeight(theme.CONTROL_HEIGHT)
        self.scale_spin.setFont(theme.body_font(tabular=True))
        self.scale_spin.setToolTip(
            "Content scale for Scaling = Custom, centered on the page "
            "(and inside the margins when set)"
        )
        self.scaling_combo.currentIndexChanged.connect(
            lambda: self.scale_spin.setEnabled(
                self.scaling_combo.currentData() == "custom"
            )
        )
        row, _ = self._row("Scaling", (self.scaling_combo, 1), (self.scale_spin, 0))
        form.addLayout(row)

        self.pageset_combo = self._combo(
            "Print only odd or even pages — for manual double-sided\n"
            "printing: print odd, re-feed the stack, print even"
        )
        for label, value in PAGE_SET_CHOICES:
            self.pageset_combo.addItem(label, value)
        row, _ = self._row("Page set", (self.pageset_combo, 1))
        form.addLayout(row)

        self.collate_check = QCheckBox("Collate copies")
        self.collate_check.setFont(theme.body_font())
        self.collate_check.setToolTip(
            "Print complete sets (1,2,3 / 1,2,3) instead of page groups"
        )
        self.collate_check.setEnabled(False)  # only meaningful for copies > 1
        self.copies_spin.valueChanged.connect(
            lambda v: self.collate_check.setEnabled(v > 1)
        )
        self.reverse_check = QCheckBox("Reverse order")
        self.reverse_check.setFont(theme.body_font())
        self.reverse_check.setToolTip("Print the last page first")
        for check in (self.collate_check, self.reverse_check):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addSpacing(LABEL_WIDTH + theme.GAP_CONTROL)
            row.addWidget(check, 1)
            form.addLayout(row)

        self.more_section = CollapsibleSection("MORE OPTIONS")
        self.more_section.setContentWidget(self.more_group)
        self.more_section.toggled.connect(self._toggle_more_options)
        #: kept for callers that used the old push button
        self.more_button = self.more_section.header
        side_layout.addWidget(self.more_section)

    def _build_margins_section(self, side_layout: QVBoxLayout) -> None:
        side_layout.addWidget(self._section_header("MARGINS & BINDING"))

        fields = QVBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(theme.GAP_CONTROL)
        self.margin_spins: dict[str, QSpinBox] = {}
        for key, label in (
            ("left", "Left"),
            ("right", "Right"),
            ("top", "Top"),
            ("bottom", "Bottom"),
        ):
            spin = SuffixSpinBox()
            spin.setRange(0, 50)
            spin.setSuffix(" mm")
            spin.setSpecialValueText("Default")
            spin.setMinimumHeight(theme.CONTROL_HEIGHT)
            # the artboards show plain fields; arrows/wheel still work
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setFont(theme.body_font(tabular=True))
            spin.setToolTip(
                "Minimum distance from the paper edge; the content is "
                "scaled to fit inside the margins"
            )
            self.margin_spins[key] = spin
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme.GAP_CONTROL)
            text = QLabel(label)
            text.setFont(theme.body_font())
            text.setFixedWidth(52)
            row.addWidget(text, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(spin, 1, Qt.AlignmentFlag.AlignVCenter)
            fields.addLayout(row)
        fields.addStretch(1)

        self.placement_diagram = PlacementDiagram("fit", False)
        self.placement_caption = theme.style_hint(QLabel(PLACEMENT_CAPTIONS["fit"]))
        self.placement_caption.setWordWrap(True)
        self.placement_caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.placement_caption.setFixedWidth(134)
        diagram_column = QVBoxLayout()
        diagram_column.setContentsMargins(0, 0, 0, 0)
        diagram_column.setSpacing(theme.GAP_ICON + 1)
        diagram_column.addWidget(
            self.placement_diagram, 0, Qt.AlignmentFlag.AlignHCenter
        )
        diagram_column.addWidget(self.placement_caption, 0)
        diagram_column.addStretch(1)

        block = QHBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(theme.GAP_CONTROL + 3)
        block.addLayout(fields, 1)
        block.addLayout(diagram_column, 0)
        side_layout.addLayout(block)

        self.placement_combo = self._combo()
        self.placement_combo.addItem("Shrink to fit margins", "fit")
        self.placement_combo.addItem("Shift by margins (may clip)", "shift")
        self.placement_combo.addItem("Center right of punch line", "hole")
        self.placement_combo.addItem(
            "Center on punch line, no shrink (may clip)", "hole-clip"
        )
        self.placement_combo.setToolTip(
            "Fit: shrink content into the margins. Shift: move it at "
            "original size. Punch line: center the measured content "
            "between the 18 mm hole line and the far edge (margins "
            "above are ignored)"
        )
        row, _ = self._row("Placement", (self.placement_combo, 1))
        side_layout.addLayout(row)

        self.mirror_check = QCheckBox("Mirror margins (binding)")
        self.mirror_check.setFont(theme.body_font())
        self.mirror_check.setIcon(theme.icon("mirror", theme.INK, 15))
        self.mirror_check.setIconSize(QSize(15, 15))
        self.mirror_check.setToolTip(
            "For double-sided printing into a binder: even pages get the "
            "left margin on the right, keeping the binding edge clear on "
            "both sides of the sheet"
        )
        self.hole_check = QCheckBox("Punch hole guide (18 mm)")
        self.hole_check.setFont(theme.body_font())
        self.hole_check.setIcon(theme.icon("punch", theme.INK, 15))
        self.hole_check.setIconSize(QSize(15, 15))
        self.hole_check.setToolTip(
            "Shows a dashed line 18 mm from the binding edge in the "
            "preview as a hole-punching reference; alternates sides "
            "when margins are mirrored. Not printed."
        )
        for check in (self.mirror_check, self.hole_check):
            side_layout.addWidget(check)

    def _build_actions(self) -> QWidget:
        footer = QWidget()
        layout = QVBoxLayout(footer)
        layout.setContentsMargins(
            SIDE_PAD, 14, SIDE_PAD + SCROLLBAR_ROOM, theme.PAD_SECTION
        )
        layout.setSpacing(theme.GAP_CONTROL + 3)

        row = QHBoxLayout()
        row.setSpacing(theme.GAP_CONTROL)
        self.last_button = QPushButton(" Use last print's settings")
        self.last_button.setIcon(theme.icon("history", theme.INK, 15))
        self.last_button.setIconSize(QSize(15, 15))
        self.last_button.setMinimumHeight(theme.CONTROL_HEIGHT)
        self.last_button.setEnabled(printing.load_last_job() is not None)
        self.last_button.clicked.connect(self._load_last_settings)
        row.addWidget(self.last_button, 1)
        reset_button = QPushButton("Reset")
        reset_button.setMinimumHeight(theme.CONTROL_HEIGHT)
        reset_button.setToolTip("Reset all print options to their defaults")
        reset_button.clicked.connect(self._reset_settings)
        row.addWidget(reset_button, 0)
        layout.addLayout(row)

        self.print_button = QPushButton()
        self.print_button.setIcon(theme.icon("print", theme.ACCENT_TEXT, 16))
        self.print_button.setIconSize(QSize(16, 16))
        theme.style_print_button(self.print_button, "Print")
        self.print_button.setDefault(True)
        self.print_button.setEnabled(False)
        self.print_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.print_button.clicked.connect(self.do_print)
        self.print_button.installEventFilter(self)
        self.print_spinner = Spinner(15, theme.TEXT_MUTED, self.print_button)
        self.print_spinner.hide()
        layout.addWidget(self.print_button)
        return footer

    # ----- status bar -----

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)

        self.job_chip = JobChip("printing", "", self)
        self.job_chip.close_button.setToolTip("Dismiss")
        self.job_chip.closed.connect(self._dismiss_job_chip)
        self.job_chip.hide()
        bar.addPermanentWidget(self.job_chip)

        self.zoom_status = QLabel("Fit width")
        self.zoom_status.setObjectName("metaLabel")
        self.zoom_status.setFont(
            theme.body_font(theme.SMALL_POINT_SIZE, tabular=True)
        )
        bar.addPermanentWidget(self.zoom_status)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFixedHeight(14)
        bar.addPermanentWidget(separator)

        self.printer_status = QLabel("")
        self.printer_status.setObjectName("metaLabel")
        self.printer_status.setFont(theme.body_font(theme.SMALL_POINT_SIZE))
        bar.addPermanentWidget(self.printer_status)
        pad = QWidget()  # breathing room between the text and the edge
        pad.setFixedWidth(10)
        bar.addPermanentWidget(pad)

    # ----- signals -----

    def _connect_signals(self) -> None:
        self._caps_cache: dict[str, dict[str, printing.PPDOption]] = {}
        self.printer_combo.currentTextChanged.connect(self._update_capabilities)
        self.color_combo.currentIndexChanged.connect(self._apply_color_preview)

        # debounce option changes before re-running the preview transform
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(500)
        self._preview_timer.timeout.connect(self._apply_layout_preview)
        self.range_edit.textChanged.connect(self._on_range_changed)
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
        self.placement_combo.currentIndexChanged.connect(self._preview_timer.start)
        self.placement_combo.currentIndexChanged.connect(self._on_placement_changed)
        self.mirror_check.toggled.connect(self._preview_timer.start)
        self.mirror_check.toggled.connect(self._update_placement_diagram)
        self.hole_check.toggled.connect(self._preview_timer.start)
        self.scale_spin.valueChanged.connect(self._preview_timer.start)
        self.scale_spin.valueChanged.connect(self._update_more_button)

        # keep the collapsed "more options" badge honest about what's set
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

        help_menu = self.menuBar().addMenu("&Help")

        guide_action = QAction("&User Guide", self)
        guide_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        guide_action.triggered.connect(self.show_help)
        help_menu.addAction(guide_action)

        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def show_help(self) -> None:
        UserGuideDialog(self).exec()

    def show_about(self) -> None:
        AboutDialog(self).exec()

    # ---------- overlays ----------

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        # runs during construction too, so never assume a widget exists
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.Show):
            if watched is getattr(self, "viewer", None):
                self._layout_overlays()
            elif watched is getattr(self, "print_button", None):
                self._layout_print_spinner()
        return super().eventFilter(watched, event)

    def _layout_overlays(self) -> None:
        pill = getattr(self, "zoom_pill", None)
        if pill is None:
            return
        width, height = self.viewer.width(), self.viewer.height()
        pill.adjustSize()
        pill.move(
            max(0, (width - pill.width()) // 2),
            max(0, height - pill.height() - 18),
        )
        pill.raise_()
        for chip, right in (
            (self.binder_chip, False),
            (self.render_chip, True),
        ):
            chip.adjustSize()
            x = max(16, width - chip.width() - 16) if right else 16
            chip.move(x, 14)
            chip.raise_()

    def _layout_print_spinner(self) -> None:
        button = getattr(self, "print_button", None)
        if button is None or getattr(self, "print_spinner", None) is None:
            return
        text = button.text().strip()
        metrics = QFontMetrics(button.font())
        advance = metrics.horizontalAdvance(text)
        size = self.print_spinner.width()
        x = max(12, (button.width() - advance) // 2 - size - 10)
        y = max(0, (button.height() - size) // 2)
        self.print_spinner.move(x, y)
        self.print_spinner.raise_()

    # ---------- zoom ----------

    def zoom_in(self) -> None:
        self.viewer.zoom_in()

    def zoom_out(self) -> None:
        self.viewer.zoom_out()

    def fit_width(self) -> None:
        self.viewer.fit_width()

    def _on_zoom_changed(self, factor: float) -> None:
        if factor == 0.0:
            self.zoom_status.setText("Fit width")
            self.zoom_pill_label.setText("Fit")
            message = "Zoom: fit width"
        else:
            self.zoom_status.setText(f"{factor:.0%}")
            self.zoom_pill_label.setText(f"{factor:.0%}")
            message = f"Zoom: {factor:.0%}"
        self.statusBar().showMessage(message, 2000)
        self._layout_overlays()

    # ---------- preview mirroring of print options ----------

    def _apply_color_preview(self) -> None:
        grayscale = self.color_combo.currentData() == "monochrome"
        # the effect goes on the VIEWPORT, not the view: the floating
        # chips and the zoom pill are children of the view and must keep
        # their own colours while the paper previews in grayscale
        target = self.viewer.viewport()
        if grayscale:
            effect = QGraphicsColorizeEffect(target)
            # the effect grayscales by luminance, then SCREENS the tint
            # color over it — black is the identity for screen, leaving
            # the true grayscale (a gray tint would wash everything out)
            effect.setColor(QColor(0, 0, 0))
            effect.setStrength(1.0)
            target.setGraphicsEffect(effect)
        else:
            target.setGraphicsEffect(None)

    def _on_range_changed(self, text: str) -> None:
        invalid = bool(text.strip()) and not printing.validate_page_range(
            text.strip()
        )
        if bool(self.range_edit.property("invalid")) != invalid:
            self.range_edit.setProperty("invalid", invalid or None)
            _repolish(self.range_edit)
        self.range_hint.setVisible(not invalid)
        self.range_error.setVisible(invalid)
        self._preview_timer.start()
        self._update_header()

    def _on_placement_changed(self) -> None:
        hole = self.placement_combo.currentData().startswith("hole")
        for spin in self.margin_spins.values():
            spin.setEnabled(not hole)
        self._update_placement_diagram()

    def _update_placement_diagram(self) -> None:
        mode = self.placement_combo.currentData() or "fit"
        self.placement_diagram.set_state(mode, self.mirror_check.isChecked())
        self.placement_caption.setText(
            PLACEMENT_CAPTIONS.get(mode, PLACEMENT_CAPTIONS["fit"])
        )

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
            self.viewer.update_guides()  # the page geometry just changed

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
        hole_mode = job.margin_mode.startswith("hole")
        self.viewer.set_margin_guides(
            None
            if hole_mode  # margins are ignored in punch-zone placement
            else (
                job.margin_left * printing.MM_TO_PT,
                job.margin_top * printing.MM_TO_PT,
                job.margin_right * printing.MM_TO_PT,
                job.margin_bottom * printing.MM_TO_PT,
            ),
            mirror=job.mirror_margins,
        )
        self.viewer.set_hole_guide(
            printing.HOLE_GUIDE_MM * printing.MM_TO_PT
            if job.hole_guide or hole_mode
            else None
        )
        self._update_legend(job)
        self._update_binder_chip(job)
        options = printing.preview_job_options(job)
        has_margins = printing.needs_gs_pass(job)
        if not options and not has_margins:
            if self._showing_transformed:
                self._load_preserving_view(self.current_path)
                self._showing_transformed = False
                self.statusBar().showMessage("Preview: original document", 3000)
                QTimer.singleShot(0, self._update_header)
            return
        if self._preview_worker is not None and self._preview_worker.isRunning():
            # a transform is in flight: re-run with fresh options after it
            self._preview_dirty = True
            return
        if self._subset_dir is None:
            self._subset_dir = tempfile.TemporaryDirectory(prefix="pdfprinter-")

        # snapshot everything the worker needs; it must not touch the UI
        source = self.current_path
        subset_dir = self._subset_dir.name

        def task() -> str:
            # same order as print_file: layout first, margins on the result
            work = source
            if options:
                preview_path = os.path.join(subset_dir, "preview.pdf")
                if printing.pdftopdf_available():
                    printing.transform_for_preview(work, options, preview_path)
                    work = preview_path
                elif (
                    job.page_range
                    and printing.validate_page_range(job.page_range)
                    and shutil.which("qpdf")
                ):
                    # fallback: at least preview the page selection
                    printing.make_page_subset(work, job.page_range, preview_path)
                    work = preview_path
            if has_margins:
                margined_path = os.path.join(subset_dir, "margined.pdf")
                printing.apply_margins(work, margined_path, job)
                work = margined_path
            return work

        self.statusBar().showMessage("Rendering preview…")
        self._set_rendering(True)
        worker = _TransformWorker(task, self)
        worker.done.connect(
            lambda result, error: self._on_preview_done(
                worker, source, options, result, error
            )
        )
        self._preview_worker = worker
        worker.start()

    def _on_preview_done(
        self,
        worker: _TransformWorker,
        source: str,
        options: str,
        result: str | None,
        error: str | None,
    ) -> None:
        if self._preview_worker is worker:
            self._preview_worker = None
        worker.deleteLater()
        if self._preview_dirty:
            # options changed while rendering: redo with the fresh state
            self._preview_dirty = False
            self._apply_layout_preview()
            return
        self._set_rendering(False)
        if error is not None:
            # stays until the next preview succeeds; full text on stderr
            self.statusBar().showMessage(f"Preview failed: {error}")
            print(f"pdfprinter: preview failed: {error}", file=sys.stderr)
            return
        if result == source:
            self.statusBar().clearMessage()
            self._showing_transformed = False
            self._update_header()
            return
        self._load_preserving_view(result)
        self._showing_transformed = True
        label = options if options else "margins"
        self.statusBar().showMessage(f"Preview: as printed ({label})", 5000)
        # the page count only settles once the load event has run
        QTimer.singleShot(0, self._update_header)

    def _set_rendering(self, running: bool) -> None:
        self.render_chip.setVisible(running and self.current_path is not None)
        if running:
            self.render_chip.spinner.start()
        else:
            self.render_chip.spinner.stop()
        self._layout_overlays()

    # ---------- header, legend and chips ----------

    def _update_header(self) -> None:
        if not self.current_path:
            self.header_icon.setVisible(False)
            self.header_name.setFullText("No document")
            self.header_name.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self.header_meta.setFullText("")
            self.header_meta.setVisible(False)
            self._update_print_label()
            return
        self.header_icon.setVisible(True)
        self.header_name.setStyleSheet("")
        self.header_name.setFullText(os.path.basename(self.current_path))
        self.header_meta.setVisible(True)

        pages = self._source_pages
        parts = [f"{pages} page" + ("" if pages == 1 else "s")]
        if self._showing_transformed:
            page_range = self.range_edit.text().strip()
            if page_range and printing.validate_page_range(page_range):
                parts.append(f"printing pages {page_range}")
            sheets = max(0, self.document.pageCount())
            parts.append(f"{sheets} sheet" + ("" if sheets == 1 else "s"))
        else:
            size = _human_size(self.current_path)
            if size:
                parts.append(size)
            parts.append(_abbrev_dir(self.current_path))
        self.header_meta.setFullText(" · ".join(parts))
        self._update_print_label()

    def _update_print_label(self) -> None:
        if self._print_stage != "idle":
            return
        sheets = self.document.pageCount() if self.current_path else 0
        if not self.current_path or sheets <= 0:
            text = "Print"
        elif sheets == 1:
            text = "Print 1 sheet"
        else:
            text = f"Print {sheets} sheets"
        self.print_button.setText(f" {text}")
        self._layout_print_spinner()

    def _update_legend(self, job: printing.PrintJob | None = None) -> None:
        if job is None:
            job = self._current_job() if self.current_path else None
        if job is None or not self.current_path:
            self.legend_bar.setVisible(False)
            return
        hole_mode = job.margin_mode.startswith("hole")
        show_margins = not hole_mode and printing.margins_active(job)
        if show_margins:
            self.legend_margins_text.setText(
                "Margins "
                f"{job.margin_left:g} / {job.margin_right:g} / "
                f"{job.margin_top:g} / {job.margin_bottom:g} mm"
            )
        self.legend_margins.setVisible(show_margins)

        show_punch = bool(job.hole_guide or hole_mode)
        self.legend_punch_text.setText(
            f"Punch line {printing.HOLE_GUIDE_MM:g} mm"
        )
        self.legend_punch.setVisible(show_punch)

        hardware = self._printer_hw
        show_hw = bool(hardware and any(hardware))
        if show_hw:
            worst = max(hardware) / printing.MM_TO_PT
            self.legend_hw_text.setText(f"Unprintable {worst:.1f} mm")
        self.legend_hw.setVisible(show_hw)
        self.legend_bar.setVisible(True)

    def _update_binder_chip(self, job: printing.PrintJob) -> None:
        hole_mode = job.margin_mode.startswith("hole")
        parts = []
        if job.mirror_margins:
            parts.append("mirrored")
        if job.hole_guide or hole_mode:
            parts.append(f"{printing.HOLE_GUIDE_MM:g} mm punch")
        if not parts:
            self.binder_chip.hide()
            return
        self.binder_chip.label.setText("Binder mode · " + " · ".join(parts))
        self.binder_chip.setVisible(True)
        self._layout_overlays()

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
        if not self._printer_restored:
            # on startup, come back to the printer of the last print
            self._printer_restored = True
            last = printing.load_last_job()
            if last is not None and last.printer in printers:
                self.printer_combo.setCurrentText(last.printer)
        self.printer_combo.blockSignals(False)
        self._update_capabilities()
        count = len(printers)
        self.statusBar().showMessage(
            f"{count} printer" + ("" if count == 1 else "s") + " found", 5000
        )

    # ---------- more options ----------

    def _toggle_more_options(self, checked: bool) -> None:
        if self.more_group is not None:
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
        active = self._advanced_active_count()
        self.more_section.setBadgeText(f"{active} set" if active else None)

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
        tooltip = "" if has_duplex else DUPLEX_UNSUPPORTED
        self.duplex_combo.setToolTip(tooltip)
        for index, (_, _, _, tip) in enumerate(DUPLEX_SEGMENTS):
            self.duplex_combo.setItemToolTip(index, tooltip or tip)
        self.duplex_info.setToolTip(tooltip)
        self.duplex_info.setVisible(not has_duplex)
        self.duplex_label.setEnabled(has_duplex)
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
        self._apply_color_default(color_default)
        if not self._color_user_set:
            # untouched selection follows the driver's default
            self.color_combo.blockSignals(True)
            self.color_combo.setCurrentData(color_default or "color")
            self.color_combo.blockSignals(False)
            self._apply_color_preview()
        self._apply_color_preview()  # the rebuild bypassed the change signal
        self._update_more_button()

        self.printer_status.setText(f"{printer} · idle" if printer else "")

        # the printer's unprintable border: shade it in the preview and
        # re-place punch-zone content, which keeps out of it
        self._printer_hw = printing.printer_hw_margins(printer) if printer else None
        self.viewer.set_hw_margins(self._printer_hw)
        self._update_legend()
        if self.placement_combo.currentData().startswith("hole"):
            self._preview_timer.start()

    def _mark_color_user_set(self) -> None:
        self._color_user_set = True

    def _apply_color_default(self, default_value: str | None) -> None:
        """Mark the driver's own colour default on the segmented control.

        Only two concrete choices exist; the "(default)" marker sits on
        whichever one the driver reports. Selecting the marked segment
        sends nothing (the printer decides), which is why no separate
        "Printer" segment is needed.
        """
        self._color_default = default_value
        self.color_combo.blockSignals(True)
        for index, (label, value) in enumerate(COLOR_SEGMENTS):
            marked = value == default_value
            self.color_combo.setItemText(
                index, f"{label} (default)" if marked else label
            )
        self.color_combo.blockSignals(False)

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
            combo.addItem("Not reported by driver", None)
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
        if not self.zotero_storage:
            return
        # reuse the dialog so the picked collection, search text and
        # scroll position are right where the user left them
        if self._zotero_dialog is None:
            self._zotero_dialog = ZoteroPickerDialog(self.zotero_storage, self)
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
        self._source_pages = self.document.pageCount()
        self.preview_stack.setCurrentWidget(self.viewer)
        self.viewer.update_guides()
        self.print_button.setEnabled(True)
        self._update_header()
        self._update_legend()
        self.statusBar().showMessage(f"Loaded {path}", 5000)
        self._layout_overlays()
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
            color_mode=(
                ""
                if self.color_combo.currentData() == self._color_default
                else self.color_combo.currentData()
            ),
            media=self.media_combo.currentData(),
            landscape=self.orientation_combo.currentData(),
            number_up=self.nup_combo.currentData(),
            number_up_layout=self.nup_layout_combo.currentData(),
            collate=self.collate_check.isChecked(),
            page_set=self.pageset_combo.currentData(),
            reverse=self.reverse_check.isChecked(),
            # "Custom" means our gs scale; no print-scaling option is sent
            scaling=(
                ""
                if self.scaling_combo.currentData() == "custom"
                else self.scaling_combo.currentData()
            ),
            scale_percent=(
                self.scale_spin.value()
                if self.scaling_combo.currentData() == "custom"
                else 100
            ),
            margin_left=self.margin_spins["left"].value(),
            margin_right=self.margin_spins["right"].value(),
            margin_top=self.margin_spins["top"].value(),
            margin_bottom=self.margin_spins["bottom"].value(),
            mirror_margins=self.mirror_check.isChecked(),
            margin_mode=self.placement_combo.currentData(),
            hw_margins=getattr(self, "_printer_hw", None) or (0.0, 0.0, 0.0, 0.0),
            hole_guide=self.hole_check.isChecked(),
            extra_options=extra_options,
        )

    def _reset_settings(self) -> None:
        """Reset every print option to defaults, keeping the printer."""
        self._apply_job_to_ui(
            printing.PrintJob(printer=self.printer_combo.currentText())
        )
        # per-printer combos: back to the driver's default choice
        for combo in (self.quality_combo, self.source_combo, self.mediatype_combo):
            for index in range(combo.count()):
                if "(default)" in combo.itemText(index):
                    combo.setCurrentIndex(index)
                    break
            else:
                combo.setCurrentIndex(0)
        self.statusBar().showMessage("Settings reset to defaults", 5000)

    def _job_summary(self, job: printing.PrintJob) -> str:
        """The printer plus only the settings that differ from defaults."""
        lines = [job.printer or "(no printer)"]
        duplex_names = {
            "two-sided-long-edge": "Double-sided (long edge)",
            "two-sided-short-edge": "Double-sided (short edge)",
        }
        placement_names = {
            "shift": "Shift by margins",
            "hole": "Center right of punch line",
            "hole-clip": "Center on punch line (no shrink)",
        }

        def add(condition: bool, text: str) -> None:
            if condition:
                lines.append("\u2022 " + text)

        add(job.copies != 1, f"Copies: {job.copies}")
        add(bool(job.page_range), f"Pages: {job.page_range}")
        add(job.duplex != "one-sided",
            "Sides: " + duplex_names.get(job.duplex, job.duplex))
        add(job.color_mode == "color", "Color: Color")
        add(job.color_mode == "monochrome", "Color: Grayscale")
        add(bool(job.media), f"Paper: {job.media}")
        add(job.landscape, "Orientation: Landscape")
        add(job.number_up > 1, f"Pages/sheet: {job.number_up}")
        add(bool(job.number_up_layout), f"N-up order: {job.number_up_layout}")
        add(job.collate, "Collate copies")
        add(bool(job.page_set), f"Page set: {job.page_set} pages")
        add(job.reverse, "Reverse order")
        if job.scale_percent != 100:
            add(True, f"Scale: {job.scale_percent} %")
        elif job.scaling:
            add(True, f"Scaling: {job.scaling}")
        margins = [
            (side, mm)
            for side, mm in (
                ("L", job.margin_left), ("R", job.margin_right),
                ("T", job.margin_top), ("B", job.margin_bottom),
            )
            if mm > 0
        ]
        add(bool(margins),
            "Margins: " + "  ".join(f"{s} {mm:g} mm" for s, mm in margins))
        add(job.margin_mode in placement_names,
            "Placement: " + placement_names.get(job.margin_mode, ""))
        add(job.mirror_margins, "Mirror margins")
        add(job.hole_guide, "Punch hole guide")
        for keyword, choice in job.extra_options.items():
            add(True, f"{keyword}: {choice}")
        if len(lines) == 1:
            lines.append("\u2022 all settings at defaults")
        return "\n".join(lines)

    def _refresh_last_tooltip(self) -> None:
        job = printing.load_last_job()
        if job is None:
            self.last_button.setToolTip("No print has been made yet")
        else:
            self.last_button.setToolTip(self._job_summary(job))

    def _load_last_settings(self) -> None:
        job = printing.load_last_job()
        if job is None:
            self.last_button.setEnabled(False)
            self.statusBar().showMessage("No saved print settings found", 5000)
            return
        self._apply_job_to_ui(job)
        if job.printer and self.printer_combo.currentText() != job.printer:
            self.statusBar().showMessage(
                "Loaded last print's settings \u2014 printer "
                f"\u201c{job.printer}\u201d is not available, kept "
                f"{self.printer_combo.currentText()}"
            )
        else:
            self.statusBar().showMessage("Loaded last print's settings", 5000)

    def _apply_job_to_ui(self, job: printing.PrintJob) -> None:
        def pick(combo, value) -> None:
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
        if job.color_mode:
            pick(self.color_combo, job.color_mode)
            self._color_user_set = True
        else:  # printer default: the marked segment (Color if unknown)
            pick(self.color_combo, self._color_default or "color")
            self._color_user_set = False
        pick(self.media_combo, job.media)
        pick(self.orientation_combo, job.landscape)
        pick(self.nup_combo, job.number_up)
        pick(self.nup_layout_combo, job.number_up_layout)
        if job.scale_percent != 100:
            pick(self.scaling_combo, "custom")
            self.scale_spin.setValue(job.scale_percent)
        else:
            pick(self.scaling_combo, job.scaling)
            self.scale_spin.setValue(100)
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
        pick(self.placement_combo, job.margin_mode)
        self.hole_check.setChecked(job.hole_guide)
        self._on_placement_changed()
        self._preview_timer.start()

    # ---------- print ----------

    def _set_print_stage(self, stage: str) -> None:
        """Move the print button between idle / preparing / submitted."""
        self._print_stage = stage
        button = self.print_button
        if stage == "preparing":
            button.setEnabled(False)
            button.setIcon(QIcon())
            button.setProperty("done", None)
            button.setText("Preparing job…")
            self.print_spinner.show()
            self.print_spinner.start()
        elif stage == "submitted":
            button.setEnabled(True)
            button.setIcon(theme.icon("check", theme.ACCENT_TEXT, 16))
            button.setProperty("done", True)
            button.setText(" Submitted")
            self.print_spinner.stop()
            self.print_spinner.hide()
        else:
            button.setEnabled(self.current_path is not None)
            button.setIcon(theme.icon("print", theme.ACCENT_TEXT, 16))
            button.setProperty("done", None)
            self.print_spinner.stop()
            self.print_spinner.hide()
            self._update_print_label()
        _repolish(button)
        self._layout_print_spinner()

    def do_print(self) -> None:
        if not self.current_path:
            self.open_dialog()
            if not self.current_path:
                return
        page_range = self.range_edit.text().strip()
        if not printing.validate_page_range(page_range):
            self.range_edit.setFocus()
            self.statusBar().showMessage(
                "Invalid pages — must look like 1-4,7,10-12"
            )
            QMessageBox.warning(
                self, "Invalid pages", "Page range must look like: 1-4,7,10-12"
            )
            return
        job = self._current_job()
        path = self.current_path
        self._set_print_stage("preparing")
        self.statusBar().showMessage("Preparing print job…")
        worker = _TransformWorker(lambda: printing.print_file(path, job), self)
        worker.done.connect(
            lambda job_id, error: self._on_print_done(worker, job, job_id, error)
        )
        self._print_worker = worker
        worker.start()

    def _on_print_done(
        self,
        worker: _TransformWorker,
        job: printing.PrintJob,
        job_id: str | None,
        error: str | None,
    ) -> None:
        worker.deleteLater()
        self._print_worker = None
        if error is not None:
            self._set_print_stage("idle")
            self._show_job_chip("failed", "Print failed")
            self.statusBar().showMessage(f"Print failed: {error}")
            if show_print_error(self, error, detail=job.printer) == "refresh":
                self.refresh_printers()
            return
        printing.save_last_job(job)
        self.last_button.setEnabled(True)
        self._refresh_last_tooltip()
        self._set_print_stage("submitted")
        QTimer.singleShot(4000, self._clear_submitted)
        self._show_job_chip("printing", f"Job {job_id} · printing…")
        self.statusBar().showMessage(f"Job {job_id} submitted to the printer")
        self._track_job(job_id)

    def _clear_submitted(self) -> None:
        if self._print_stage == "submitted":
            self._set_print_stage("idle")

    # ---------- job tracking ----------

    def _show_job_chip(self, state: str, text: str, hide_after: int = 0) -> None:
        self._chip_timer.stop()
        self.job_chip.set_state(state, text)
        self.job_chip.show()
        if hide_after:
            self._chip_timer.start(hide_after)

    def _dismiss_job_chip(self) -> None:
        self._chip_timer.stop()
        self.job_chip.hide()

    def _track_job(self, job_id: str) -> None:
        self._tracked_job = job_id
        self._track_polls = 0
        self._job_timer.start()
        printer = self.printer_combo.currentText()
        if printer:
            self.printer_status.setText(f"{printer} · printing")

    def _poll_job(self) -> None:
        job_id = self._tracked_job
        if job_id is None:
            self._job_timer.stop()
            return
        self._track_polls += 1
        state = printing.job_state(job_id)
        printer = self.printer_combo.currentText()
        if state == "queued":
            if self._track_polls > 150:  # ~5 minutes; stop nagging
                self._job_timer.stop()
                self._show_job_chip("printing", f"Job {job_id} · still queued")
                self.statusBar().showMessage(
                    f"Job {job_id} is still queued (check the printer)"
                )
            else:
                self._show_job_chip("printing", f"Job {job_id} · printing…")
                self.statusBar().showMessage(f"Job {job_id}: printing…")
            return
        self._job_timer.stop()
        self._tracked_job = None
        if printer:
            self.printer_status.setText(f"{printer} · idle")
        if state == "completed":
            self._show_job_chip(
                "completed", f"Job {job_id} · completed", hide_after=30000
            )
            self.statusBar().showMessage(f"Job {job_id} completed ✓", 30000)
        else:
            self._show_job_chip("canceled", f"Job {job_id} · canceled")
            self.statusBar().showMessage(
                f"Job {job_id} disappeared from the queue — it may have "
                "been canceled or aborted"
            )

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        # let running workers finish; destroying a live thread aborts
        for worker in (self._preview_worker, self._print_worker):
            if worker is not None and worker.isRunning():
                worker.wait(10000)
        super().closeEvent(event)

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
