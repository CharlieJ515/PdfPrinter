"""Small reusable UI pieces shared across the app's windows."""

from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

from . import printing, theme


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


