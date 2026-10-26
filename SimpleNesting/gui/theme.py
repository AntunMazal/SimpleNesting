"""Qt style sheet with a light and a dark palette and a live accent colour."""
from __future__ import annotations

import colorsys
from pathlib import Path
from string import Template

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

from ..settings import APP_DIR

DARK = dict(bg="#2b2927", panel="#3a3735", input="#2f2d2b", border="#4a4642", muted="#aaa39b",
            selected="#46413d", text="#ece8e3", danger="#f08a6a", hover="#4a4642",
            ok_bg="#1f4d33", ok_fg="#7fe0a6", warn_bg="#5c4310", warn_fg="#f2c15a")
LIGHT = dict(bg="#f4f2ef", panel="#e8e5e0", input="#ffffff", border="#c9c4bd", muted="#6b655e",
             selected="#d9d4cc", text="#1f1d1c", danger="#b3401a", hover="#dcd7d0",
             ok_bg="#cfeedd", ok_fg="#14663a", warn_bg="#fbe8b8", warn_fg="#7a5200")
CANVAS_BG = "#1f1d1c"
_state = {"accent": "#d9480f", "dark": True}

QSS = Template("""
QMainWindow, QDialog, QWidget#root { background: $bg; }
QWidget { color: $text; font-size: 13px; }
QLabel { background: transparent; }
QLabel#h1 { font-size: 16px; font-weight: bold; }
QLabel#muted { color: $muted; font-size: 12px; }
QLabel#big { font-size: 34px; font-weight: bold; }
QFrame#panel { background: $panel; border-radius: 10px; }
QFrame#tile { background: $bg; border-radius: 8px; }
QFrame#seg { background: $input; border: 1px solid $border; border-radius: 6px; }
QPushButton { background: $input; border: 1px solid $border; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: $hover; }
QPushButton:disabled { color: $muted; }
QPushButton#primary, QPushButton#run { background: $accent; border: none; color: white; font-weight: bold; }
QPushButton#primary:hover, QPushButton#run:hover { background: $accent_hover; }
QPushButton#primary:disabled, QPushButton#run:disabled { background: $border; color: $muted; }
QPushButton#run { font-size: 16px; letter-spacing: 1px; border-radius: 12px; }
QPushButton#danger { color: $danger; }
QPushButton#link { background: transparent; border: none; color: $danger; text-decoration: underline; padding: 0; }
QPushButton[seg="true"] { background: transparent; border: none; border-radius: 5px; padding: 7px 8px; }
QPushButton[seg="true"]:hover { background: $hover; }
QPushButton[seg="true"]:checked { background: $accent; color: white; }
QPushButton[card="true"] { padding: 6px 7px; }
QPushButton[card="true"]:checked { background: $accent; color: white; border: none; }
QPushButton#dashed { background: transparent; border: 2px dashed $border; padding: 10px; }
QPushButton#dashed:hover { background: $hover; }
QPushButton#dashed[hint="true"] { background: $hover; }
QPushButton#dashed[hint="true"]:hover { background: $hover; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: $input; border: 1px solid $border;
    border-radius: 6px; padding: 5px 8px; selection-background-color: $accent; selection-color: white; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: $muted; }
QLineEdit[invalid="true"] { border: 1px solid #d33; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: $input; selection-background-color: $accent; border: 1px solid $border; }
QTableView, QTableWidget { background: transparent; border: none; gridline-color: transparent;
    selection-background-color: $selected; selection-color: $text; outline: none; }
QTableView::item { padding: 3px 4px; border: none; }
QHeaderView { background: transparent; border: none; }
QTableCornerButton::section { background: transparent; border: none; }
QHeaderView::section { background: transparent; color: $muted; border: none; padding: 4px; font-size: 12px; }
QTableView::indicator, QCheckBox::indicator, QTableWidget::indicator { width: 16px; height: 16px;
    border: 1px solid $border; border-radius: 4px; background: $input; }
QTableView::indicator:checked, QCheckBox::indicator:checked, QTableWidget::indicator:checked {
    background: $accent; border-color: $accent; image: url($check); }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url($up); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url($down); width: 9px; height: 9px; }
QCheckBox { spacing: 8px; background: transparent; }
QListWidget { background: transparent; border: none; outline: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $border; border-radius: 5px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar { background: $input; border: none; border-radius: 4px; max-height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { background: $accent; border-radius: 4px; }
QMenuBar { background: $bg; }
QMenuBar::item { background: transparent; padding: 5px 12px; border-radius: 4px; }
QMenuBar::item:selected { background: $hover; }
QMenu { background: $panel; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 6px 28px 6px 20px; border-radius: 4px; }
QMenu::item:selected { background: $accent; color: white; }
QMenu::item:disabled { color: $muted; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QToolTip { background: $panel; color: $text; border: 1px solid $border; padding: 4px; }
QMessageBox, QFileDialog { background: $bg; }
""")


def colors() -> dict:
    return DARK if _state["dark"] else LIGHT


def accent() -> str:
    return _state["accent"]


def darker(hex_color: str, factor: float = 0.82) -> str:
    c = QColor(hex_color)
    h, l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
    r, g, b = colorsys.hls_to_rgb(h, l * factor, s)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def resolve_dark(mode: str) -> bool:
    if mode == "Dark":
        return True
    if mode == "Light":
        return False
    hints = QGuiApplication.styleHints()
    return hints.colorScheme() != Qt.ColorScheme.Light       # System: dark unless the OS says light


def _check_icon() -> str:
    path = APP_DIR / "check.svg"
    if not path.exists():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
                        'fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    return path.as_posix()


def _arrow_icon(name: str, points: str) -> str:
    path = APP_DIR / f"{name}.svg"
    if not path.exists():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="{points}" fill="none" '
                        'stroke="#aaa39b" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    return path.as_posix()


def apply(app, mode: str, accent_color: str) -> None:
    _state["accent"], _state["dark"] = accent_color, resolve_dark(mode)
    app.setStyleSheet(QSS.safe_substitute(**colors(), accent=accent_color, accent_hover=darker(accent_color),
                                          check=_check_icon(), up=_arrow_icon("arrow_up", "M2 7 L5 3.5 L8 7"),
                                          down=_arrow_icon("arrow_down", "M2 3.5 L5 7 L8 3.5")))


def part_colors(index: int) -> tuple[str, str]:
    """(fill, outline) for part number `index`; golden-angle hues keep neighbours distinct."""
    h = (index * 0.61803398875) % 1.0
    fr, fg, fb = colorsys.hsv_to_rgb(h, 0.45, 0.42)
    orr, og, ob = colorsys.hsv_to_rgb(h, 0.55, 0.95)
    f = lambda r, g, b: "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))
    return f(fr, fg, fb), f(orr, og, ob)
