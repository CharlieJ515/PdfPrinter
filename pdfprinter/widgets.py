"""Reusable widgets of the PDF Printer design system."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

__all__ = [
    "SegmentedControl",
    "CollapsibleSection",
    "PlacementDiagram",
    "Spinner",
    "JobChip",
    "apply_row_height",
    "icon",
]

#: Re-exported so callers need only one import for widgets and icons.
icon = theme.icon


class _RowHeightDelegate(QStyledItemDelegate):
    """Qt ignores QSS row heights in item views; this does not."""

    def __init__(self, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._height = height

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), self._height))
        return size


def apply_row_height(view, height: int = 28) -> None:
    """Give an item view the design system's roomy rows."""
    view.setItemDelegate(_RowHeightDelegate(height, view))


def _repolish(widget: QWidget) -> None:
    """Re-evaluate the stylesheet after a dynamic property changed."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


# --------------------------------------------------------------------------


class SegmentedControl(QWidget):
    """A row of exclusive buttons drawn as one segmented control.

    Drop-in replacement for a small QComboBox: it carries a label and a
    userData per item, reports :meth:`currentData` and emits
    :attr:`changed` (plus :attr:`currentIndexChanged`) when the selection
    moves. Left/Right/Home/End move the selection from the keyboard.
    """

    changed = pyqtSignal()
    currentIndexChanged = pyqtSignal(int)  # noqa: N815 (mirrors QComboBox)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = []
        self._labels: list[str] = []
        self._data: list[Any] = []
        self._index = -1
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(-1)  # segments share their 1 px border
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    # ---------- building ----------

    def addItem(  # noqa: N802 (mirrors QComboBox)
        self,
        label: str,
        userData: Any = None,  # noqa: N803 (mirrors QComboBox)
        icon: QIcon | None = None,
    ) -> QPushButton:
        """Append a segment and return its button."""
        # Qt leaves no room between a button's icon and its text; a
        # leading space buys the design system's 4.6 px icon/label gap
        button = QPushButton(f" {label}" if icon is not None else label, self)
        button.setObjectName("segment")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        button.setMinimumHeight(theme.CONTROL_HEIGHT)
        if icon is not None:
            button.setIcon(icon)
            button.setIconSize(QSize(14, 14))
        button.installEventFilter(self)
        button.toggled.connect(self._on_toggled)
        self.layout().addWidget(button, 1)
        self._buttons.append(button)
        self._labels.append(label)
        self._data.append(userData)
        self._update_edges()
        if self._index < 0:
            self.setCurrentIndex(0)
        return button

    def clear(self) -> None:
        """Remove every segment."""
        for button in self._buttons:
            button.toggled.disconnect(self._on_toggled)
            button.setParent(None)
            button.deleteLater()
        self._buttons.clear()
        self._labels.clear()
        self._data.clear()
        self._index = -1

    def _update_edges(self) -> None:
        last = len(self._buttons) - 1
        for position, button in enumerate(self._buttons):
            if last == 0:
                edge = "only"
            elif position == 0:
                edge = "first"
            elif position == last:
                edge = "last"
            else:
                edge = "middle"
            if button.property("edge") != edge:
                button.setProperty("edge", edge)
                _repolish(button)

    # ---------- selection ----------

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:  # noqa: N802 (mirrors QComboBox)
        return self._index

    def currentData(self) -> Any:  # noqa: N802 (mirrors QComboBox)
        if 0 <= self._index < len(self._data):
            return self._data[self._index]
        return None

    def currentText(self) -> str:  # noqa: N802 (mirrors QComboBox)
        if 0 <= self._index < len(self._labels):
            return self._labels[self._index]
        return ""

    def itemText(self, index: int) -> str:  # noqa: N802 (mirrors QComboBox)
        return self._labels[index] if 0 <= index < len(self._labels) else ""

    def itemData(self, index: int) -> Any:  # noqa: N802 (mirrors QComboBox)
        return self._data[index] if 0 <= index < len(self._data) else None

    def findData(self, value: Any) -> int:  # noqa: N802 (mirrors QComboBox)
        for position, data in enumerate(self._data):
            if data == value:
                return position
        return -1

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not (0 <= index < len(self._buttons)) or index == self._index:
            return
        button = self._buttons[index]
        was_blocked = button.blockSignals(True)
        button.setChecked(True)
        button.blockSignals(was_blocked)
        self._set_index(index)

    def setCurrentData(self, value: Any) -> bool:  # noqa: N802
        """Select the segment carrying ``value``; False when absent."""
        index = self.findData(value)
        if index < 0:
            return False
        self.setCurrentIndex(index)
        return True

    def _set_index(self, index: int) -> None:
        if index == self._index:
            return
        self._index = index
        for position, button in enumerate(self._buttons):
            if position == index:
                button.raise_()  # the accent border overlaps its neighbours
            button.setChecked(position == index)
        self.currentIndexChanged.emit(index)
        self.changed.emit()

    def _on_toggled(self, checked: bool) -> None:
        if not checked:
            return
        sender = self.sender()
        if sender in self._buttons:
            self._set_index(self._buttons.index(sender))

    # ---------- per-item state ----------

    def setItemToolTip(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= index < len(self._buttons):
            self._buttons[index].setToolTip(text)

    def setItemEnabled(self, index: int, enabled: bool) -> None:  # noqa: N802
        if 0 <= index < len(self._buttons):
            self._buttons[index].setEnabled(enabled)

    def setItemText(self, index: int, text: str) -> None:  # noqa: N802
        if 0 <= index < len(self._buttons):
            button = self._buttons[index]
            self._labels[index] = text
            button.setText(
                f" {text}" if not button.icon().isNull() else text
            )

    def button(self, index: int) -> QPushButton | None:
        return self._buttons[index] if 0 <= index < len(self._buttons) else None

    # ---------- keyboard ----------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.KeyPress and watched in self._buttons:
            key = event.key()
            step = {
                Qt.Key.Key_Left: -1,
                Qt.Key.Key_Up: -1,
                Qt.Key.Key_Right: 1,
                Qt.Key.Key_Down: 1,
            }.get(key)
            if step is not None:
                target = self._next_enabled(self._index, step)
                if target >= 0:
                    self.setCurrentIndex(target)
                    self._buttons[target].setFocus(
                        Qt.FocusReason.TabFocusReason
                    )
                return True
            if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
                start = -1 if key == Qt.Key.Key_Home else len(self._buttons)
                target = self._next_enabled(
                    start, 1 if key == Qt.Key.Key_Home else -1
                )
                if target >= 0:
                    self.setCurrentIndex(target)
                    self._buttons[target].setFocus(
                        Qt.FocusReason.TabFocusReason
                    )
                return True
        return super().eventFilter(watched, event)

    def _next_enabled(self, start: int, step: int) -> int:
        position = start + step
        while 0 <= position < len(self._buttons):
            if self._buttons[position].isEnabled():
                return position
            position += step
        return -1

    def focusInEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if 0 <= self._index < len(self._buttons):
            self._buttons[self._index].setFocus(event.reason())
        super().focusInEvent(event)


# --------------------------------------------------------------------------


class CollapsibleSection(QWidget):
    """Section label that folds a content widget away.

    Collapsed it reads ``› MORE OPTIONS``; expanded ``⌄ MORE OPTIONS``
    with an optional accent pill on the right (``3 set``).
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str = "",
        parent: QWidget | None = None,
        *,
        expanded: bool = False,
    ) -> None:
        super().__init__(parent)
        self._content: QWidget | None = None
        self._title = title

        self.header = QToolButton(self)
        self.header.setObjectName("sectionHeader")
        self.header.setText(f"  {title}")  # room between chevron and label
        self.header.setFont(theme.section_label_font())
        self.header.setCheckable(True)
        self.header.setAutoRaise(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.header.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.header.setIconSize(QSize(12, 12))
        self.header.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.header.toggled.connect(self._on_header_toggled)

        self.badge = QLabel(self)
        self.badge.setObjectName("badge")
        self.badge.setFont(theme.body_font(theme.SMALL_POINT_SIZE))
        self.badge.hide()

        self.rule = QWidget(self)  # hairline filling the rest of the row
        self.rule.setObjectName("chipSep")
        theme.styled_panel(self.rule)
        self.rule.setFixedHeight(1)
        self.rule.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(theme.GAP_CONTROL)
        header_row.addWidget(self.header, 0)
        header_row.addWidget(self.badge, 0)
        header_row.addWidget(self.rule, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.GAP_CONTROL)
        layout.addLayout(header_row)

        self._sync_chevron(expanded)
        self.header.setChecked(expanded)

    # ---------- api ----------

    def setContentWidget(self, widget: QWidget) -> None:  # noqa: N802
        """Install the widget the header folds away."""
        if self._content is not None:
            self.layout().removeWidget(self._content)
            self._content.setParent(None)
        self._content = widget
        if widget is not None:
            widget.setParent(self)
            self.layout().addWidget(widget)
            widget.setVisible(self.header.isChecked())

    def contentWidget(self) -> QWidget | None:  # noqa: N802
        return self._content

    def setTitle(self, title: str) -> None:  # noqa: N802
        self._title = title
        self.header.setText(f"  {title}")

    def title(self) -> str:
        return self._title

    def setBadgeText(self, text: str | None) -> None:  # noqa: N802
        """Show an accent pill next to the title (None hides it)."""
        if text:
            self.badge.setText(text)
            self.badge.show()
        else:
            self.badge.clear()
            self.badge.hide()

    def badgeText(self) -> str:  # noqa: N802
        return self.badge.text()

    def setExpanded(self, expanded: bool) -> None:  # noqa: N802
        self.header.setChecked(expanded)

    def expanded(self) -> bool:
        return self.header.isChecked()

    # ---------- internals ----------

    def _sync_chevron(self, expanded: bool) -> None:
        self.header.setIcon(
            theme.icon(
                "chevron-down" if expanded else "chevron-right",
                theme.TEXT_MUTED,
                12,
            )
        )

    def _on_header_toggled(self, checked: bool) -> None:
        self._sync_chevron(checked)
        if self._content is not None:
            self._content.setVisible(checked)
        self.toggled.emit(checked)


# --------------------------------------------------------------------------


class PlacementDiagram(QWidget):
    """Little painted page showing where the content lands on the sheet.

    Modes mirror ``PrintJob.margin_mode``: ``"fit"``, ``"shift"``,
    ``"hole"`` and ``"hole-clip"``. With ``mirror`` the sheet is drawn
    twice, odd and even, with the binding side swapped.
    """

    MODES = ("fit", "shift", "hole", "hole-clip")

    def __init__(
        self,
        mode: str = "fit",
        mirror: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode if mode in self.MODES else "fit"
        self._mirror = mirror
        self.setFixedSize(110, 150)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---------- api ----------

    def set_state(self, mode: str, mirror: bool = False) -> None:
        """Show ``mode`` (one of :attr:`MODES`), optionally mirrored."""
        mode = mode if mode in self.MODES else "fit"
        if (mode, mirror) == (self._mode, self._mirror):
            return
        self._mode, self._mirror = mode, mirror
        self.update()

    def mode(self) -> str:
        return self._mode

    def mirror(self) -> bool:
        return self._mirror

    # ---------- painting ----------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if self._mirror:
            gap = 7.0
            half = (area.width() - gap) / 2.0
            left = QRectF(area.left(), area.top(), half, area.height())
            right = QRectF(area.right() - half, area.top(), half, area.height())
            self._draw_sheet(painter, self._page_rect(left), even=False)
            self._draw_sheet(painter, self._page_rect(right), even=True)
        else:
            self._draw_sheet(painter, self._page_rect(area), even=False)
        painter.end()

    @staticmethod
    def _page_rect(area: QRectF) -> QRectF:
        """The biggest A4-shaped page centred in ``area``."""
        ratio = 297.0 / 210.0
        width = min(area.width(), area.height() / ratio)
        height = width * ratio
        return QRectF(
            area.center().x() - width / 2.0,
            area.center().y() - height / 2.0,
            width,
            height,
        )

    def _draw_sheet(self, painter: QPainter, page: QRectF, even: bool) -> None:
        w, h = page.width(), page.height()

        # the sheet itself
        painter.setPen(QPen(theme.qcolor(theme.INK, 0.22), 1.0))
        painter.setBrush(theme.qcolor(theme.PAPER))
        painter.drawRect(page)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        block = theme.qcolor("#e6e3e3")

        def fill(rect: QRectF) -> None:
            painter.fillRect(rect, block)

        def flip(rect: QRectF) -> QRectF:
            """Mirror a rect across the page's vertical centre line."""
            if not even:
                return rect
            return QRectF(
                page.left() + (page.right() - rect.right()),
                rect.top(),
                rect.width(),
                rect.height(),
            )

        if self._mode in ("fit", "shift"):
            if self._mode == "fit":
                margins = QRectF(
                    page.left() + 0.13 * w,
                    page.top() + 0.085 * h,
                    w * 0.74,
                    h * 0.83,
                )
                content = margins.adjusted(
                    0.07 * w, 0.05 * h, -0.07 * w, -0.05 * h
                )
            else:  # shifted by the margins, far edge may clip
                margins = QRectF(
                    page.left() + 0.21 * w,
                    page.top() + 0.13 * h,
                    w * 0.80,
                    h * 0.84,
                )
                content = QRectF(
                    margins.left() + 0.07 * w,
                    margins.top() + 0.05 * h,
                    w * 0.60,
                    h * 0.72,
                )
            margins, content = flip(margins), flip(content)
            fill(content)
            painter.setPen(theme.guide_pen(theme.MARGIN_GUIDE))
            painter.drawRect(margins.intersected(page.adjusted(-1, -1, 1, 1)))
        else:
            punch_x = page.left() + 0.17 * w
            if self._mode == "hole":  # centred right of the punch line
                content = QRectF(
                    punch_x + 0.11 * w,
                    page.top() + 0.13 * h,
                    w * 0.60,
                    h * 0.72,
                )
            else:  # hole-clip: full width, both edges may clip
                content = QRectF(
                    page.left() + 0.02 * w,
                    page.top() + 0.15 * h,
                    w * 0.96,
                    h * 0.68,
                )
            content = flip(content)
            fill(content)
            x = punch_x if not even else page.right() - 0.17 * w
            painter.setPen(theme.guide_pen(theme.PUNCH_GUIDE))
            painter.drawLine(QPointF(x, page.top()), QPointF(x, page.bottom()))


# --------------------------------------------------------------------------


class Spinner(QWidget):
    """A small indeterminate ring, 14 px by default."""

    def __init__(
        self,
        size: int = 14,
        color: str = theme.ACCENT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self._color = QColor(color)
        self._angle = 0
        self._spinning = False
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---------- api ----------

    def start(self) -> None:
        """Begin spinning (idempotent)."""
        self._spinning = True
        if self.isVisible():
            self._timer.start()
        self.update()

    def stop(self) -> None:
        """Stop spinning and freeze at the current angle."""
        self._spinning = False
        self._timer.stop()
        self.update()

    def isSpinning(self) -> bool:  # noqa: N802 (Qt naming)
        return self._spinning

    def setColor(self, color: str) -> None:  # noqa: N802 (Qt naming)
        self._color = QColor(color)
        self.update()

    def color(self) -> QColor:
        return QColor(self._color)

    # ---------- internals ----------

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._spinning:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self._timer.stop()  # never burn cycles off-screen
        super().hideEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(self._size, self._size)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = max(1.2, self._size / 9.0)
        rect = QRectF(self.rect()).adjusted(
            width / 2.0 + 0.5, width / 2.0 + 0.5, -width / 2.0 - 0.5,
            -width / 2.0 - 0.5,
        )
        track = QColor(self._color)
        track.setAlphaF(0.22)
        pen = QPen(track, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        if self._spinning:
            pen.setColor(self._color)
            painter.setPen(pen)
            painter.drawArc(rect, int(-self._angle * 16), int(-110 * 16))
        painter.end()


# --------------------------------------------------------------------------


class JobChip(QWidget):
    """Pill reporting the tracked CUPS job — printing, done or gone."""

    closed = pyqtSignal()

    STATES = ("printing", "completed", "canceled", "failed")

    _PALETTE = {
        "printing": (theme.CONTROL_FILL, theme.HAIRLINE_SOLID, theme.INK, None),
        "completed": (
            theme.ACCENT_TINT,
            theme.ACCENT,
            theme.ACCENT_TEXT,
            "check",
        ),
        "canceled": (theme.ERROR_TINT, theme.ERROR_BORDER, theme.ERROR, "blocked"),
        "failed": (theme.ERROR_TINT, theme.ERROR_BORDER, theme.ERROR, "warning"),
    }

    def __init__(
        self,
        state: str = "printing",
        text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = "printing"
        self._closable = True

        self.spinner = Spinner(13, theme.TEXT_FAINT, self)
        self.mark = QLabel(self)
        self.mark.setFixedSize(14, 14)
        self.label = QLabel(self)
        self.label.setFont(theme.body_font())
        self.separator = QWidget(self)
        self.separator.setObjectName("chipSep")
        theme.styled_panel(self.separator)
        self.separator.setFixedWidth(1)
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("chipClose")
        self.close_button.setIcon(theme.icon("close", theme.TEXT_MUTED, 11))
        self.close_button.setIconSize(QSize(11, 11))
        self.close_button.setFixedSize(16, 16)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Cancel this job")
        self.close_button.clicked.connect(self.closed.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 9, 0)
        layout.setSpacing(7)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.separator)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_state(state, text)

    # ---------- api ----------

    def set_state(self, state: str, text: str = "") -> None:
        """Switch between ``printing``/``completed``/``canceled``/``failed``."""
        if state not in self._PALETTE:
            state = "printing"
        self._state = state
        if text:
            self.label.setText(text)
        _, _, ink, mark = self._PALETTE[state]
        self.label.setStyleSheet(f"color: {ink};")
        spinning = state == "printing"
        self.spinner.setVisible(spinning)
        if spinning:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.mark.setVisible(mark is not None)
        if mark is not None:
            self.mark.setPixmap(theme.pixmap(mark, ink, 13))
        show_close = self._closable and state == "printing"
        self.separator.setVisible(show_close)
        self.close_button.setVisible(show_close)
        self.updateGeometry()
        self.update()

    def state(self) -> str:
        return self._state

    def text(self) -> str:
        return self.label.text()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        self.label.setText(text)
        self.updateGeometry()

    def setClosable(self, closable: bool) -> None:  # noqa: N802 (Qt naming)
        """Whether the ✕ shows while the job is printing."""
        self._closable = closable
        self.set_state(self._state)

    # ---------- painting ----------

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.separator.setFixedHeight(max(12, self.height() - 14))
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        background, border, _, _ = self._PALETTE[self._state]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        painter.setPen(QPen(QColor(border), 1.0))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()
