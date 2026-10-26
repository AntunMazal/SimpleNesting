"""Small reusable widgets and helpers."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def scale_font(font, factor: float, bold: bool | None = None):
    """Scaled copy of a font; works for pixel-sized fonts (the style sheet uses px)."""
    f = QFont(font)
    if f.pixelSize() > 0:
        f.setPixelSize(max(1, round(f.pixelSize() * factor)))
    elif f.pointSizeF() > 0:
        f.setPointSizeF(f.pointSizeF() * factor)
    if bold is not None:
        f.setBold(bold)
    return f


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def panel(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("panel")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.setSpacing(8)
    if title:
        lay.addWidget(label(title, "h1"))
    return frame, lay


def button(text: str, name: str | None = None, slot=None) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if name:
        b.setObjectName(name)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    return b


def set_invalid(widget: QWidget, bad: bool) -> None:
    widget.setProperty("invalid", bad)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def parse_float(text: str, minimum: float = 0.0, allow_zero: bool = True) -> float | None:
    try:
        v = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    if v < minimum or (v == minimum and not allow_zero):
        return None
    return v


class Segmented(QFrame):
    """Exclusive row of buttons (like a tab selector)."""
    changed = Signal(str)

    def __init__(self, values: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for v in values:
            b = QPushButton(v)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b)
            lay.addWidget(b, 1)
            b.clicked.connect(lambda _=False, v=v: self.changed.emit(v))
            self.buttons[v] = b

    def set(self, value: str) -> None:
        if value in self.buttons:
            self.buttons[value].setChecked(True)

    def value(self) -> str:
        return next((v for v, b in self.buttons.items() if b.isChecked()), "")
