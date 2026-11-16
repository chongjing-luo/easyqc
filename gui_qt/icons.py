"""Original line glyphs and the EasyQC brand mark, painted with QtGui only.

Every glyph is drawn on a 24 × 24 logical grid with ``QPainter`` so the
presentation needs no image files, no QtSvg module and no extra Qt plugins in
packaged builds.  Pixmaps are rendered at the requested device-pixel ratio so
icons stay crisp on high-DPI screens.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

GRID = 24.0
DEFAULT_STROKE = 1.8

_Painter = Callable[[QPainter], None]


def _polyline(painter: QPainter, *points: tuple[float, float]) -> None:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    painter.drawPath(path)


def _polygon(painter: QPainter, *points: tuple[float, float]) -> None:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    path.closeSubpath()
    painter.drawPath(path)


def _line(painter: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _folder(p: QPainter) -> None:
    _polygon(p, (3.2, 6.0), (9.4, 6.0), (11.4, 8.4), (20.8, 8.4), (20.8, 19.0), (3.2, 19.0))
    _line(p, 3.2, 11.0, 20.8, 11.0)


def _import(p: QPainter) -> None:
    _polyline(p, (4.0, 13.5), (4.0, 19.5), (20.0, 19.5), (20.0, 13.5))
    _line(p, 12.0, 4.0, 12.0, 14.6)
    _polyline(p, (8.0, 10.6), (12.0, 14.6), (16.0, 10.6))


def _table(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 4.5, 17.0, 15.0), 2.4, 2.4)
    _line(p, 3.5, 9.5, 20.5, 9.5)
    _line(p, 3.5, 14.5, 20.5, 14.5)
    _line(p, 9.5, 4.5, 9.5, 19.5)


def _brace(path: QPainterPath, mirror: bool) -> None:
    def x(value: float) -> float:
        return GRID - value if mirror else value

    path.moveTo(x(9.5), 4.0)
    path.lineTo(x(8.4), 4.0)
    path.quadTo(x(6.4), 4.0, x(6.4), 6.0)
    path.lineTo(x(6.4), 10.0)
    path.quadTo(x(6.4), 12.0, x(4.2), 12.0)
    path.quadTo(x(6.4), 12.0, x(6.4), 14.0)
    path.lineTo(x(6.4), 18.0)
    path.quadTo(x(6.4), 20.0, x(8.4), 20.0)
    path.lineTo(x(9.5), 20.0)


def _braces(p: QPainter) -> None:
    path = QPainterPath()
    _brace(path, False)
    _brace(path, True)
    p.drawPath(path)
    previous = p.brush()
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(12.0, 12.0), 1.15, 1.15)
    p.setBrush(previous)


def _layers(p: QPainter) -> None:
    _polygon(p, (12.0, 3.6), (20.8, 8.0), (12.0, 12.4), (3.2, 8.0))
    _polyline(p, (3.2, 12.2), (12.0, 16.6), (20.8, 12.2))
    _polyline(p, (3.2, 16.2), (12.0, 20.6), (20.8, 16.2))


def _clipboard_check(p: QPainter) -> None:
    board = QPainterPath(QPointF(8.8, 4.6))
    board.lineTo(7.2, 4.6)
    board.quadTo(5.0, 4.6, 5.0, 6.8)
    board.lineTo(5.0, 18.8)
    board.quadTo(5.0, 21.0, 7.2, 21.0)
    board.lineTo(16.8, 21.0)
    board.quadTo(19.0, 21.0, 19.0, 18.8)
    board.lineTo(19.0, 6.8)
    board.quadTo(19.0, 4.6, 16.8, 4.6)
    board.lineTo(15.2, 4.6)
    p.drawPath(board)
    p.drawRoundedRect(QRectF(8.8, 2.9, 6.4, 3.4), 1.1, 1.1)
    _polyline(p, (8.8, 13.6), (11.1, 15.9), (15.4, 11.6))


def _sliders(p: QPainter) -> None:
    _line(p, 4.0, 7.5, 12.6, 7.5)
    _line(p, 17.4, 7.5, 20.0, 7.5)
    p.drawEllipse(QPointF(15.0, 7.5), 2.3, 2.3)
    _line(p, 4.0, 16.5, 6.6, 16.5)
    _line(p, 11.4, 16.5, 20.0, 16.5)
    p.drawEllipse(QPointF(9.0, 16.5), 2.3, 2.3)


def _panel(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 4.5, 17.0, 15.0), 2.4, 2.4)
    _line(p, 9.5, 4.5, 9.5, 19.5)


def _panel_close(p: QPainter) -> None:
    _panel(p)
    _polyline(p, (16.0, 9.4), (13.4, 12.0), (16.0, 14.6))


def _panel_open(p: QPainter) -> None:
    _panel(p)
    _polyline(p, (13.4, 9.4), (16.0, 12.0), (13.4, 14.6))


def _chevron_down(p: QPainter) -> None:
    _polyline(p, (6.0, 9.0), (12.0, 15.0), (18.0, 9.0))


def _chevron_up(p: QPainter) -> None:
    _polyline(p, (6.0, 15.0), (12.0, 9.0), (18.0, 15.0))


def _chevron_right(p: QPainter) -> None:
    _polyline(p, (9.0, 6.0), (15.0, 12.0), (9.0, 18.0))


def _check(p: QPainter) -> None:
    _polyline(p, (5.0, 12.6), (9.8, 17.4), (19.0, 7.2))


def _dash(p: QPainter) -> None:
    _line(p, 6.0, 12.0, 18.0, 12.0)


def _dot(p: QPainter) -> None:
    previous = p.brush()
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(12.0, 12.0), 4.6, 4.6)
    p.setBrush(previous)


def _play(p: QPainter) -> None:
    _polygon(p, (8.0, 5.6), (18.6, 12.0), (8.0, 18.4))


def _keyboard(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(2.8, 6.2, 18.4, 11.6), 2.4, 2.4)
    previous = p.brush()
    p.setBrush(p.pen().color())
    for x in (7.0, 10.3, 13.7, 17.0):
        p.drawEllipse(QPointF(x, 10.0), 0.6, 0.6)
    p.setBrush(previous)
    _line(p, 8.0, 14.2, 16.0, 14.2)


GLYPHS: dict[str, _Painter] = {
    "braces": _braces,
    "check": _check,
    "chevron-down": _chevron_down,
    "chevron-right": _chevron_right,
    "chevron-up": _chevron_up,
    "clipboard-check": _clipboard_check,
    "dash": _dash,
    "dot": _dot,
    "folder": _folder,
    "import": _import,
    "keyboard": _keyboard,
    "layers": _layers,
    "panel-close": _panel_close,
    "panel-open": _panel_open,
    "play": _play,
    "sliders": _sliders,
    "table": _table,
}


def _device_pixel_ratio(dpr: float | None) -> float:
    if dpr is not None and dpr > 0:
        return float(dpr)
    app = QGuiApplication.instance()
    if app is None:
        return 1.0
    screen = QGuiApplication.primaryScreen()
    return max(1.0, float(screen.devicePixelRatio())) if screen else 1.0


def render_glyph_image(
    name: str,
    color: str,
    pixel_size: int,
    *,
    stroke: float = DEFAULT_STROKE,
) -> QImage:
    """Render one glyph to a transparent ARGB image of exact pixel size."""

    try:
        painter_function = GLYPHS[name]
    except KeyError as exc:
        raise KeyError(f"Unknown EasyQC glyph: {name}") from exc
    image = QImage(pixel_size, pixel_size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale = pixel_size / GRID
        painter.scale(scale, scale)
        pen = QPen(QColor(color))
        pen.setWidthF(stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter_function(painter)
    finally:
        painter.end()
    return image


def glyph_pixmap(
    name: str,
    color: str,
    size: int = 18,
    *,
    dpr: float | None = None,
    stroke: float = DEFAULT_STROKE,
    pad_right: int = 0,
) -> QPixmap:
    """Return one glyph pixmap; ``pad_right`` adds transparent trailing space."""

    ratio = _device_pixel_ratio(dpr)
    pixels = max(1, round(size * ratio))
    image = render_glyph_image(name, color, pixels, stroke=stroke)
    if pad_right > 0:
        padded = QImage(
            pixels + round(pad_right * ratio),
            pixels,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        padded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(padded)
        painter.drawImage(0, 0, image)
        painter.end()
        image = padded
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def glyph_icon(
    name: str,
    color: str,
    *,
    active_color: str | None = None,
    selected_color: str | None = None,
    disabled_color: str | None = None,
    size: int = 18,
    pad_right: int = 0,
) -> QIcon:
    """Return a multi-state icon whose modes follow the surrounding palette."""

    icon = QIcon()
    states = (
        (QIcon.Mode.Normal, color),
        (QIcon.Mode.Active, active_color or color),
        (QIcon.Mode.Selected, selected_color or active_color or color),
        (QIcon.Mode.Disabled, disabled_color or color),
    )
    for ratio in (1.0, 2.0):
        for mode, mode_color in states:
            for state in (QIcon.State.Off, QIcon.State.On):
                icon.addPixmap(
                    glyph_pixmap(
                        name,
                        mode_color,
                        size,
                        dpr=ratio,
                        pad_right=pad_right,
                    ),
                    mode,
                    state,
                )
    return icon


BRAND_TEAL = "#1FC2AE"
BRAND_BLUE = "#2F5FD8"


def paint_brand_mark(painter: QPainter, rect: QRectF) -> None:
    """Paint the EasyQC mark: an axial slice ring crossed by a check."""

    side = min(rect.width(), rect.height())
    box = QRectF(
        rect.center().x() - side / 2,
        rect.center().y() - side / 2,
        side,
        side,
    )
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        gradient = QLinearGradient(box.topLeft(), box.bottomRight())
        gradient.setColorAt(0.0, QColor(BRAND_TEAL))
        gradient.setColorAt(1.0, QColor(BRAND_BLUE))
        radius = side * 0.27
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(box, radius, radius)

        glow = QRadialGradient(
            QPointF(box.left() + side * 0.28, box.top() + side * 0.2),
            side * 0.75,
        )
        glow.setColorAt(0.0, QColor(255, 255, 255, 70))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(QBrush(glow))
        painter.drawRoundedRect(box, radius, radius)

        center = box.center()
        ring_pen = QPen(QColor(255, 255, 255, 120))
        ring_pen.setWidthF(side * 0.065)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, side * 0.29, side * 0.25)
        inner_pen = QPen(QColor(255, 255, 255, 70))
        inner_pen.setWidthF(side * 0.045)
        painter.setPen(inner_pen)
        painter.drawEllipse(center, side * 0.16, side * 0.135)

        check_pen = QPen(QColor("#FFFFFF"))
        check_pen.setWidthF(side * 0.095)
        check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(check_pen)
        check = QPainterPath(QPointF(box.left() + side * 0.31, box.top() + side * 0.53))
        check.lineTo(box.left() + side * 0.45, box.top() + side * 0.665)
        check.lineTo(box.left() + side * 0.715, box.top() + side * 0.375)
        painter.drawPath(check)
    finally:
        painter.restore()


def brand_pixmap(size: int, *, dpr: float | None = None) -> QPixmap:
    ratio = _device_pixel_ratio(dpr)
    pixels = max(1, round(size * ratio))
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        paint_brand_mark(painter, QRectF(0, 0, pixels, pixels))
    finally:
        painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def application_icon() -> QIcon:
    """Return the multi-resolution window/taskbar icon."""

    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        icon.addPixmap(brand_pixmap(size, dpr=1.0))
    return icon


__all__ = [
    "BRAND_BLUE",
    "BRAND_TEAL",
    "GLYPHS",
    "application_icon",
    "brand_pixmap",
    "glyph_icon",
    "glyph_pixmap",
    "paint_brand_mark",
    "render_glyph_image",
]
