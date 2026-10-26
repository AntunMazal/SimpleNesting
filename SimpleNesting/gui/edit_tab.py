"""Edit tab: move and rotate parts by hand. All sheets of a result are on one canvas, one below the other.

Dragging a part is free: it may pass over other parts. While it overlaps another part (or leaves the margin)
it is RED; releasing it there puts it back at the last free position of that drag. While the button is held,
R / Shift+R or the mouse wheel turn it about its centroid. Dropping it on another sheet moves it there.
Parts keep the material's distance and margin (see SimpleNesting/editing.py)."""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import Qt, Signal
from shapely.geometry import Polygon
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QMessageBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import editing as E
from ..export.dxf_exporter import export_material
from ..models import Material, MaterialResult
from ..settings import Settings
from . import theme
from .sheet_view import EditView, Shape
from .widgets import button, label, panel

log = logging.getLogger(__name__)
HINT = ("Drag = move (red = overlap: release there and it jumps back to the last free position) · while dragging: R / Shift+R or the mouse wheel rotate\n"
        "Drop on another sheet = move it there · arrows = nudge 1 mm (Shift 10, Ctrl 0.1) · Del = remove from sheet · Ctrl+Z = undo")
GAP = 0.15                                              # space between sheets, as a fraction of the sheet height


def _ring(coords, ox: float = 0.0, oy: float = 0.0) -> list[tuple[float, float]]:
    return [(x + ox, y + oy) for x, y in list(coords)[:-1]]


