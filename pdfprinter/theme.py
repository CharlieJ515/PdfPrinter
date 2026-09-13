"""Design system: palette, fonts, code-drawn icons and the app stylesheet.

Every size in this module is a *point* size (Qt logical points, 1 pt =
4/3 logical px at the usual 96 dpi), so the values scale with the user's
display the way the rest of Qt does. The design spec is written in px;
the conversion is ``pt = px * 0.75``.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Final

from PyQt6.QtCore import QPointF, QRectF, QStandardPaths, Qt
from PyQt6.QtWidgets import QProxyStyle, QStyle
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

GROUND: Final = "#f3f2f2"  # window / panel background
CONTROL_FILL: Final = "#f8f4f4"  # inside of every control
CANVAS: Final = "#d7d3d3"  # the grey behind the paper in the preview
PAPER: Final = "#ffffff"
INK: Final = "#2d2b2b"  # primary text, tooltip background
ACCENT: Final = "#b68235"  # strokes, checked states
ACCENT_TINT: Final = "#fff3e4"  # hover / checked fill
ACCENT_TINT_DEEP: Final = "#ffe7c9"  # pressed fill
ACCENT_TEXT: Final = "#7d5411"  # text on tinted fills

TEXT_MUTED: Final = "#6d6a6a"  # hints, status bar, section labels
TEXT_FAINT: Final = "#928e8e"  # placeholders, metadata
TEXT_DISABLED: Final = "rgba(45, 43, 43, 0.40)"
DISABLED_FILL: Final = "#e8e6e6"

HAIRLINE: Final = "rgba(45, 43, 43, 0.16)"  # 1 px control border
HAIRLINE_SOFT: Final = "rgba(45, 43, 43, 0.09)"  # separators inside lists
HAIRLINE_SOLID: Final = "#dedbdb"  # opaque equivalent of HAIRLINE

# errors (artboard 1g)
ERROR: Final = "#a5231d"
ERROR_BORDER: Final = "#c0413a"
ERROR_TINT: Final = "#fdf2f1"

# guide colours — fixed semantics, never themed away
MARGIN_GUIDE: Final = "#c2352b"  # dashed margin rectangle
PUNCH_GUIDE: Final = "#2f4bab"  # dashed punch line
UNPRINTABLE: Final = "#605d5d"  # hardware border, flat fill
UNPRINTABLE_ALPHA: Final = 0.26

GUIDE_DASH: Final = (6.0, 4.0)  # dash pattern in px, per the style guide
GUIDE_WIDTH: Final = 1.5

# --------------------------------------------------------------------------
# metrics (see "SPACING & METRICS" on artboard 1h)
# --------------------------------------------------------------------------

GAP_ICON: Final = 5  # icon → label
GAP_CONTROL: Final = 9  # control → control
PAD_SECTION: Final = 18  # inside a section
GAP_GROUP: Final = 28  # section → section
CONTROL_HEIGHT: Final = 30
PRINT_BUTTON_HEIGHT: Final = 44
RADIUS: Final = 4
SIDEBAR_WIDTH: Final = 372

# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

BASE_POINT_SIZE: Final = 10.0  # ≈ the spec's "Lora 12.5"
SMALL_POINT_SIZE: Final = 8.5  # ≈ the spec's "Lora 11"
SECTION_POINT_SIZE: Final = 9.0  # ≈ the spec's "Cormorant 11 semibold"
TITLE_POINT_SIZE: Final = 21.0  # ≈ the spec's "Cormorant 28"
SECTION_LETTER_SPACING: Final = 112.0  # percent

_FONT_FILES: Final = (
    "Lora.ttf",
    "Lora-Italic.ttf",
    "CormorantGaramond.ttf",
    "CormorantGaramond-Italic.ttf",
)
_BODY_FAMILY_NAME: Final = "Lora"
_SERIF_FAMILY_NAME: Final = "Cormorant Garamond"
_FALLBACK_SERIFS: Final = (
    "DejaVu Serif",
    "Liberation Serif",
    "Noto Serif",
    "Times New Roman",
    "Georgia",
    "Serif",
)

#: Family actually used for body copy — "Lora" once :func:`load_fonts` ran.
BODY_FAMILY: str = _BODY_FAMILY_NAME
#: Family actually used for display type — "Cormorant Garamond" if present.
SERIF_FAMILY: str = _SERIF_FAMILY_NAME


def font_dir() -> Path:
    """Directory holding the bundled TTFs (works from a wheel or a checkout)."""
    try:
        from importlib.resources import files

        path = files(__package__ or "pdfprinter").joinpath("fonts")
        return Path(str(path))
    except Exception:  # noqa: BLE001 — zipped/odd installs fall back to __file__
        return Path(__file__).resolve().parent / "fonts"


def _first_available(candidates: tuple[str, ...]) -> str:
    installed = set(QFontDatabase.families())
    for name in candidates:
        if name in installed:
            return name
    return ""


def load_fonts(app: QGuiApplication | None = None) -> list[str]:
    """Register the bundled fonts and set the application's default font.

    Missing or unreadable font files are not fatal: the module falls back
    to an installed serif (and finally to Qt's generic serif hint).
    Returns the sorted family names Qt reports for what was registered.
    """
    global BODY_FAMILY, SERIF_FAMILY

    families: set[str] = set()
    directory = font_dir()
    for name in _FONT_FILES:
        path = directory / name
        try:
            if not path.is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
        except Exception:  # noqa: BLE001 — never let type break the app
            continue
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))

    installed = set(QFontDatabase.families())
    fallback = _first_available(_FALLBACK_SERIFS)
    BODY_FAMILY = (
        _BODY_FAMILY_NAME
        if _BODY_FAMILY_NAME in families or _BODY_FAMILY_NAME in installed
        else fallback
    )
    SERIF_FAMILY = (
        _SERIF_FAMILY_NAME
        if _SERIF_FAMILY_NAME in families or _SERIF_FAMILY_NAME in installed
        else BODY_FAMILY
    )

    if app is not None:
        app.setFont(body_font())
    return sorted(families)


def _styled(family: str, size_pt: float, weight: QFont.Weight) -> QFont:
    font = QFont(family, weight=weight)
    if not family:  # no serif installed at all: ask Qt for its best one
        font.setStyleHint(QFont.StyleHint.Serif, QFont.StyleStrategy.PreferMatch)
        font.setFamily("serif")
    font.setPointSizeF(size_pt)
    font.setWeight(weight)
    return font


def body_font(
    size_pt: float = BASE_POINT_SIZE,
    weight: QFont.Weight = QFont.Weight.Normal,
    *,
    tabular: bool = False,
) -> QFont:
    """Lora, the family used for labels, values and body copy."""
    font = _styled(BODY_FAMILY, size_pt, weight)
    if tabular:  # figures line up in columns (page counts, mm, %)
        try:
            font.setFeature("tnum", 1)  # Qt ≥ 6.7
        except Exception:  # noqa: BLE001
            pass
    return font


def serif_font(
    size_pt: float = TITLE_POINT_SIZE,
    weight: QFont.Weight = QFont.Weight.Normal,
) -> QFont:
    """Cormorant Garamond, the display family (titles, section labels)."""
    return _styled(SERIF_FAMILY, size_pt, weight)


def section_label_font(size_pt: float = SECTION_POINT_SIZE) -> QFont:
    """Small-caps, letterspaced Cormorant used for every section label."""
    font = serif_font(size_pt, QFont.Weight.DemiBold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(
        QFont.SpacingType.PercentageSpacing, SECTION_LETTER_SPACING
    )
    return font


def print_button_font(size_pt: float = 11.0) -> QFont:
    """Uppercase letterspaced Cormorant for the primary print button."""
    font = serif_font(size_pt, QFont.Weight.DemiBold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118.0)
    return font


# --------------------------------------------------------------------------
# small helpers for the objectName hooks the stylesheet defines
# --------------------------------------------------------------------------


def style_section_label(label, text: str | None = None):
    """Turn a QLabel into a SECTION LABEL (objectName + font)."""
    label.setObjectName("sectionLabel")
    label.setFont(section_label_font())
    if text is not None:
        label.setText(text)
    return label


def style_hint(label, text: str | None = None):
    """Turn a QLabel into a small grey hint line."""
    label.setObjectName("hintLabel")
    label.setFont(body_font(SMALL_POINT_SIZE))
    if text is not None:
        label.setText(text)
    return label


def style_error(label, text: str | None = None):
    """Turn a QLabel into a small red inline error line."""
    label.setObjectName("errorLabel")
    label.setFont(body_font(SMALL_POINT_SIZE))
    if text is not None:
        label.setText(text)
    return label


def style_print_button(button, text: str | None = None):
    """Turn a QPushButton into the primary print button.

    Set the icon *before* calling: Qt leaves no room between a button's
    icon and its label, so the text gets a leading space when one is set.
    """
    button.setObjectName("printButton")
    button.setFont(print_button_font())
    button.setMinimumHeight(PRINT_BUTTON_HEIGHT)
    if text is not None:
        button.setText(text if button.icon().isNull() else f" {text}")
    return button


def style_header_view(header):
    """Give a QHeaderView the letterspaced small-caps column labels."""
    header.setFont(section_label_font(SMALL_POINT_SIZE))
    return header


def styled_panel(widget):
    """Let a plain QWidget honour its stylesheet background/border."""
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


def qcolor(spec: str, alpha: float = 1.0) -> QColor:
    """A QColor from one of the palette constants, optionally faded."""
    color = QColor(spec)
    if alpha < 1.0:
        color.setAlphaF(alpha)
    return color


def guide_pen(color: str, width: float = GUIDE_WIDTH) -> QPen:
    """The dashed 1.5 px 6/4 pen shared by every on-screen guide."""
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCosmetic(True)
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern([GUIDE_DASH[0] / width, GUIDE_DASH[1] / width])
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    return pen


def apply_palette(app: QGuiApplication) -> None:
    """Palette roles the stylesheet cannot reach (placeholders, selections)."""
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(GROUND))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(INK))
    palette.setColor(QPalette.ColorRole.Base, QColor(CONTROL_FILL))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(GROUND))
    palette.setColor(QPalette.ColorRole.Text, QColor(INK))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(INK))
    palette.setColor(QPalette.ColorRole.Button, QColor(CONTROL_FILL))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_FAINT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_TINT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(ACCENT_TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(INK))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#ffffff"))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        qcolor(INK, 0.40),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        qcolor(INK, 0.40),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.WindowText,
        qcolor(INK, 0.40),
    )
    app.setPalette(palette)


# --------------------------------------------------------------------------
# icons — drawn in code, Lucide-like, 1.5 px stroke at 14 px
# --------------------------------------------------------------------------

_VIEWBOX: Final = 24.0
_icon_cache: dict[tuple[str, str, int], QIcon] = {}
_pixmap_cache: dict[tuple[str, str, int], QPixmap] = {}


#: Icons blown up well past 14 px look washed out with a hairline stroke;
#: these grow theirs optically (exponent of the size ratio above 16 px).
_OPTICAL_STROKE: Final[dict[str, float]] = {"doc-plus": 0.45}


def _pen_width(name: str, size: int) -> float:
    """Stroke width in viewbox units — 1.5 logical px, optics aside."""
    width = 36.0 / max(size, 1)
    exponent = _OPTICAL_STROKE.get(name)
    if exponent is not None and size > 16:
        width *= (size / 16.0) ** exponent
    return width


def icon_names() -> tuple[str, ...]:
    """Every icon name :func:`icon` knows about."""
    return tuple(sorted(_PAINTERS))


def pixmap(name: str, color: str = INK, size: int = 14) -> QPixmap:
    """A single icon pixmap, rendered at 2× for crisp downscaling."""
    key = (name, color, size)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached
    dpr = 2.0
    pm = QPixmap(int(round(size * dpr)), int(round(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # the painter already maps logical → device through the pixmap's dpr
    painter.scale(size / _VIEWBOX, size / _VIEWBOX)
    pen = QPen(QColor(color))
    pen.setWidthF(_pen_width(name, size))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    drawer = _PAINTERS.get(name)
    if drawer is not None:
        drawer(painter, QColor(color))
    painter.end()
    _pixmap_cache[key] = pm
    return pm


def icon(name: str, color: str = INK, size: int = 14) -> QIcon:
    """A QIcon drawn in code, so it always matches the palette."""
    key = (name, color, size)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    result = QIcon(pixmap(name, color, size))
    _icon_cache[key] = result
    return result


def _poly(painter: QPainter, *points: tuple[float, float], close: bool = False):
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    if close:
        path.closeSubpath()
    painter.drawPath(path)


def _dot(painter: QPainter, x: float, y: float, r: float, color: QColor):
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(x, y), r, r)
    painter.restore()


def _page(painter: QPainter) -> None:
    """The folded-corner sheet shared by the document icons."""
    _poly(
        painter,
        (6, 3.5),
        (13.5, 3.5),
        (18, 8),
        (18, 20.5),
        (6, 20.5),
        close=True,
    )
    _poly(painter, (13.5, 3.5), (13.5, 8), (18, 8))


def _i_open(p: QPainter, c: QColor) -> None:
    _poly(p, (3.5, 18.5), (3.5, 5), (9, 5), (11.2, 8.3), (18.5, 8.3), (18.5, 11))
    _poly(p, (3.5, 18.5), (6.6, 11), (21.2, 11), (18.5, 18.5), close=True)


def _i_zotero(p: QPainter, c: QColor) -> None:
    for x in (5.0, 8.4, 11.8):
        _poly(p, (x, 4.8), (x, 19.2))
    _poly(p, (15.4, 5.2), (20.0, 19.0))


def _i_zoom_in(p: QPainter, c: QColor) -> None:
    _poly(p, (12, 5), (12, 19))
    _poly(p, (5, 12), (19, 12))


def _i_zoom_out(p: QPainter, c: QColor) -> None:
    _poly(p, (5, 12), (19, 12))


def _i_fit_width(p: QPainter, c: QColor) -> None:
    _poly(p, (3.5, 12), (20.5, 12))
    _poly(p, (7.8, 7.7), (3.5, 12), (7.8, 16.3))
    _poly(p, (16.2, 7.7), (20.5, 12), (16.2, 16.3))


def _i_duplex(p: QPainter, c: QColor) -> None:
    p.drawRoundedRect(QRectF(3.2, 5.2, 8.0, 13.6), 1.4, 1.4)
    p.drawRoundedRect(QRectF(12.8, 5.2, 8.0, 13.6), 1.4, 1.4)
    _poly(p, (12, 5.2), (12, 18.8))


def _i_mirror(p: QPainter, c: QColor) -> None:
    _poly(p, (9.2, 5.5), (5, 5.5), (5, 18.5), (9.2, 18.5))
    _poly(p, (14.8, 5.5), (19, 5.5), (19, 18.5), (14.8, 18.5))
    pen = p.pen()
    dashed = QPen(pen)
    dashed.setStyle(Qt.PenStyle.CustomDashLine)
    dashed.setDashPattern([2.0, 1.6])
    p.setPen(dashed)
    _poly(p, (12, 4.2), (12, 19.8))
    p.setPen(pen)


def _i_punch(p: QPainter, c: QColor) -> None:
    p.drawEllipse(QPointF(12.0, 12.0), 7.6, 7.6)
    _dot(p, 12, 12, 2.4, c)


def _i_print(p: QPainter, c: QColor) -> None:
    _poly(p, (7, 8.6), (7, 3.6), (17, 3.6), (17, 8.6))
    p.drawRoundedRect(QRectF(3.2, 8.6, 17.6, 7.8), 1.8, 1.8)
    p.save()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.fillRect(QRectF(6.4, 12.6, 11.2, 8.0), QColor(0, 0, 0, 255))
    p.restore()
    p.drawRect(QRectF(7.0, 13.2, 10.0, 7.2))


def _i_check(p: QPainter, c: QColor) -> None:
    _poly(p, (4.6, 12.6), (9.6, 17.6), (19.4, 6.8))


def _i_history(p: QPainter, c: QColor) -> None:
    rect = QRectF(4.0, 4.0, 16.0, 16.0)
    p.drawArc(rect, int(100 * 16), int(-315 * 16))
    _poly(p, (3.2, 4.4), (4.3, 9.2), (9.1, 8.0))
    _poly(p, (12, 7.6), (12, 12.3), (15.4, 14.2))


def _i_refresh(p: QPainter, c: QColor) -> None:
    # two arcs with wide gaps, closed by solid heads: still reads as
    # circular arrows when the whole glyph is only 14 px across
    rect = QRectF(4.6, 4.6, 14.8, 14.8)
    # short arcs leave clear air on both sides of each arrowhead, so
    # the two arrows read as separate even at 15 px
    p.drawArc(rect, int(127 * 16), int(103 * 16))  # left side
    p.drawArc(rect, int(307 * 16), int(103 * 16))  # right side
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPolygon(  # top gap, pointing clockwise (right)
        QPointF(10.3, 2.0), QPointF(15.9, 4.9), QPointF(10.3, 7.8)
    )
    p.drawPolygon(  # bottom gap, pointing clockwise (left)
        QPointF(13.7, 16.2), QPointF(8.1, 19.1), QPointF(13.7, 22.0)
    )
    p.restore()


def _i_warning(p: QPainter, c: QColor) -> None:
    _poly(p, (12, 3.8), (21.4, 19.8), (2.6, 19.8), close=True)
    _poly(p, (12, 9.6), (12, 14.2))
    _dot(p, 12, 17.0, 1.05, c)


def _i_alert(p: QPainter, c: QColor) -> None:
    p.drawEllipse(QPointF(12.0, 12.0), 8.2, 8.2)
    _poly(p, (12, 7.2), (12, 12.8))
    _dot(p, 12, 15.8, 1.05, c)


def _i_blocked(p: QPainter, c: QColor) -> None:
    p.drawEllipse(QPointF(12.0, 12.0), 8.2, 8.2)
    _poly(p, (6.4, 17.6), (17.6, 6.4))


def _i_eye_off(p: QPainter, c: QColor) -> None:
    path = QPainterPath(QPointF(2.6, 12.0))
    path.cubicTo(QPointF(6.5, 5.6), QPointF(17.5, 5.6), QPointF(21.4, 12.0))
    path.cubicTo(QPointF(19.8, 14.6), QPointF(17.6, 16.4), QPointF(15.2, 17.3))
    p.drawPath(path)
    path2 = QPainterPath(QPointF(11.0, 17.9))
    path2.cubicTo(QPointF(7.2, 17.4), QPointF(4.2, 15.2), QPointF(2.6, 12.0))
    p.drawPath(path2)
    p.drawArc(QRectF(8.6, 8.6, 6.8, 6.8), int(20 * 16), int(230 * 16))
    _poly(p, (3.6, 3.6), (20.4, 20.4))


def _i_doc(p: QPainter, c: QColor) -> None:
    _page(p)
    for y in (13.2, 17.0):
        _poly(p, (8.8, y), (15.2, y))


def _i_doc_plus(p: QPainter, c: QColor) -> None:
    _page(p)
    pen = QPen(p.pen())  # the plus carries the glyph: draw it heavier
    pen.setWidthF(pen.widthF() * 1.25)
    p.save()
    p.setPen(pen)
    _poly(p, (12, 10.9), (12, 17.9))
    _poly(p, (8.5, 14.4), (15.5, 14.4))
    p.restore()


def _i_chevron_down(p: QPainter, c: QColor) -> None:
    _poly(p, (6.2, 9.4), (12, 15.2), (17.8, 9.4))


def _i_chevron_up(p: QPainter, c: QColor) -> None:
    _poly(p, (6.2, 14.6), (12, 8.8), (17.8, 14.6))


def _i_chevron_right(p: QPainter, c: QColor) -> None:
    _poly(p, (9.4, 6.2), (15.2, 12), (9.4, 17.8))


def _i_chevron_left(p: QPainter, c: QColor) -> None:
    _poly(p, (14.6, 6.2), (8.8, 12), (14.6, 17.8))


def _i_close(p: QPainter, c: QColor) -> None:
    _poly(p, (6.2, 6.2), (17.8, 17.8))
    _poly(p, (17.8, 6.2), (6.2, 17.8))


def _i_search(p: QPainter, c: QColor) -> None:
    p.drawEllipse(QPointF(10.8, 10.8), 6.4, 6.4)
    _poly(p, (15.6, 15.6), (20.2, 20.2))


def _i_info(p: QPainter, c: QColor) -> None:
    p.drawEllipse(QPointF(12.0, 12.0), 8.2, 8.2)
    _poly(p, (12, 11.2), (12, 16.6))
    _dot(p, 12, 8.2, 1.05, c)


def _i_portrait(p: QPainter, c: QColor) -> None:
    p.drawRoundedRect(QRectF(7.0, 3.6, 10.0, 16.8), 1.6, 1.6)


def _i_landscape(p: QPainter, c: QColor) -> None:
    p.drawRoundedRect(QRectF(3.6, 7.0, 16.8, 10.0), 1.6, 1.6)


def _i_single(p: QPainter, c: QColor) -> None:
    _poly(p, (7, 3.8), (14, 3.8), (17.4, 7.2), (17.4, 20.2), (7, 20.2), close=True)


def _i_folder(p: QPainter, c: QColor) -> None:
    _poly(
        p,
        (3.2, 19.4), (3.2, 5.4), (9.2, 5.4), (11.4, 8.2),
        (20.8, 8.2), (20.8, 19.4),
        close=True,
    )


def _i_folder_plus(p: QPainter, c: QColor) -> None:
    _i_folder(p, c)
    _poly(p, (12, 11.2), (12, 16.8))
    _poly(p, (9.2, 14), (14.8, 14))


def _i_list(p: QPainter, c: QColor) -> None:
    for y in (6.5, 12.0, 17.5):
        _poly(p, (8.5, y), (20.5, y))
        _dot(p, 4.6, y, 1.1, c)


def _i_grid(p: QPainter, c: QColor) -> None:
    for x in (4.0, 13.2):
        for y in (4.0, 13.2):
            p.drawRoundedRect(QRectF(x, y, 6.8, 6.8), 1.2, 1.2)


_PAINTERS: Final[dict] = {
    "open": _i_open,
    "zotero": _i_zotero,
    "zoom-in": _i_zoom_in,
    "zoom-out": _i_zoom_out,
    "fit-width": _i_fit_width,
    "duplex": _i_duplex,
    "mirror": _i_mirror,
    "punch": _i_punch,
    "print": _i_print,
    "check": _i_check,
    "history": _i_history,
    "refresh": _i_refresh,
    "warning": _i_warning,
    "alert": _i_alert,
    "blocked": _i_blocked,
    "eye-off": _i_eye_off,
    "doc": _i_doc,
    "doc-plus": _i_doc_plus,
    "chevron-down": _i_chevron_down,
    "chevron-up": _i_chevron_up,
    "chevron-right": _i_chevron_right,
    "chevron-left": _i_chevron_left,
    "close": _i_close,
    "search": _i_search,
    "info": _i_info,
    "portrait": _i_portrait,
    "landscape": _i_landscape,
    "single": _i_single,
    "folder": _i_folder,
    "folder-plus": _i_folder_plus,
    "list": _i_list,
    "grid": _i_grid,
}




# --------------------------------------------------------------------------
# standard-icon proxy style
# --------------------------------------------------------------------------


class AppStyle(QProxyStyle):
    """Feed Qt's standard icons from the theme's glyph set.

    Qt's fallback dialogs (e.g. the widget QFileDialog) take their
    toolbar icons from the system icon theme; on systems without one
    the buttons render blank. This proxy answers those requests with
    theme glyphs so every dialog stays legible and on-palette.
    """

    _MAP = {
        QStyle.StandardPixmap.SP_ArrowBack: "chevron-left",
        QStyle.StandardPixmap.SP_ArrowForward: "chevron-right",
        QStyle.StandardPixmap.SP_ArrowLeft: "chevron-left",
        QStyle.StandardPixmap.SP_ArrowRight: "chevron-right",
        QStyle.StandardPixmap.SP_ArrowUp: "chevron-up",
        QStyle.StandardPixmap.SP_ArrowDown: "chevron-down",
        QStyle.StandardPixmap.SP_FileDialogToParent: "chevron-up",
        QStyle.StandardPixmap.SP_FileDialogNewFolder: "folder-plus",
        QStyle.StandardPixmap.SP_FileDialogListView: "list",
        QStyle.StandardPixmap.SP_FileDialogDetailedView: "grid",
        QStyle.StandardPixmap.SP_FileDialogBack: "chevron-left",
        QStyle.StandardPixmap.SP_DirIcon: "folder",
        QStyle.StandardPixmap.SP_DirOpenIcon: "open",
        QStyle.StandardPixmap.SP_DirClosedIcon: "folder",
        QStyle.StandardPixmap.SP_FileIcon: "doc",
        QStyle.StandardPixmap.SP_BrowserReload: "refresh",
        QStyle.StandardPixmap.SP_DialogCloseButton: "close",
        QStyle.StandardPixmap.SP_MessageBoxWarning: "warning",
        QStyle.StandardPixmap.SP_MessageBoxInformation: "info",
        QStyle.StandardPixmap.SP_MessageBoxCritical: "alert",
    }

    def standardIcon(self, sp, option=None, widget=None):  # noqa: N802
        name = self._MAP.get(sp)
        if name is not None:
            return icon(name, INK, 16)
        return super().standardIcon(sp, option, widget)

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802
        # fields opting in via the property hide the text caret while
        # their content is selected (select-all on click looks calmer)
        if (
            metric == QStyle.PixelMetric.PM_TextCursorWidth
            and widget is not None
            and widget.property("hideCaretWhenSelected")
            and hasattr(widget, "hasSelectedText")
            and widget.hasSelectedText()
        ):
            return 0
        return super().pixelMetric(metric, option, widget)


# --------------------------------------------------------------------------
# stylesheet
# --------------------------------------------------------------------------

_GLYPH_SPECS: Final = (
    # name          icon          colour        size  canvas
    ("check", "check", ACCENT, 11, 16),
    ("check-disabled", "check", "#9c9898", 11, 16),
    ("chevron-down", "chevron-down", TEXT_MUTED, 12, 12),
    ("chevron-down-disabled", "chevron-down", "#a9a5a5", 12, 12),
    ("chevron-right", "chevron-right", TEXT_MUTED, 10, 10),
    ("chevron-down-small", "chevron-down", TEXT_MUTED, 10, 10),
    ("spin-up", "chevron-up", TEXT_MUTED, 10, 10),
    ("spin-down", "chevron-down", TEXT_MUTED, 10, 10),
)


def _glyph_dir() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    if not base:
        import tempfile

        base = tempfile.gettempdir()
    path = Path(base) / "theme-glyphs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_glyphs() -> dict[str, str]:
    """Render the few glyphs QSS can only reference as image files.

    Qt picks up the "@2x" variants automatically on hi-dpi screens.
    Failure is survivable: the stylesheet then simply omits the images.
    """
    try:
        directory = _glyph_dir()
    except Exception:  # noqa: BLE001
        return {}
    urls: dict[str, str] = {}
    for key, name, color, size, canvas in _GLYPH_SPECS:
        try:
            for scale, suffix in ((1, ""), (2, "@2x")):
                pm = QPixmap(canvas * scale, canvas * scale)
                pm.fill(QColor(0, 0, 0, 0))
                painter = QPainter(pm)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                glyph = pixmap(name, color, size * scale)
                painter.drawPixmap(
                    QPointF(
                        (canvas * scale - size * scale) / 2.0,
                        (canvas * scale - size * scale) / 2.0,
                    ),
                    glyph,
                )
                painter.end()
                target = directory / f"{key}{suffix}.png"
                if not pm.save(str(target), "PNG"):
                    raise OSError(target)
            urls[key] = (directory / f"{key}.png").as_posix()
        except Exception:  # noqa: BLE001
            return {}
    return urls


def _image(urls: dict[str, str], key: str) -> str:
    path = urls.get(key)
    return f'url("{path}")' if path else "none"


_QSS = Template(
    """
/* ---------------------------------------------------------------- ground */
QMainWindow, QDialog, QMessageBox, QWizard {
    background-color: $ground;
}
QWidget {
    color: $ink;
}
QWidget:disabled {
    color: $text_disabled;
}
QMainWindow::separator, QSplitter::handle {
    background-color: $hairline_solid;
    width: 1px;
    height: 1px;
}
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QFrame[frameShape="4"] {            /* HLine separator */
    background-color: $hairline_solid;
    color: $hairline_solid;
    border: none;
    max-height: 1px;
}
QFrame[frameShape="5"] {            /* VLine separator */
    background-color: $hairline_solid;
    color: $hairline_solid;
    border: none;
    max-width: 1px;
}

/* ------------------------------------------------------------- controls */
QComboBox, QLineEdit, QAbstractSpinBox, QPushButton, QToolButton {
    background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: ${radius}px;
    min-height: 28px;
    padding: 0px 10px;
    color: $ink;
    selection-background-color: $accent_tint;
    selection-color: $accent_text;
}
QPushButton, QToolButton {
    padding: 0px 14px;
}
QComboBox:hover, QLineEdit:hover, QAbstractSpinBox:hover {
    background-color: $accent_tint;
    border-color: $accent;
}
QPushButton:hover, QToolButton:hover {
    background-color: $accent_tint;
    border-color: $accent;
    color: $accent_text;
}
QPushButton:pressed, QToolButton:pressed, QPushButton:checked {
    background-color: $accent_tint_deep;
    border-color: $accent;
    color: $accent_text;
}
QComboBox:focus, QLineEdit:focus, QAbstractSpinBox:focus,
QPushButton:focus, QToolButton:focus {
    border: 2px solid $accent;
    min-height: 26px;
    padding: 0px 9px;
}
QPushButton:focus, QToolButton:focus {
    padding: 0px 13px;
}
QComboBox:disabled, QLineEdit:disabled, QAbstractSpinBox:disabled,
QPushButton:disabled, QToolButton:disabled {
    background-color: $disabled_fill;
    border-color: $hairline_soft;
    color: $text_disabled;
}
QLineEdit[invalid="true"], QLineEdit[invalid="true"]:focus,
QComboBox[invalid="true"], QAbstractSpinBox[invalid="true"] {
    border: 1px solid $error_border;
    background-color: $paper;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: $img_chevron_down;
    width: 12px;
    height: 12px;
}
QComboBox::down-arrow:disabled {
    image: $img_chevron_down_disabled;
}
QComboBox QAbstractItemView {
    background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: ${radius}px;
    padding: 4px;
    outline: none;
    selection-background-color: $accent_tint;
    selection-color: $accent_text;
}
QComboBox QAbstractItemView::item {
    min-height: 24px;
    padding: 2px 6px;
    border-radius: 3px;
    color: $ink;
}

QAbstractSpinBox {
    padding-right: 18px;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border;
    width: 18px;
    border: none;
    background: transparent;
}
QAbstractSpinBox::up-button {
    subcontrol-position: top right;
    /* pull the glyph toward the field's middle line */
    padding-top: 6px;
}
QAbstractSpinBox::down-button {
    subcontrol-position: bottom right;
    padding-bottom: 6px;
}
QAbstractSpinBox::up-arrow {
    image: $img_spin_up;
    width: 10px;
    height: 10px;
}
QAbstractSpinBox::down-arrow {
    image: $img_spin_down;
    width: 10px;
    height: 10px;
}

QCheckBox, QRadioButton {
    background: transparent;
    spacing: 8px;
    padding: 2px 0px;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $hairline;
    border-radius: 3px;
    background-color: $control_fill;
}
QRadioButton::indicator {
    border-radius: 8px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: $accent;
    background-color: $accent_tint;
}
QCheckBox::indicator:focus, QRadioButton::indicator:focus {
    /* same geometry as the checked state: accent border and tint,
       no thickening — focus just looks 'primed', minus the check */
    border: 1px solid $accent;
    background-color: $accent_tint;
}
QCheckBox::indicator:checked {
    border: 1px solid $accent;
    background-color: $accent_tint;
    image: $img_check;
}
QRadioButton::indicator:checked {
    border: 5px solid $accent;
    background-color: $accent_tint;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background-color: $disabled_fill;
    border-color: $hairline_soft;
}
QCheckBox::indicator:checked:disabled {
    image: $img_check_disabled;
}

/* -------------------------------------------------- primary print button */
QPushButton#printButton {
    min-height: 40px;
    padding: 0px 16px;
    border: 2px solid $accent;
    border-radius: 5px;
    background-color: $control_fill;
    color: $accent_text;
}
QPushButton#printButton:hover {
    background-color: $accent_tint;
}
QPushButton#printButton:pressed {
    background-color: $accent_tint_deep;
}
QPushButton#printButton:focus {
    border: 2px solid $accent;
    background-color: $accent_tint;
    padding: 0px 16px;
}
QPushButton#printButton:disabled {
    background-color: $disabled_fill;
    border: 2px solid $hairline_soft;
    color: $text_disabled;
}
QPushButton#printButton[done="true"] {
    background-color: $accent_tint;
    color: $accent_text;
    border: 2px solid $accent;
}

