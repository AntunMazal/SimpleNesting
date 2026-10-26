"""Zoomable, pannable view for polygons with holes (QGraphicsView, world mm, y up)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView

from ..models import Ring
from ..nesting.common import transform_ring
from . import theme


@dataclass
class Shape:
    key: object
    outer: Ring
    holes: list[Ring] = field(default_factory=list)
    fill: str = "#555555"
    outline: str = "#dddddd"


def _polygon(ring: Ring) -> QPolygonF:
    return QPolygonF([QPointF(x, -y) for x, y in ring])        # scene y points down, world y up


def _pen(color: str, width: float = 1.0, dash: bool = False) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCosmetic(True)                                       # constant on-screen width at any zoom
    if dash:
        pen.setStyle(Qt.PenStyle.DashLine)
    return pen


class _ShapeItem(QGraphicsPathItem):
    def __init__(self, key, path: QPainterPath, view: "SheetView"):
        super().__init__(path)
        self.key, self._view = key, view
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event):
        self._view.hovered.emit(self.key)

    def hoverLeaveEvent(self, event):
        self._view.hovered.emit(None)


class SheetView(QGraphicsView):
    hovered = Signal(object)        # shape key or None
    clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)          # keep a Python reference, or the scene is collected
        self.setScene(self._scene)
        self.setBackgroundBrush(QBrush(QColor(theme.CANVAS_BG)))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setSceneRect(-1e6, -1e6, 2e6, 2e6)                 # unlimited panning
        self.setMouseTracking(True)
        self._items: dict[object, _ShapeItem] = {}
        self._base: dict[object, QPen] = {}
        self._fill: dict[object, QColor] = {}
        self._hl: set = set()
        self._blocked: set = set()
        self._marker = None
        self._autofit = True
        self._press: QPointF | None = None
        self._moved = False

    # ------------------------------------------------------------- content ---
    def set_content(self, shapes: list[Shape], frame: tuple[float, float] | None = None,
                    lines: list[tuple[Ring, str, float]] | None = None,
                    points: list[tuple[float, float]] | None = None, fit: bool = True,
                    frames: list[tuple[float, float, float, float, str]] | None = None) -> None:
        scene = self.scene()
        scene.clear()
        self._items.clear()
        self._base.clear()
        self._fill.clear()
        self._hl, self._blocked, self._marker = set(), set(), None
        if frame:
            r = scene.addRect(QRectF(0, -frame[1], frame[0], frame[1]), _pen("#8a847d", 1, dash=True))
            r.setZValue(-1)
        for fx, fy, fw, fh, text in frames or []:                # several sheets in one scene (Edit tab)
            r = scene.addRect(QRectF(fx, -(fy + fh), fw, fh), _pen("#8a847d", 1, dash=True))
            r.setZValue(-1)
            if text:
                t = scene.addSimpleText(text)
                t.setBrush(QBrush(QColor("#aaa39b")))
                t.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                t.setPos(fx, -(fy + fh))
                t.setTransform(QTransform.fromTranslate(0, -22))
        for s in shapes:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            path.addPolygon(_polygon(s.outer))
            path.closeSubpath()
            for h in s.holes:
                path.addPolygon(_polygon(h))
                path.closeSubpath()
            item = _ShapeItem(s.key, path, self)
            item.setBrush(QBrush(QColor(s.fill)))
            pen = _pen(s.outline)
            item.setPen(pen)
            scene.addItem(item)
            self._items[s.key] = item
            self._base[s.key] = pen
            self._fill[s.key] = QColor(s.fill)
        for ring, color, width in lines or []:
            path = QPainterPath()
            poly = _polygon(ring)
            path.addPolygon(poly)
            item = scene.addPath(path, _pen(color, width))
            item.setZValue(2)
        for x, y in points or []:
            e = scene.addEllipse(QRectF(-5, -5, 10, 10), _pen("#ff4d4d", 2), QBrush(Qt.BrushStyle.NoBrush))
            e.setFlag(e.GraphicsItemFlag.ItemIgnoresTransformations)    # fixed 10 px circle
            e.setPos(x, -y)
            e.setZValue(3)
        if fit:
            self._autofit = True
            self.fit()

    def _restyle(self, k) -> None:
        item = self._items.get(k)
        if item is None:
            return
        on = k in self._hl
        bad = k in self._blocked                                  # overlapping while dragging: red
        item.setPen(_pen("#ff3b30", 3) if bad else (_pen("#ffffff", 3) if on else self._base[k]))
        item.setBrush(QBrush(QColor("#b5362c") if bad else (self._fill[k].lighter(165) if on else self._fill[k])))
        item.setZValue(2 if bad else (1 if on else 0))

    def highlight(self, keys=()) -> None:
        """Light up any number of shapes (white outline, brighter fill); pass nothing to clear."""
        self._hl = set(keys)
        for k in self._items:
            self._restyle(k)

    def set_blocked(self, key, on: bool) -> None:
        """Red shape while the dragged part overlaps another part or leaves the sheet margin."""
        (self._blocked.add if on else self._blocked.discard)(key)
        self._restyle(key)

    def update_shape(self, key, outer: Ring, holes: list[Ring]) -> None:
        """Replace the geometry of one shape (during a drag) without rebuilding the scene."""
        item = self._items.get(key)
        if item is None:
            return
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addPolygon(_polygon(outer))
        path.closeSubpath()
        for h in holes:
            path.addPolygon(_polygon(h))
            path.closeSubpath()
        item.setPath(path)

    def set_marker(self, x: float | None, y: float | None = None) -> None:
        """Pivot marker (circle + cross of constant screen size); None removes it."""
        if self._marker is not None:
            self.scene().removeItem(self._marker)
            self._marker = None
        if x is None:
            return
        path = QPainterPath()
        path.addEllipse(QRectF(-6, -6, 12, 12))
        path.moveTo(-11, 0)
        path.lineTo(11, 0)
        path.moveTo(0, -11)
        path.lineTo(0, 11)
        self._marker = self.scene().addPath(path, _pen("#ffd166", 1.5))
        self._marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._marker.setPos(x, -y)
        self._marker.setZValue(5)
        self._marker.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _shape_at(self, point):
        """Topmost part shape under a viewport point; markers, frames and labels never catch the mouse."""
        return next((it for it in self.items(point) if isinstance(it, _ShapeItem)), None)

    def key_at(self, x: float, y: float):
        """Key of the shape under a sheet point (x, y up), or None."""
        item = next((it for it in self.scene().items(QPointF(x, -y)) if isinstance(it, _ShapeItem)), None)
        return item.key if item is not None else None

    # -------------------------------------------------------------- viewing ---
    def fit(self) -> None:
        self._autofit = True
        rect = self.scene().itemsBoundingRect()
        if rect.isEmpty():
            return
        m = 0.03 * max(rect.width(), rect.height(), 1.0)
        self.resetTransform()
        self.fitInView(rect.adjusted(-m, -m, m, m), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        self._autofit = False
        self.scale(factor, factor)

    def focus(self, x: float, y: float, w: float, h: float) -> None:
        """Zoom to a rectangle given in sheet coordinates (y up)."""
        m = 0.04 * max(w, h)
        self._autofit = False
        self.resetTransform()
        self.fitInView(QRectF(x - m, -(y + h) - 3 * m, w + 2 * m, h + 4 * m), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_in(self) -> None:
        self.zoom(1.4)

    def zoom_out(self) -> None:
        self.zoom(1 / 1.4)

    def render_image(self, width: int = 2000):
        from PySide6.QtGui import QImage
        rect = self.scene().itemsBoundingRect().adjusted(-20, -20, 20, 20)
        img = QImage(width, max(1, int(width * rect.height() / max(rect.width(), 1))), QImage.Format.Format_ARGB32)
        img.fill(QColor(theme.CANVAS_BG))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.scene().render(p, QRectF(img.rect()), rect)
        p.end()
        return img

    # --------------------------------------------------------------- events ---
    def wheelEvent(self, event):
        self.zoom(1.15 ** (event.angleDelta().y() / 120.0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._autofit:
            self.fit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press, self._moved = event.position(), False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            d = event.position() - self._press
            if abs(d.x()) + abs(d.y()) > 3:
                self._moved = True
            if self._moved:
                self._autofit = False
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
                self._press = event.position()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None and not self._moved:
            item = self._shape_at(event.position().toPoint())
            if item is not None:
                self.clicked.emit(item.key)
        self._press = None
        super().mouseReleaseEvent(event)


def show_part(view: SheetView, part, rotate_deg: float = 0.0) -> None:
    """Draw one part (turned by `rotate_deg`, e.g. its start angle); for files with problems the raw
    lines are shown with the problem highlighted."""
    if part is None:
        view.set_content([])
    elif part.geometry_ok:
        fill, line = theme.part_colors(0)
        rad = math.radians(rotate_deg)
        view.set_content([Shape("p", transform_ring(part.outer, rad, 0, 0),
                                [transform_ring(h, rad, 0, 0) for h in part.holes], fill, line)])
    else:
        lines = [(r, "#8a847d", 1.0) for r in part.raw_lines] + [(r, "#ff4d4d", 2.5) for r in part.problem_lines]
        view.set_content([], lines=lines, points=part.problem_points)


class EditView(SheetView):
    """SheetView whose shapes can be grabbed: the drag is reported in sheet coordinates (mm, y up)."""
    pressed = Signal(object, float, float, object)          # key, x, y, keyboard modifiers
    moved = Signal(float, float, object)
    released = Signal(float, float, object)
    wheeled = Signal(int)                                   # mouse wheel while a part is held: rotate

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._grab = False

    def _sheet_point(self, event) -> tuple[float, float]:
        p = self.mapToScene(event.position().toPoint())
        return p.x(), -p.y()

    def wheelEvent(self, event):
        if self._grab:
            self.wheeled.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self._shape_at(event.position().toPoint())
            if item is not None:
                self.setFocus()
                self._grab = True
                x, y = self._sheet_point(event)
                self.pressed.emit(item.key, x, y, event.modifiers())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._grab:
            x, y = self._sheet_point(event)
            self.moved.emit(x, y, event.modifiers())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._grab and event.button() == Qt.MouseButton.LeftButton:
            self._grab = False
            x, y = self._sheet_point(event)
            self.released.emit(x, y, event.modifiers())
            event.accept()
            return
        super().mouseReleaseEvent(event)
