"""Table model for the DXF collection of one material (virtualised: only visible rows are painted)."""
from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox, QStyledItemDelegate

from .. import strings as S
from ..models import Material, NestOptions, Part
from . import theme
from .widgets import scale_font

COLS = ["", "File Name", "Size (mm)", "Holes", "Status", "Start (°)", "Rotation", "Quantity"]
C_ENABLED, C_NAME, C_SIZE, C_HOLES, C_STATUS, C_START, C_ROT, C_QTY = range(8)

HEADER_TIPS = {
    C_START: "Start angle: how far the part is turned (counter-clockwise) from its position in the DXF drawing.\n"
             "Used by every engine.",
    C_ROT: "Rotation step for this part.\nGlobal = the step from 'Nesting & output', Fixed = no rotation,\n"
           "180° = 2 positions, 90° = 4 positions, 45° = 8 positions ...\n"
           "spyrrow only: libnest2d and Box use the global step for all parts.",
}
ROTATION_PRESETS: list[tuple[str, float | None]] = [
    ("Global", None), ("Fixed", 0.0), ("180°  (2 positions)", 180.0), ("90°  (4 positions)", 90.0),
    ("45°  (8 positions)", 45.0), ("30°  (12 positions)", 30.0), ("15°  (24 positions)", 15.0)]


def rotation_text(step: float | None) -> str:
    return "Global" if step is None else ("Fixed" if step <= 0 else f"{step:g}°")


def parse_rotation(text: str) -> tuple[bool, float | None]:
    """'Global' -> None, 'Fixed' -> 0, '180' / '90°  (4 positions)' -> that step. (ok, value)"""
    t = text.strip().lower()
    if t in ("", "global", "g"):
        return True, None
    if t in ("fixed", "fix", "none", "off", "0"):
        return True, 0.0
    m = re.match(r"^(\d+(?:[.,]\d+)?)", t)
    if m:
        v = float(m.group(1).replace(",", "."))
        if 0 < v < 360:
            return True, v
    return False, None


class FilesModel(QAbstractTableModel):
    def __init__(self, parent=None, get_options: Callable[[], NestOptions] | None = None):
        super().__init__(parent)
        self.material: Material | None = None
        self._options = get_options or NestOptions

    def set_material(self, material: Material | None) -> None:
        self.beginResetModel()
        self.material = material
        self.endResetModel()

    def part(self, row: int) -> Part | None:
        if self.material and 0 <= row < len(self.material.parts):
            return self.material.parts[row]
        return None

    def refresh(self) -> None:
        """Re-read all values (e.g. statuses changed after a sheet/rotation edit)."""
        if self.material and self.material.parts:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.material.parts) - 1, len(COLS) - 1))

    # ---- Qt model API
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or not self.material else len(self.material.parts)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return HEADER_TIPS.get(section)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _rotation_tip(self, p: Part) -> str:
        opts = self._options()
        angles = p.orientations(opts)
        tip = f"{len(angles)} position{'s' if len(angles) != 1 else ''}: " + ", ".join(f"{a:g}°" for a in angles[:12])
        tip += " ..." if len(angles) > 12 else ""
        if p.rotation_step is not None and opts.engine != "spyrrow":
            tip += "\nThe per-part step is ignored by libnest2d and Box (they use the global step)."
        return tip

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        p = self.part(index.row())
        if p is None:
            return None
        c = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and c == C_ENABLED:
            return Qt.CheckState.Checked if p.enabled else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            if c == C_NAME:
                return p.name
            if c == C_SIZE:
                return f"{p.width:.0f} x {p.height:.0f}" if p.geometry_ok else "-"
            if c == C_HOLES:
                return str(p.hole_count) if p.geometry_ok else "-"
            if c == C_STATUS:
                return p.status
            if c == C_START:
                return f"{p.start_angle:g}°"
            if c == C_ROT:
                return rotation_text(p.rotation_step)
            if c == C_QTY:
                return str(p.qty)
        if role == Qt.ItemDataRole.EditRole:
            if c == C_QTY:
                return p.qty
            if c == C_START:
                return float(p.start_angle)
            if c == C_ROT:
                return rotation_text(p.rotation_step)
        if role == Qt.ItemDataRole.ToolTipRole:
            if c == C_ROT:
                return self._rotation_tip(p)
            if c == C_START:
                return "Double-click to edit. Counter-clockwise from the DXF position."
            return f"{p.path}\n{p.note}".strip()
        if role == Qt.ItemDataRole.ForegroundRole:
            muted = QColor(theme.colors()["muted"])
            if c in (C_SIZE, C_HOLES):
                return muted
            if c == C_ROT and p.rotation_step is not None and self._options().engine != "spyrrow":
                return muted                                # own step is set but ignored by this engine
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (C_SIZE, C_HOLES, C_START, C_ROT, C_QTY):
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        p = self.part(index.row())
        if p is None:
            return False
        c = index.column()
        if role == Qt.ItemDataRole.CheckStateRole and c == C_ENABLED:
            p.enabled = Qt.CheckState(value) == Qt.CheckState.Checked
        elif role == Qt.ItemDataRole.EditRole and c == C_QTY:
            try:
                p.qty = max(0, min(9999, int(value)))
            except (TypeError, ValueError):
                return False
        elif role == Qt.ItemDataRole.EditRole and c == C_START:
            try:
                p.start_angle = round(float(value) % 360, 4)
            except (TypeError, ValueError):
                return False
        elif role == Qt.ItemDataRole.EditRole and c == C_ROT:
            ok, step = parse_rotation(str(value))
            if not ok:
                return False
            p.rotation_step = step
        else:
            return False
        self.dataChanged.emit(index, index)
        return True

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == C_ENABLED:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        if index.column() in (C_QTY, C_START, C_ROT):
            f |= Qt.ItemFlag.ItemIsEditable
        return f


class QtyDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        box = QSpinBox(parent)
        box.setRange(0, 9999)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return box

    def setEditorData(self, editor, index):
        editor.setValue(int(index.data(Qt.ItemDataRole.EditRole) or 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(0, 1, 0, -1))


class StartAngleDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        box = QDoubleSpinBox(parent)
        box.setRange(0.0, 359.9)
        box.setDecimals(1)
        box.setSuffix("°")
        box.setWrapping(True)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return box

    def setEditorData(self, editor, index):
        editor.setValue(float(index.data(Qt.ItemDataRole.EditRole) or 0.0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(0, 1, 0, -1))


class RotationDelegate(QStyledItemDelegate):
    """Editable combo box: pick a preset or type a step (e.g. 60)."""

    def createEditor(self, parent, option, index):
        box = QComboBox(parent)
        box.setEditable(True)
        box.addItems([text for text, _ in ROTATION_PRESETS])
        return box

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data(Qt.ItemDataRole.EditRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(0, 1, 0, -1))


class StatusDelegate(QStyledItemDelegate):
    """Draws the status as a coloured chip."""

    def paint(self, painter: QPainter, option, index):
        super().paint(painter, option, index)       # selection background
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        c = theme.colors()
        ok = text == S.STATUS_OK
        bg, fg = (c["ok_bg"], c["ok_fg"]) if ok else (c["warn_bg"], c["warn_fg"])
        fm = painter.fontMetrics()
        w = min(fm.horizontalAdvance(text) + 18, option.rect.width() - 6)
        rect = QRectF(option.rect.left() + 4, option.rect.center().y() - 11, w, 22)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor(fg))
        painter.setFont(scale_font(painter.font(), 0.9))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w - 10)))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(120, 30)