class EditTab(QWidget):
    changed = Signal()                                   # a manual edit was committed

    def __init__(self, settings: Settings, set_status, parent=None):
        super().__init__(parent)
        self.settings, self.set_status = settings, set_status
        self.entries: list[tuple[MaterialResult, Material]] = []
        self._ids: list[int] = []
        self.ri = 0                                      # result (material) index
        self.si = 0                                      # focused sheet (the one of the selected part)
        self.editors: list[E.SheetEditor] = []
        self._offs: list[tuple[float, float]] = []       # origin of every sheet on the canvas
        self._where: dict[tuple[int, int], int] = {}     # part key -> sheet index
        self.sel: tuple[int, int] | None = None
        self.undo: list = []
        self.redo: list = []
        self._drag: dict | None = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        root.addLayout(self._build_toolbar())
        mid = QHBoxLayout()
        mid.setSpacing(14)
        mid.addWidget(self._build_side())
        mid.addWidget(self._build_view(), 1)
        root.addLayout(mid, 1)
        self._build_shortcuts()
        self._enable()

    # ------------------------------------------------------------ builders ---
    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(label("Edit result for", "muted"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(200)
        self.combo.activated.connect(self.on_material)
        row.addWidget(self.combo)
        row.addSpacing(16)
        self.prev_btn = button("◀", slot=lambda: self.step_sheet(-1))
        self.next_btn = button("▶", slot=lambda: self.step_sheet(1))
        for b in (self.prev_btn, self.next_btn):
            b.setFixedWidth(40)
        self.sheet_label = label("")
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        row.addWidget(self.sheet_label)
        row.addStretch(1)
        self.fit_btn = button("Show All Sheets", slot=lambda: self.view.fit())
        self.add_sheet_btn = button("Add Sheet", slot=self.add_sheet)
        self.undo_btn = button("Undo", slot=self.do_undo)
        self.redo_btn = button("Redo", slot=self.do_redo)
        self.revert_btn = button("Revert to Nesting", "danger", self.do_revert)
        self.export_btn = button("Export DXF", "primary", self.export_dxf)
        for b in (self.fit_btn, self.add_sheet_btn, self.undo_btn, self.redo_btn, self.revert_btn, self.export_btn):
            row.addWidget(b)
        return row

    def _spin(self, lo: float, hi: float, dec: int = 2, suffix: str = "") -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        return s

    def _build_side(self) -> QFrame:
        frame, lay = panel("Selected part")
        frame.setFixedWidth(340)
        self.part_label = label("Click a part on the sheet", "muted", wrap=True)
        lay.addWidget(self.part_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.sx, self.sy = self._spin(-100000, 100000, 2, " mm"), self._spin(-100000, 100000, 2, " mm")
        self.sa = self._spin(0, 359.99, 2, "°")
        self.sa.setWrapping(True)
        for r, (text, w) in enumerate((("X", self.sx), ("Y", self.sy), ("Angle", self.sa))):
            grid.addWidget(label(text, "muted"), r, 0)
            grid.addWidget(w, r, 1)
            w.editingFinished.connect(self.apply_fields)
        lay.addLayout(grid)
        lay.addWidget(label("X / Y = lower-left corner of the part's bounding box on its sheet.", "muted", wrap=True))

        rot = QHBoxLayout()
        self.step = self._spin(0.1, 180, 1, "°")
        self.step.setValue(15)
        rot.addWidget(label("Step", "muted"))
        rot.addWidget(self.step)
        self.rot_btns = []
        for text, fn in (("−90°", lambda: self.rotate_selected(-90)), ("+90°", lambda: self.rotate_selected(90)),
                         ("−", lambda: self.rotate_selected(-self.step.value())),
                         ("+", lambda: self.rotate_selected(self.step.value()))):
            b = button(text, slot=fn)
            b.setFixedWidth(52)
            b.setStyleSheet("padding: 6px 2px;")
            rot.addWidget(b)
            self.rot_btns.append(b)
        lay.addLayout(rot)

        pull = QHBoxLayout()
        pull.addWidget(label("Pull to contact", "muted"))
        pull.addStretch(1)
        self.pull_btns = []
        for text, (ux, uy) in (("←", (-1, 0)), ("→", (1, 0)), ("↑", (0, 1)), ("↓", (0, -1))):
            b = button(text, slot=lambda ux=ux, uy=uy: self.pull_selected(ux, uy))
            b.setFixedWidth(40)
            pull.addWidget(b)
            self.pull_btns.append(b)
        lay.addLayout(pull)
        self.send_btn = button("Move to a Sheet with Room", slot=self.send_selected)
        self.remove_btn = button("Remove from Sheet", "danger", self.remove_selected)
        lay.addWidget(self.send_btn)
        lay.addWidget(self.remove_btn)

        opt = QHBoxLayout()
        self.snap_chk = QCheckBox("Snap within")
        self.snap_chk.setChecked(True)
        self.snap_mm = self._spin(0.5, 100, 1, " mm")
        self.snap_mm.setValue(6)
        opt.addWidget(self.snap_chk)
        opt.addWidget(self.snap_mm)
        lay.addLayout(opt)
        self.nudge_chk = QCheckBox("Nudge a blocked rotation (up to 32 mm)")
        self.nudge_chk.setChecked(True)
        lay.addWidget(self.nudge_chk)

        lay.addWidget(label("Not placed", "h1"))
        self.unplaced = QTableWidget(0, 2)
        self.unplaced.setHorizontalHeaderLabels(["Part", "Copies"])
        self.unplaced.verticalHeader().setVisible(False)
        self.unplaced.setShowGrid(False)
        self.unplaced.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.unplaced.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.unplaced.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.unplaced.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.unplaced.setColumnWidth(1, 60)
        self.unplaced.cellDoubleClicked.connect(lambda *_: self.place_selected())
        lay.addWidget(self.unplaced, 1)
        self.place_btn = button("Place on a Sheet", slot=self.place_selected)
        lay.addWidget(self.place_btn)
        return frame

    def _build_view(self) -> QFrame:
        frame, lay = panel("Sheets")
        self.view = EditView()
        self.view.pressed.connect(self._on_press)
        self.view.moved.connect(self._on_move)
        self.view.released.connect(self._on_release)
        self.view.wheeled.connect(self._on_wheel)
        lay.addWidget(self.view, 1)
        self.hint = label(HINT, "muted")
        lay.addWidget(self.hint)
        return frame

    def _build_shortcuts(self) -> None:
        def sc(seq: str, slot, widget: QWidget, ctx=Qt.ShortcutContext.WidgetShortcut) -> None:
            s = QShortcut(QKeySequence(seq), widget)
            s.setContext(ctx)
            s.activated.connect(slot)

        v = self.view
        sc("R", lambda: self.rotate_selected(self.step.value()), v)
        sc("Shift+R", lambda: self.rotate_selected(-self.step.value()), v)
        sc("Delete", self.remove_selected, v)
        for name, (ux, uy) in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, 1)), ("Down", (0, -1))):
            for mod, mm in (("", 1.0), ("Shift+", 10.0), ("Ctrl+", 0.1)):
                sc(mod + name, lambda ux=ux, uy=uy, mm=mm: self.nudge_selected(ux * mm, uy * mm), v)
        sc("Ctrl+Z", self.do_undo, self, Qt.ShortcutContext.WidgetWithChildrenShortcut)
        for seq in ("Ctrl+Y", "Ctrl+Shift+Z"):
            sc(seq, self.do_redo, self, Qt.ShortcutContext.WidgetWithChildrenShortcut)

    # ---------------------------------------------------------------- data ---
    @property
    def editor(self) -> E.SheetEditor | None:
        return self.editors[self.si] if 0 <= self.si < len(self.editors) else None

    @property
    def result(self) -> MaterialResult | None:
        return self.entries[self.ri][0] if self.entries else None

    @property
    def material(self) -> Material | None:
        return self.entries[self.ri][1] if self.entries else None

    def set_data(self, results: list[MaterialResult], materials: list[Material]) -> None:
        """Called when the tab is shown and after nesting / opening a project."""
        by_uid = {m.uid: m for m in materials}
        self.entries = [(r, by_uid[r.material_uid]) for r in results if r.material_uid in by_uid]
        ids = [id(r) for r, _ in self.entries]
        new_set = ids != self._ids
        if new_set:                                       # a different result set: start fresh
            self._ids, self.ri, self.si, self.sel = ids, 0, 0, None
            self.undo.clear()
            self.redo.clear()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems([r.label for r, _ in self.entries] or ["-"])
        self.combo.setCurrentIndex(min(self.ri, max(len(self.entries) - 1, 0)))
        self.combo.blockSignals(False)
        self._load(fit=new_set)

    def on_material(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            self.ri, self.si, self.sel = index, 0, None
            self.undo.clear()
            self.redo.clear()
            self._load()

    def step_sheet(self, d: int) -> None:
        if self.editors:
            self.si = (self.si + d) % len(self.editors)
            self.sel = None
            self._select(None)
            self._focus_sheet(self.si)
            self._refresh_header()

    def _focus_sheet(self, k: int) -> None:
        if 0 <= k < len(self._offs) and self.material:
            ox, oy = self._offs[k]
            self.view.focus(ox, oy, self.material.sheet[0], self.material.sheet[1])

    def _enable(self) -> None:
        has = bool(self.editors)
        sel = has and self.sel is not None
        for w in (self.add_sheet_btn, self.prev_btn, self.next_btn, self.fit_btn, self.export_btn):
            w.setEnabled(has)
        self.undo_btn.setEnabled(bool(self.undo))
        self.redo_btn.setEnabled(bool(self.redo))
        self.revert_btn.setEnabled(bool(self.result and self.result.original is not None))
        for w in (self.sx, self.sy, self.sa, self.step, self.remove_btn, self.send_btn, *self.rot_btns, *self.pull_btns):
            w.setEnabled(sel)
        self.send_btn.setEnabled(sel and len(self.editors) > 1)
        self.place_btn.setEnabled(has and self.unplaced.rowCount() > 0)

    def _load(self, fit: bool = False) -> None:
        r, m = self.result, self.material
        if r is None or m is None or not r.sheets:
            self.editors, self._offs, self._where = [], [], {}
            self.view.set_content([])
            self.sheet_label.setText("No result to edit. Run nesting first." if r is None else "No sheets.")
            self.unplaced.setRowCount(0)
            self._select(None)
            self._enable()
            return
        W, H = m.sheet
        self.editors = [E.SheetEditor(m, s) for s in r.sheets]
        self._offs = [(0.0, -k * H * (1 + GAP)) for k in range(len(r.sheets))]
        self.si = max(0, min(self.si, len(self.editors) - 1))
        shapes, frames, self._where = [], [], {}
        for k, ed in enumerate(self.editors):
            ox, oy = self._offs[k]
            frames.append((ox, oy, W, H, f"Sheet {k + 1} · {r.sheets[k].yield_pct:.1f}% · {len(ed.items)} parts"))
            for pl in ed.placements():
                key = (pl.part_index, pl.copy)
                self._where[key] = k
                fill, line = theme.part_colors(pl.part_index)
                shapes.append(Shape(key, [(x + ox, y + oy) for x, y in pl.outer],
                                    [[(x + ox, y + oy) for x, y in h] for h in pl.holes], fill, line))
        self.view.set_content(shapes, fit=fit, frames=frames)
        if self.sel is not None and self.sel not in self._where:
            self.sel = None
        self._select(self.sel)
        self._refresh_header()
        self._refresh_unplaced()
        self._enable()

    def _refresh_header(self) -> None:
        r = self.result
        if r is None or not r.sheets:
            return
        k = max(0, min(self.si, len(r.sheets) - 1))
        s = r.sheets[k]
        self.sheet_label.setText(f"Sheet {k + 1} / {len(r.sheets)} · {s.yield_pct:.1f}%" + (" · edited" if r.edited else "")
                                 + (f" · ⚠ {len(r.warnings)}" if r.warnings else ""))
        self.sheet_label.setToolTip(f"Yield of this sheet {s.yield_pct:.1f}%, of the whole result {r.yield_pct:.1f}%, "
                                    f"{len(s.placements)} parts on this sheet.")

    def _refresh_unplaced(self) -> None:
        r, m = self.result, self.material
        self.unplaced.setRowCount(0)
        if r is None or m is None:
            return
        missing = E.unplaced(m, r)
        self.unplaced.setRowCount(len(missing))
        for row, (pi, copies) in enumerate(missing.items()):
            name = QTableWidgetItem(m.parts[pi].name)
            name.setData(Qt.ItemDataRole.UserRole, pi)
            self.unplaced.setItem(row, 0, name)
            self.unplaced.setItem(row, 1, QTableWidgetItem(str(len(copies))))
            self.unplaced.setRowHeight(row, 26)

    # ------------------------------------------------------------ selection ---
    def _ki(self, key=None) -> tuple[int, int] | None:
        """(sheet index, item index) of a part key (default: the selected one)."""
        key = self.sel if key is None else key
        k = self._where.get(key) if key is not None else None
        if k is None or k >= len(self.editors):
            return None
        i = self.editors[k].index_of(key)
        return (k, i) if i >= 0 else None

    def _select(self, key) -> None:
        self.sel = key
        ki = self._ki(key) if key is not None else None
        if ki is None:
            self.sel = None
            self.view.highlight(())
            self.view.set_marker(None)
            self.part_label.setText("Click a part on the sheet")
        else:
            k, i = ki
            self.si = k
            self.view.highlight({key})
            self._marker(k, i)
            self.part_label.setText(f"{self.material.parts[key[0]].name}  #{key[1]}  ·  sheet {k + 1}")
            self._fill_fields(k, i)
            self._refresh_header()
        self._enable()

    def _marker(self, k: int, i: int) -> None:
        px, py = self.editors[k].pivot(i)
        self.view.set_marker(px + self._offs[k][0], py + self._offs[k][1])

    def _fill_fields(self, k: int, i: int) -> None:
        self._loading = True
        x, y = self.editors[k].bbox_min(i)
        self.sx.setValue(round(x, 2))
        self.sy.setValue(round(y, 2))
        self.sa.setValue(round(self.editors[k].items[i].rot % 360, 2))
        self._loading = False

    def _refresh_item(self, k: int, i: int) -> None:
        it = self.editors[k].items[i]
        ox, oy = self._offs[k]
        self.view.update_shape((it.part_index, it.copy), _ring(it.poly.exterior.coords, ox, oy),
                               [_ring(r.coords, ox, oy) for r in it.poly.interiors])

    # --------------------------------------------------------- commit / undo ---
    def _before(self):
        return E.raw_state(self.result)

    def _commit(self, before, sheets: set[int]) -> None:
        """Write the editors into the result; remember `before` for Undo."""
        r, m = self.result, self.material
        self.undo.append(before)
        del self.undo[:-100]
        self.redo.clear()
        E.commit_sheets(m, r, {k: self.editors[k] for k in sheets})
        self._refresh_header()
        self._refresh_unplaced()
        ki = self._ki()
        if ki is not None:
            self._fill_fields(*ki)
        self._enable()
        self.changed.emit()
        if r.warnings:
            self.set_status(f"⚠ {len(r.warnings)} layout problem(s): {r.warnings[0]}")

    def _restore(self, state) -> None:
        r, m = self.result, self.material
        E.set_layout(m, r, state)
        r.edit_rev += 1
        r.edited = True
        self.si = min(self.si, len(r.sheets) - 1)
        self._load()
        self.changed.emit()

    def do_undo(self) -> None:
        if self._drag is None and self.undo and self.result:
            self.redo.append(E.raw_state(self.result))
            self._restore(self.undo.pop())

    def do_redo(self) -> None:
        if self._drag is None and self.redo and self.result:
            self.undo.append(E.raw_state(self.result))
            self._restore(self.redo.pop())

    def do_revert(self) -> None:
        r, m = self.result, self.material
        if r is None or r.original is None:
            return
        if QMessageBox.question(self, "SimpleNesting", "Discard all manual edits of this result and restore the nesting result?") \
                != QMessageBox.StandardButton.Yes:
            return
        E.revert(m, r)
        self.undo.clear()
        self.redo.clear()
        self.sel = None
        self._load(fit=True)
        self.changed.emit()
        self.set_status("Restored the nesting result.")

    # ------------------------------------------------------------------ drag ---
    def _on_press(self, key, x: float, y: float, mods) -> None:
        ki = self._ki(key)
        if ki is None:
            return
        k, i = ki
        self._select(key)
        it = self.editors[k].items[i]
        self._drag = dict(key=key, k0=k, i0=i, pose0=(it.rot, it.tx, it.ty), pivot=self.editors[k].pivot(i),
                          x0=x, y0=y, cur=(x, y), rot_extra=0.0, target=k, valid=True,
                          shown=(k, it.rot, it.tx, it.ty), last_valid=(k, it.rot, it.tx, it.ty), before=self._before())

    def _sheet_at(self, cx: float, cy: float, default: int) -> int:
        W, H = self.material.sheet
        for k, (ox, oy) in enumerate(self._offs):
            if ox <= cx <= ox + W and oy <= cy <= oy + H:
                return k
        return default

    def _drag_pose(self, d: dict) -> tuple[int, float, float, float]:
        """Pose of the held part for the cursor position, in the coordinates of the sheet it is over."""
        x, y = d["cur"]
        dx, dy = x - d["x0"], y - d["y0"]
        rot0, tx0, ty0 = d["pose0"]
        px, py = d["pivot"]
        a = math.radians(d["rot_extra"])
        c, s = math.cos(a), math.sin(a)
        rx, ry = tx0 - px, ty0 - py                       # turn about the centroid, then follow the cursor
        tx, ty = c * rx - s * ry + px + dx, s * rx + c * ry + py + dy
        ox0, oy0 = self._offs[d["k0"]]
        k = self._sheet_at(px + dx + ox0, py + dy + oy0, d["target"])    # the sheet under the centroid
        ox, oy = self._offs[k]
        return k, rot0 + d["rot_extra"], tx + ox0 - ox, ty + oy0 - oy

    def _update_drag(self) -> None:
        d = self._drag
        k, rot, tx, ty = self._drag_pose(d)
        ed = self.editors[k]
        pi = d["key"][0]
        ok = ed.is_valid_pose(pi, rot, tx, ty, ignore=d["i0"] if k == d["k0"] else -1)
        d["target"], d["shown"], d["valid"] = k, (k, rot, tx, ty), ok
        if ok:
            d["last_valid"] = d["shown"]
        poly = ed._poly(pi, rot, tx, ty)
        ox, oy = self._offs[k]
        self.view.update_shape(d["key"], _ring(poly.exterior.coords, ox, oy), [_ring(r.coords, ox, oy) for r in poly.interiors])
        self.view.set_blocked(d["key"], not ok)
        c = Polygon(poly.exterior).centroid
        self.view.set_marker(c.x + ox, c.y + oy)

    def _on_move(self, x: float, y: float, mods) -> None:
        if self._drag is not None:
            self._drag["cur"] = (x, y)
            self._update_drag()

    def _on_wheel(self, n: int) -> None:
        if self._drag is not None:
            self._drag["rot_extra"] += n * self.step.value()
            self._update_drag()

    def _on_release(self, x: float, y: float, mods) -> None:
        d = self._drag
        if d is None:
            return
        d["cur"] = (x, y)
        self._update_drag()
        self._drag = None
        k0, i0, key = d["k0"], d["i0"], d["key"]
        k, rot, tx, ty = d["shown"] if d["valid"] else d["last_valid"]     # overlapped: back to the last free position
        pose0 = d["pose0"]
        moved = (k != k0 or abs(tx - pose0[1]) > 1e-6 or abs(ty - pose0[2]) > 1e-6
                 or abs(((rot - pose0[0] + 180.0) % 360.0) - 180.0) > 1e-6)
        self.view.set_blocked(key, False)
        if not moved:
            self._load()
            self._select(key)
            if not d["valid"]:
                self.set_status("Overlap: the part went back to where it was.")
            return
        src = self.editors[k0]
        if k == k0:
            if not src.set_pose(i0, rot, tx, ty):
                self._load()
                self._select(key)
                return
            j = i0
        else:
            item = src.remove(i0)
            j = self.editors[k].add_item(item.part_index, item.copy, rot, tx, ty)
        if self.snap_chk.isChecked():
            self.editors[k].snap(j, self.snap_mm.value())
        self._where[key] = k
        self._commit(d["before"], {k0, k})
        self._load()
        self._select(key)
        note = []
        if not d["valid"]:
            note.append("Overlap: the part went back to the last free position of the drag.")
        if k != k0:
            note.append(f"Moved to sheet {k + 1}.")
        if note:
            self.set_status(" ".join(note))

    # ----------------------------------------------------------- operations ---
    def rotate_selected(self, delta: float) -> None:
        if self._drag is not None:                        # button held: turn the part in hand
            self._drag["rot_extra"] += delta
            self._update_drag()
            return
        ki = self._ki()
        if ki is None:
            return
        k, i = ki
        ed = self.editors[k]
        before, pose0 = self._before(), ed.pose(i)
        r = ed.rotate(i, delta, nudge=32.0 if self.nudge_chk.isChecked() else 0.0)
        note = ""
        if r.blocked:
            note = f"Rotation blocked by a neighbour or the sheet edge: turned {abs(r.delta):.1f}° of {abs(delta):g}°."
        elif r.nudge != (0.0, 0.0):
            note = f"Rotated {delta:g}° after moving the part {math.hypot(*r.nudge):.2f} mm to make room."
        self._finish(k, i, before, ed.pose(i) != pose0, note)

    def pull_selected(self, ux: float, uy: float) -> None:
        ki = self._ki()
        if ki is None or self._drag is not None:
            return
        k, i = ki
        ed = self.editors[k]
        before, pose0 = self._before(), ed.pose(i)
        ed.pull(i, ux, uy)
        self._finish(k, i, before, ed.pose(i) != pose0)

    def nudge_selected(self, dx: float, dy: float) -> None:
        ki = self._ki()
        if ki is None or self._drag is not None:
            return
        k, i = ki
        ed = self.editors[k]
        before, pose0 = self._before(), ed.pose(i)
        r = ed.move(i, dx, dy)
        self._finish(k, i, before, ed.pose(i) != pose0, "Blocked by a neighbour or the sheet edge." if r.blocked else "")

    def _finish(self, k: int, i: int, before, changed: bool, note: str = "") -> None:
        self._refresh_item(k, i)
        if changed:
            self._commit(before, {k})
        self._select(self.sel)
        if note:
            self.set_status(note)

    def apply_fields(self) -> None:
        """X / Y / Angle typed in: applied all-or-nothing, only if the pose is free."""
        ki = self._ki()
        if ki is None or self._loading or self._drag is not None:
            return
        k, i = ki
        ed = self.editors[k]
        before, pose0 = self._before(), ed.pose(i)
        nudge = 32.0 if self.nudge_chk.isChecked() else 0.0
        ok = True
        if abs(((self.sa.value() - ed.items[i].rot + 180.0) % 360.0) - 180.0) > 1e-6:
            ok = ed.set_rotation(i, self.sa.value(), nudge)
        if ok:
            x, y = ed.bbox_min(i)
            if abs(self.sx.value() - x) > 1e-6 or abs(self.sy.value() - y) > 1e-6:
                ok = ed.set_bbox_min(i, self.sx.value(), self.sy.value())
        self._finish(k, i, before, ed.pose(i) != pose0,
                     "" if ok else "Not possible: the part would overlap another part or leave the sheet margin.")

    def _orientations(self, pi: int, first: float | None = None) -> list[float]:
        orient = list(self.material.parts[pi].orientations(self.settings.to_options())) or [0.0]
        extra = [a for a in (0.0, 90.0, 180.0, 270.0) if a not in orient]
        return ([first] if first is not None and first not in orient else []) + orient + extra

    def remove_selected(self) -> None:
        ki = self._ki()
        if ki is None or self._drag is not None:
            return
        k, i = ki
        before = self._before()
        item = self.editors[k].remove(i)
        self.sel = None
        self._where.pop((item.part_index, item.copy), None)
        self._commit(before, {k})
        self._load()
        self.set_status(f"{self.material.parts[item.part_index].name} #{item.copy} removed from the sheet (see 'Not placed').")

    def send_selected(self) -> None:
        """Move the selected part to the first other sheet that has room for it."""
        ki = self._ki()
        if ki is None or len(self.editors) < 2 or self._drag is not None:
            return
        k0, i0 = ki
        it = self.editors[k0].items[i0]
        n = len(self.editors)
        for step in range(1, n):
            k = (k0 + step) % n
            j = self.editors[k].add(it.part_index, it.copy, self._orientations(it.part_index, it.rot))
            if j is not None:
                before = self._before()
                item = self.editors[k0].remove(i0)
                self._where[(item.part_index, item.copy)] = k
                self.si = k
                self._commit(before, {k0, k})
                self._load()
                self._focus_sheet(k)
                self.set_status(f"{self.material.parts[it.part_index].name} #{it.copy} moved to sheet {k + 1}.")
                return
        self.set_status("No other sheet has room for this part. Add a sheet or make room.")

    def place_selected(self) -> None:
        r, m = self.result, self.material
        row = self.unplaced.currentRow() if self.unplaced.currentRow() >= 0 else (0 if self.unplaced.rowCount() else -1)
        if not self.editors or row < 0:
            return
        pi = self.unplaced.item(row, 0).data(Qt.ItemDataRole.UserRole)
        copy = E.unplaced(m, r)[pi][0]
        before = self._before()
        n = len(self.editors)
        for step in range(n):                              # the focused sheet first, then the others
            k = (self.si + step) % n
            if self.editors[k].add(pi, copy, self._orientations(pi)) is not None:
                self.sel, self.si = (pi, copy), k
                self._where[(pi, copy)] = k
                self._commit(before, {k})
                self._load()
                self._focus_sheet(k)
                self.set_status(f"{m.parts[pi].name} #{copy} placed on sheet {k + 1}.")
                return
        self.set_status(f"No sheet has room for {m.parts[pi].name}. Use Add Sheet or make room.")

    def add_sheet(self) -> None:
        r, m = self.result, self.material
        if r is None or self._drag is not None:
            return
        before = self._before()
        E.add_sheet(m, r)
        self.undo.append(before)
        self.redo.clear()
        self.si, self.sel = len(r.sheets) - 1, None
        self._load()
        self._focus_sheet(self.si)
        self.changed.emit()

    def export_dxf(self) -> None:
        r, m = self.result, self.material
        if r is None or m is None:
            return
        folder = self.settings.output_dir or QFileDialog.getExistingDirectory(self, "Select output folder")
        if not folder:
            return
        self.settings.output_dir = folder
        try:
            r.dxf_files = export_material(m, r, folder)
        except Exception as exc:                          # noqa: BLE001 - shown to the user
            log.exception("DXF export failed")
            QMessageBox.critical(self, "SimpleNesting", f"DXF export failed:\n{exc}")
            return
        self.set_status(f"{len(r.dxf_files)} DXF file(s) written to {folder}")
