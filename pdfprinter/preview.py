"""Preview view: zoomable PDF widget and its screen-guide overlay.

The guides (margins, punch line, unprintable border, page captions)
are painted on a transparent overlay stacked over the viewport so the
grayscale preview effect never desaturates them.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import QGraphicsColorizeEffect, QPinchGesture, QWidget

from . import theme

ZOOM_STEP = 1.25
MIN_ZOOM = 0.2
MAX_ZOOM = 8.0
SCROLL_MULTIPLIER = 2.0  # touchpad pixel deltas feel too small at 1:1
WHEEL_STEP_PX = 220  # scroll distance per mouse-wheel notch


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