/* ------------------------------------------------------- segmented group */
QPushButton#segment {
    background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: 0px;
    min-height: 28px;
    padding: 0px 12px;
    color: $ink;
}
QPushButton#segment[edge="first"], QPushButton#segment[edge="only"] {
    border-top-left-radius: ${radius}px;
    border-bottom-left-radius: ${radius}px;
}
QPushButton#segment[edge="last"], QPushButton#segment[edge="only"] {
    border-top-right-radius: ${radius}px;
    border-bottom-right-radius: ${radius}px;
}
QPushButton#segment:hover {
    background-color: $accent_tint;
    border-color: $accent;
    color: $accent_text;
}
QPushButton#segment:checked {
    background-color: $accent_tint;
    border: 1px solid $accent;
    color: $accent_text;
}
QPushButton#segment:focus {
    border: 1px solid $accent;
    padding: 0px 12px;
    min-height: 28px;
}
QPushButton#segment:checked:focus {
    background-color: $accent_tint_deep;
}
QPushButton#segment:disabled {
    background-color: $disabled_fill;
    border-color: $hairline_soft;
    color: $text_disabled;
}
QPushButton#segment:checked:disabled {
    background-color: #efeae4;
    color: $text_disabled;
}

/* ---------------------------------------------------- collapsible header */
QToolButton#sectionHeader {
    background: transparent;
    border: none;
    border-radius: 3px;
    min-height: 22px;
    padding: 3px 4px 3px 0px;
    color: $text_muted;
}
QToolButton#sectionHeader:hover {
    background: transparent;
    border: none;
    color: $accent_text;
}
QToolButton#sectionHeader:focus {
    background-color: rgba(182, 130, 53, 0.12);
    border: none;
    padding: 3px 4px 3px 0px;
}
QFileDialog QToolButton {
    padding: 0px;
    min-height: 24px;
    min-width: 24px;
}
QToolButton#chipClose {
    background: transparent;
    border: none;
    border-radius: 8px;
    min-height: 16px;
    padding: 0px;
}
QToolButton#chipClose:hover {
    background-color: rgba(45, 43, 43, 0.10);
}
QWidget#chipSep {
    background-color: $hairline_solid;
}

