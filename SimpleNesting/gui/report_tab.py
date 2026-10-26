"""Report tab: efficiency, parts breakdown, sheet preview and report export."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QMessageBox, QProgressBar, QStyledItemDelegate,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..export.report import write_report
from ..models import MaterialResult, fmt_num
from ..settings import Settings
from . import theme
from .sheet_view import SheetView, Shape
from .widgets import Segmented, button, label, panel, scale_font

log = logging.getLogger(__name__)
HINT = "Hover a part or click it. Wheel = zoom, drag = pan."


class SwatchDelegate(QStyledItemDelegate):
    """Colour square taken from the item's UserRole."""

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(index.data(Qt.ItemDataRole.UserRole)))
        c = option.rect.center()
        painter.drawRoundedRect(QRectF(c.x() - 7, c.y() - 7, 14, 14), 3, 3)
        painter.restore()


class ReportTab(QWidget):
    def __init__(self, settings: Settings, set_status, on_folder_changed, parent=None):
        super().__init__(parent)
        self.settings, self.set_status, self.on_folder_changed = settings, set_status, on_folder_changed
        self.results: list[MaterialResult] = []
        self.current = 0
        self.sheet_no = 0
        self._row_of: dict[int, int] = {}       # part index -> row in the breakdown table
        self._click_keys: set = set()
        self._click_row: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("Report for", "muted"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(220)
        self.combo.activated.connect(self.on_select)
        top.addWidget(self.combo)
        top.addStretch(1)
        root.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        mid.addWidget(self._build_left())
        mid.addWidget(self._build_right(), 1)
        root.addLayout(mid, 1)
        root.addWidget(self._build_export_bar())
        self.show_results([])

    # ------------------------------------------------------------ builders ---
    def _build_left(self) -> QFrame:
        frame, lay = panel("Efficiency")
        frame.setFixedWidth(420)
        self.yield_label = label("-", "big")
        lay.addWidget(self.yield_label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.stale_label = label("", wrap=True)
        self.stale_label.setStyleSheet("color: #f2c15a;")
        self.stale_label.hide()
        lay.addWidget(self.stale_label)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.tiles: dict[str, QWidget] = {}
        for i, key in enumerate(("Sheet Size", "Sheets", "Total Area", "Nested Parts")):
            tile = QFrame()
            tile.setObjectName("tile")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(12, 8, 12, 8)
            tl.setSpacing(0)
            tl.addWidget(label(key, "muted"))
            v = label("-")
            v.setFont(scale_font(v.font(), 1.15, bold=True))
            tl.addWidget(v)
            grid.addWidget(tile, i // 2, i % 2)
            self.tiles[key] = v
        lay.addLayout(grid)
        lay.addSpacing(6)
        lay.addWidget(label("Parts breakdown", "h1"))
        self.parts = QTableWidget(0, 4)
        self.parts.setHorizontalHeaderLabels(["", "Part", "Nested", "Sheets"])
        self.parts.verticalHeader().setVisible(False)
        self.parts.setShowGrid(False)
        self.parts.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.parts.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.parts.setMouseTracking(True)
        self.parts.cellEntered.connect(self._on_cell_entered)
        self.parts.cellClicked.connect(self._on_cell_clicked)
        self._parts_viewport = self.parts.viewport()
        self._parts_viewport.installEventFilter(self)
        self.parts.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.parts.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c, w in ((0, 22), (2, 64), (3, 70)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.parts.setColumnWidth(c, w)
        self.parts.setItemDelegateForColumn(0, SwatchDelegate(self.parts))
        lay.addWidget(self.parts, 1)
        return frame

    def _build_right(self) -> QFrame:
        frame, lay = panel("Sheet Preview")
        bar = QHBoxLayout()
        self.prev_btn = button("◀", slot=lambda: self.step_sheet(-1))
        self.next_btn = button("▶", slot=lambda: self.step_sheet(1))
        for b in (self.prev_btn, self.next_btn):
            b.setFixedWidth(40)
            bar.addWidget(b)
        self.sheet_label = label("")
        bar.addWidget(self.sheet_label)
        bar.addStretch(1)
        self.view = SheetView()
        self.view.hovered.connect(self.on_hover)
        self.view.clicked.connect(self.on_click)
        for text, fn in (("Zoom In", self.view.zoom_in), ("Zoom Out", self.view.zoom_out),
                         ("Reset View", self.view.fit), ("Export Image", self.export_image)):
            bar.addWidget(button(text, slot=fn))
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)
        self.hint = label(HINT, "muted")
        lay.addWidget(self.hint)
        return frame

    def _build_export_bar(self) -> QFrame:
        frame, lay = panel()
        lay.setContentsMargins(16, 8, 16, 8)
        row = QHBoxLayout()
        row.addWidget(label("Export format", "muted"))
        self.fmt = Segmented(["CSV", "TXT"])
        self.fmt.set(self.settings.report_format)
        self.fmt.changed.connect(self.on_fmt)
        row.addWidget(self.fmt)
        self.inc = QCheckBox("Include Parts List")
        self.inc.setChecked(self.settings.include_parts_list)
        self.inc.toggled.connect(self.on_inc)
        row.addWidget(self.inc)
        self.auto = QCheckBox("Auto-Save After Nesting")
        self.auto.setChecked(self.settings.auto_save_report)
        self.auto.toggled.connect(self.on_auto)
        row.addWidget(self.auto)
        row.addWidget(button("Save Report", "primary", self.save_report))
        row.addWidget(button("Change Folder...", slot=self.change_folder))
        row.addStretch(1)
        lay.addLayout(row)
        return frame

    # ----------------------------------------------------------------- data ---
    def show_results(self, results: list[MaterialResult], keep_selection: bool = False) -> None:
        self.results = results
        if not keep_selection:
            self.current = 0
        self.current = min(self.current, len(results) - 1) if results else 0
        self.combo.clear()
        self.combo.addItems([r.label for r in results] or ["-"])
        self.combo.setEnabled(bool(results))
        if results:
            self.combo.setCurrentIndex(self.current)
        self.sheet_no = 0
        self.render()

    def on_select(self, index: int) -> None:
        if 0 <= index < len(self.results):
            self.current, self.sheet_no = index, 0
            self.render()

    def render(self) -> None:
        self.parts.setRowCount(0)
        self._row_of = {}
        if not self.results:
            self.yield_label.setText("-")
            self.bar.setValue(0)
            for v in self.tiles.values():
                v.setText("-")
            self.sheet_label.setText("Run nesting to see the result.")
            self.stale_label.hide()
            self.view.set_content([])
            return
        r = self.results[self.current]
        color = "#e5533d" if r.yield_pct < 50 else ("#e0a800" if r.yield_pct < 75 else "#2fb36b")
        notes = []
        if r.stale:
            notes.append("⚠ Out of date: files or settings changed after this nesting. Run nesting again.")
        if r.edited:
            notes.append("✎ Edited by hand (Edit tab). Running the nesting again replaces these edits.")
        self.stale_label.setVisible(bool(notes))
        self.stale_label.setText("\n".join(notes))
        self.stale_label.setStyleSheet("color: #f2c15a;" if r.stale else "color: #aaa39b;")
        self.yield_label.setText(f"{r.yield_pct:.1f}% yield")
        self.yield_label.setStyleSheet(f"color: {color};")
        self.bar.setValue(round(min(r.yield_pct, 100) * 10))
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}")
        self.tiles["Sheet Size"].setText(f"{fmt_num(r.sheet[0])} x {fmt_num(r.sheet[1])}")
        self.tiles["Sheets"].setText(str(len(r.sheets)))
        self.tiles["Total Area"].setText(f"{r.sheet[0] * r.sheet[1] * len(r.sheets) / 1e6:.3f} m²")
        self.tiles["Nested Parts"].setText(f"{r.nested} / {r.requested}")
        self.parts.setRowCount(len(r.parts))
        for row, ps in enumerate(r.parts):
            swatch = QTableWidgetItem()
            swatch.setData(Qt.ItemDataRole.UserRole, theme.part_colors(ps.part_index)[1])
            self.parts.setItem(row, 0, swatch)
            self.parts.setItem(row, 1, QTableWidgetItem(ps.name))
            n = QTableWidgetItem(f"{ps.nested}/{ps.requested}")
            if ps.nested < ps.requested:
                n.setForeground(QColor("#e5533d"))
            self.parts.setItem(row, 2, n)
            self.parts.setItem(row, 3, QTableWidgetItem(",".join(str(s) for s in sorted(ps.sheet_counts)) or "-"))
            self.parts.setRowHeight(row, 26)
            self._row_of[ps.part_index] = row
        self.render_sheet()

    def render_sheet(self) -> None:
        r = self.results[self.current]
        self._click_keys, self._click_row = set(), None
        self._mark_row(None)
        if not r.sheets:
            self.sheet_label.setText("Nothing was placed.")
            self.view.set_content([], frame=r.sheet)
            return
        self.sheet_no = max(0, min(self.sheet_no, len(r.sheets) - 1))
        s = r.sheets[self.sheet_no]
        self.sheet_label.setText(f"Sheet {s.index} / {len(r.sheets)} · Yield: {s.yield_pct:.1f}%")
        shapes = []
        for pl in s.placements:
            fill, line = theme.part_colors(pl.part_index)
            shapes.append(Shape((pl.part_index, pl.copy), pl.outer, pl.holes, fill, line))
        self.view.set_content(shapes, frame=r.sheet)

    def step_sheet(self, d: int) -> None:
        if self.results and self.results[self.current].sheets:
            self.sheet_no = (self.sheet_no + d) % len(self.results[self.current].sheets)
            self.render_sheet()

    def _describe(self, key) -> str:
        r = self.results[self.current]
        pl = next(p for p in r.sheets[self.sheet_no].placements if (p.part_index, p.copy) == key)
        return f"{r.part_names[pl.part_index]}  #{pl.copy}  ·  rotated {pl.rotation_deg:g}°  ·  at ({pl.tx:.0f}, {pl.ty:.0f}) mm"

    # ------------------------------------------------------- hover / click links ---
    def _mark_row(self, row: int | None) -> None:
        if row is None:
            self.parts.clearSelection()
        else:
            self.parts.selectRow(row)
            self.parts.scrollToItem(self.parts.item(row, 1))

    def _keys_on_sheet(self, part_index: int) -> set:
        r = self.results[self.current]
        if not r.sheets:
            return set()
        return {(p.part_index, p.copy) for p in r.sheets[self.sheet_no].placements if p.part_index == part_index}

    def _restore(self) -> None:
        """Back to the clicked selection (or nothing) when the mouse leaves."""
        self.view.highlight(self._click_keys)
        self._mark_row(self._click_row)
        self.hint.setText(HINT)

    def on_hover(self, key) -> None:               # mouse over a contour on the sheet
        if key is None:
            self._restore()
            return
        self.view.highlight({key})
        self._mark_row(self._row_of.get(key[0]))
        self.hint.setText(self._describe(key))

    def on_click(self, key) -> None:
        self._click_keys, self._click_row = {key}, self._row_of.get(key[0])
        self.view.highlight(self._click_keys)
        self._mark_row(self._click_row)
        self.hint.setText(self._describe(key))

    def _on_cell_entered(self, row: int, _col: int) -> None:   # mouse over a part in the list
        if not self.results or row >= len(self.results[self.current].parts):
            return
        ps = self.results[self.current].parts[row]
        keys = self._keys_on_sheet(ps.part_index)
        self.view.highlight(keys)
        if keys:
            self.hint.setText(f"{ps.name}: {len(keys)} of {ps.nested} on this sheet")
        elif ps.sheet_counts:
            self.hint.setText(f"{ps.name}: not on this sheet (on sheet {', '.join(str(s) for s in sorted(ps.sheet_counts))})")
        else:
            self.hint.setText(f"{ps.name}: not placed")

    def _on_cell_clicked(self, row: int, _col: int) -> None:   # click: jump to the first sheet with this part
        ps = self.results[self.current].parts[row]
        if not ps.sheet_counts:
            return
        if self.sheet_no + 1 not in ps.sheet_counts:
            self.sheet_no = min(ps.sheet_counts) - 1
            self.render_sheet()
        self._click_keys, self._click_row = self._keys_on_sheet(ps.part_index), row
        self.view.highlight(self._click_keys)
        self._mark_row(row)

    def eventFilter(self, obj, event):
        if obj is self._parts_viewport and event.type() == QEvent.Type.Leave:
            self._restore()
        return super().eventFilter(obj, event)

    # --------------------------------------------------------------- export ---
    def on_fmt(self, v: str) -> None:
        self.settings.report_format = v
        self.settings.save()

    def on_inc(self, on: bool) -> None:
        self.settings.include_parts_list = on
        self.settings.save()

    def on_auto(self, on: bool) -> None:
        self.settings.auto_save_report = on
        self.settings.save()

    def change_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder", self.settings.output_dir or "")
        if d:
            self.settings.output_dir = d
            self.settings.save()
            self.on_folder_changed(d)

    def save_report(self, auto: bool = False) -> None:
        if not self.results:
            if not auto:
                self.set_status("No result to save yet.")
            return
        folder = self.settings.output_dir
        if not folder:
            if auto:
                return
            folder = QFileDialog.getExistingDirectory(self, "Select output folder")
            if not folder:
                return
            self.settings.output_dir = folder
            self.on_folder_changed(folder)
        fmt = self.fmt.value() or "CSV"
        path = Path(folder) / f"simplenesting_report.{fmt.lower()}"
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            write_report(self.results, str(path), fmt, self.inc.isChecked())
            self.set_status(f"Report saved: {path}")
        except OSError as exc:
            log.exception("Cannot save report")
            QMessageBox.critical(self, "SimpleNesting", f"Cannot save report:\n{exc}")

    def export_image(self) -> None:
        if not self.results or not self.results[self.current].sheets:
            return
        s = self.results[self.current].sheets[self.sheet_no]
        path, _ = QFileDialog.getSaveFileName(self, "Export image", f"sheet_{s.index:02d}.png", "PNG image (*.png)")
        if path:
            self.view.render_image().save(path)
            self.set_status(f"Image saved: {path}")