/* ------------------------------------------------------- labels & chips */
QLabel {
    background: transparent;
}
QLabel#sectionLabel {
    color: $text_muted;
    font-size: ${section_pt}pt;
}
QLabel#hintLabel {
    color: $text_muted;
    font-size: ${small_pt}pt;
}
QLabel#errorLabel {
    color: $error;
    font-size: ${small_pt}pt;
}
QLabel#valueLabel {
    color: $ink;
}
QLabel#metaLabel {
    color: $text_faint;
    font-size: ${small_pt}pt;
}
QLabel#badge {
    color: $accent_text;
    background-color: $accent_tint;
    border: 1px solid $accent;
    border-radius: 9px;
    padding: 1px 8px;
    font-size: ${small_pt}pt;
}
QLabel#chip, QLabel#chipOk, QLabel#chipError {
    border-radius: 13px;
    padding: 4px 12px;
    font-size: ${small_pt}pt;
}
QLabel#chip {
    background-color: $control_fill;
    border: 1px solid $hairline;
    color: $ink;
}
QLabel#chipOk {
    background-color: $accent_tint;
    border: 1px solid $accent;
    color: $accent_text;
}
QLabel#chipError {
    background-color: $error_tint;
    border: 1px solid $error_border;
    color: $error;
}

/* ------------------------------------------------------------ surfaces */
QWidget#headerBar, QWidget#legendBar {
    background-color: $ground;
}
QWidget#headerBar {
    border-bottom: 1px solid $hairline_soft;
}
QWidget#legendBar {
    border-top: 1px solid $hairline_soft;
}
QFrame#emptyCard {
    background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: 8px;
}
QFrame#pillToolbar {
    background-color: #fbf9f9;
    border: 1px solid $hairline;
    border-radius: 19px;
}
QFrame#pillToolbar QPushButton, QFrame#pillToolbar QToolButton {
    background: transparent;
    border: none;
    border-radius: 15px;
    min-height: 26px;
    padding: 0px 10px;
    color: $ink;
}
QFrame#pillToolbar QPushButton:hover, QFrame#pillToolbar QToolButton:hover {
    background-color: $accent_tint;
    color: $accent_text;
}
QFrame#pillToolbar QPushButton:disabled {
    background: transparent;
    color: $text_disabled;
}
QGroupBox {
    /* flattened: no frame at all — sections carry a label instead */
    border: none;
    margin: 18px 0px 0px 0px;
    padding: 0px;
    color: $text_muted;
    font-size: ${section_pt}pt;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px;
    padding: 0px;
}

/* --------------------------------------------------------------- menus */
QMenuBar {
    background-color: $ground;
    border-bottom: 1px solid $hairline_soft;
    padding: 2px 4px;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 10px;
    border-radius: 3px;
    color: $ink;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background-color: $accent_tint;
    color: $accent_text;
}
QMenu {
    background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: 6px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 22px 6px 14px;
    border-radius: 3px;
    color: $ink;
}
QMenu::item:selected {
    background-color: $accent_tint;
    color: $accent_text;
}
QMenu::item:disabled {
    color: $text_disabled;
}
QMenu::separator {
    height: 1px;
    background-color: $hairline_soft;
    margin: 5px 8px;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
    left: 6px;
}

/* ------------------------------------------------------------ tooltips */
QToolTip {
    background-color: $ink;
    color: #ffffff;
    border: none;
    border-radius: ${radius}px;
    padding: 6px 9px;
    font-size: ${small_pt}pt;
    opacity: 240;
}

/* ---------------------------------------------------------- scrollbars */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: rgba(45, 43, 43, 0.22);
    border-radius: 4px;
    min-height: 28px;
    min-width: 28px;
}
QScrollBar::handle:hover {
    background-color: rgba(45, 43, 43, 0.36);
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* --------------------------------------------------------------- lists */
QTreeView, QTreeWidget, QListView, QListWidget, QTableView {
    background-color: $control_fill;
    alternate-background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: ${radius}px;
    outline: none;
    show-decoration-selected: 1;
}
QTreeView::item, QListView::item {
    height: 26px;
    padding: 3px 6px;
    border-left: 2px solid transparent;
    border-bottom: 1px solid $hairline_soft;
    color: $ink;
}
QTreeView::item:hover, QListView::item:hover {
    background-color: rgba(182, 130, 53, 0.09);
}
QTreeView::item:selected, QListView::item:selected,
QTreeView::item:selected:active, QListView::item:selected:active {
    background-color: $accent_tint;
    border-left: 2px solid $accent;
    color: $accent_text;
}
QTreeView::branch {
    background: transparent;
    border-image: none;
}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {
    image: $img_chevron_right;
}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {
    image: $img_chevron_down_small;
}
QHeaderView {
    background-color: $ground;
    border: none;
}
QHeaderView::section {
    background-color: $ground;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $hairline;
    border-right: 1px solid $hairline_soft;
    padding: 6px 8px;
    font-size: ${small_pt}pt;
}
QHeaderView::section:last {
    border-right: none;
}

/* ----------------------------------------------------------- statusbar */
QStatusBar {
    background-color: $ground;
    border-top: 1px solid $hairline_soft;
    color: $text_muted;
    font-size: ${small_pt}pt;
}
QStatusBar::item {
    border: none;
}
QStatusBar QLabel {
    color: $text_muted;
    font-size: ${small_pt}pt;
}

/* ----------------------------------------------------------- text view */
QTextBrowser, QTextEdit, QPlainTextEdit {
    background-color: $control_fill;
    border: 1px solid $hairline;
    border-radius: ${radius}px;
    padding: 8px;
    selection-background-color: $accent_tint;
    selection-color: $accent_text;
}
QDialogButtonBox {
    button-layout: 2;
}
"""
)


def stylesheet() -> str:
    """The application-wide QSS.

    Call after :func:`load_fonts` and after a QApplication exists (the
    checkbox tick and chevrons are rendered to PNGs on first use).
    """
    urls = _write_glyphs()
    return _QSS.substitute(
        ground=GROUND,
        control_fill=CONTROL_FILL,
        canvas=CANVAS,
        paper=PAPER,
        ink=INK,
        accent=ACCENT,
        accent_tint=ACCENT_TINT,
        accent_tint_deep=ACCENT_TINT_DEEP,
        accent_text=ACCENT_TEXT,
        text_muted=TEXT_MUTED,
        text_faint=TEXT_FAINT,
        text_disabled=TEXT_DISABLED,
        disabled_fill=DISABLED_FILL,
        hairline=HAIRLINE,
        hairline_soft=HAIRLINE_SOFT,
        hairline_solid=HAIRLINE_SOLID,
        error=ERROR,
        error_border=ERROR_BORDER,
        error_tint=ERROR_TINT,
        radius=RADIUS,
        small_pt=SMALL_POINT_SIZE,
        section_pt=SECTION_POINT_SIZE,
        img_check=_image(urls, "check"),
        img_check_disabled=_image(urls, "check-disabled"),
        img_chevron_down=_image(urls, "chevron-down"),
        img_chevron_down_disabled=_image(urls, "chevron-down-disabled"),
        img_chevron_down_small=_image(urls, "chevron-down-small"),
        img_chevron_right=_image(urls, "chevron-right"),
        img_spin_up=_image(urls, "spin-up"),
        img_spin_down=_image(urls, "spin-down"),
    )
