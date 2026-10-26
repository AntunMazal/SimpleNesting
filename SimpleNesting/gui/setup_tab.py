"""Setup tab: materials, the DXF collection of the selected material, preview and settings."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QStackedWidget, QStyle,
                               QStyledItemDelegate, QTableView, QVBoxLayout, QWidget)

from .. import strings as S
from ..models import MAX_TIME_LIMIT_S, Material, fmt_num, rotation_angles
from ..nesting.engine import available_engines
from ..nesting.estimate import describe
from ..settings import ACCENTS, Settings
from . import theme
from .file_model import (C_ENABLED, C_HOLES, C_NAME, C_QTY, C_ROT, C_SIZE, C_START, C_STATUS, ROTATION_PRESETS,
                         FilesModel, QtyDelegate, RotationDelegate, StartAngleDelegate, StatusDelegate, rotation_text)
from .import_dialog import ImportDialog
from .sheet_view import SheetView, show_part
from .state import AppModel
from .widgets import Segmented, button, label, panel, parse_float, scale_font, set_invalid

log = logging.getLogger(__name__)

ENGINE_LABELS = {"box": S.ENGINE_BOX, "libnest2d": S.ENGINE_LIBNEST, "spyrrow": S.ENGINE_SPYRROW}
SHEET_PRESETS = [(2000.0, 1000.0), (2500.0, 1250.0), (3000.0, 1500.0)]


class MaterialDelegate(QStyledItemDelegate):
    """Material card: name, 'DXF collection' and a file count badge. Double-click / F2 renames."""

    def __init__(self, parent, on_rename):
        super().__init__(parent)
        self.on_rename = on_rename          # (row, text) -> bool

    def createEditor(self, parent, option, index):
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        editor.setText(index.data(Qt.ItemDataRole.DisplayRole))
        editor.selectAll()

    def setModelData(self, editor, model, index):
        self.on_rename(index.row(), editor.text())      # the tab updates its own model and list

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect.adjusted(8, 7, -8, -29))

    def paint(self, painter: QPainter, option, index):
        c = theme.colors()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(2, 3, -2, -3)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(c["selected"] if selected else c["bg"]))
        painter.setPen(QPen(QColor(theme.accent() if selected else c["panel"]), 2))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(scale_font(option.font, 1.05, bold=True))
        painter.setPen(QColor(c["text"]))
        painter.drawText(rect.adjusted(12, 9, -70, 0), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                         index.data(Qt.ItemDataRole.DisplayRole))
        painter.setFont(scale_font(option.font, 0.9))
        painter.setPen(QColor(c["muted"]))
        painter.drawText(rect.adjusted(12, 0, -12, -9), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                         "DXF collection")
        painter.drawText(rect.adjusted(12, 9, -12, 0), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                         index.data(Qt.ItemDataRole.UserRole))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(0, 64)


class SetupTab(QWidget):
    theme_changed = Signal()

    def __init__(self, model: AppModel, settings: Settings, set_status, parent=None):
        super().__init__(parent)
        self.model, self.settings, self.set_status = model, settings, set_status
        self._loading = False
        self._hint_timer = QTimer(self)                 # blinks the '+ Add Material...' button
        self._hint_timer.timeout.connect(self._blink_hint)
        self._hint_on = False
        self._hint_toggles = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self._build_materials())
        top.addWidget(self._build_files(), 1)
        top.addWidget(self._build_preview())
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        bottom.addWidget(self._build_settings(), 5)
        bottom.addWidget(self._build_nesting(), 5)
        bottom.addWidget(self._build_appearance(), 3)
        root.addLayout(top, 1)
        root.addLayout(bottom)
        self.refresh_all()

    # ------------------------------------------------------------ builders ---
    def _build_materials(self) -> QFrame:
        frame, lay = panel("Materials")
        frame.setFixedWidth(240)
        self.mat_list = QListWidget()
        self.mat_list.setItemDelegate(MaterialDelegate(self.mat_list, self.rename_material))
        self.mat_list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.mat_list.setToolTip("Double-click (or F2) to rename. Example: 3mm DD11")
        self.mat_list.setSpacing(1)
        self.mat_list.currentRowChanged.connect(self.select_material)
        lay.addWidget(self.mat_list, 1)
        self.add_material_btn = button("+ Add Material...", "dashed", self.add_material)
        lay.addWidget(self.add_material_btn)
        self.remove_mat_btn = button("Remove", "danger", self.remove_material)
        lay.addWidget(self.remove_mat_btn)
        return frame

    def _build_files(self) -> QFrame:
        frame, lay = panel()
        head = QHBoxLayout()
        self.files_title = label("Files", "h1")
        head.addWidget(self.files_title)
        head.addStretch(1)
        self.locate_btn = button("Locate Missing...", "danger", self.locate_missing)
        self.locate_btn.hide()
        self.remove_files_btn = button("Remove Selected", slot=self.remove_selected_parts)
        self.add_files_btn = button("+ Add Files...", "primary", self.add_files)
        head.addWidget(self.locate_btn)
        head.addWidget(self.remove_files_btn)
        head.addWidget(self.add_files_btn)
        lay.addLayout(head)

        self.files_model = FilesModel(self, self._options)
        self.files_view = QTableView()
        self.files_view.setModel(self.files_model)
        v = self.files_view
        v.verticalHeader().setVisible(False)
        v.verticalHeader().setDefaultSectionSize(32)
        v.setShowGrid(False)
        v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        v.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.SelectedClicked)
        v.setItemDelegateForColumn(C_QTY, QtyDelegate(v))
        v.setItemDelegateForColumn(C_STATUS, StatusDelegate(v))
        v.setItemDelegateForColumn(C_START, StartAngleDelegate(v))
        v.setItemDelegateForColumn(C_ROT, RotationDelegate(v))
        v.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        v.customContextMenuRequested.connect(self._files_menu)
        h = v.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setStretchLastSection(False)
        h.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        for col, w in ((C_ENABLED, 32), (C_SIZE, 86), (C_HOLES, 48), (C_STATUS, 122), (C_START, 72), (C_ROT, 92),
                       (C_QTY, 76)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            v.setColumnWidth(col, w)
        v.selectionModel().currentRowChanged.connect(lambda cur, _prev: self._on_row_changed(cur.row()))
        self.files_model.dataChanged.connect(lambda tl, br, *_: self._on_data_changed(tl.column(), br.column()))
        self._refreshing = False
        sc = QShortcut(QKeySequence.StandardKey.Delete, v)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self.remove_selected_parts)

        self.empty = QFrame()
        self.empty.setStyleSheet("QFrame { border: 2px dashed palette(mid); border-radius: 10px; }")
        el = QVBoxLayout(self.empty)
        self.empty_text = label("", "muted", wrap=True)
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setStyleSheet("border: none;")
        el.addWidget(self.empty_text)
        self.stack = QStackedWidget()
        self.stack.addWidget(v)
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        self.files_total = label("", "muted")
        foot.addWidget(self.files_total)
        foot.addStretch(1)
        foot.addWidget(label("Set All To:", "muted"))
        self.all_qty = QLineEdit("1")
        self.all_qty.setFixedWidth(56)
        self.all_qty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        foot.addWidget(self.all_qty)
        foot.addWidget(button("Apply", slot=self.apply_all_qty))
        lay.addLayout(foot)
        return frame

    def _build_preview(self) -> QFrame:
        frame, lay = panel("Preview")
        frame.setFixedWidth(290)
        self.preview_name = label("No file selected", "muted")
        lay.addWidget(self.preview_name)
        self.preview = SheetView()
        self.preview.setMinimumHeight(140)
        lay.addWidget(self.preview, 1)
        row = QHBoxLayout()
        for text, fn in (("Zoom In", self.preview.zoom_in), ("Zoom Out", self.preview.zoom_out), ("Fit", self.preview.fit)):
            row.addWidget(button(text, slot=fn))
        lay.addLayout(row)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.info: dict[str, QWidget] = {}
        for r, key in enumerate(("Size", "Area", "Contours", "Status", "Rotation")):
            grid.addWidget(label(key, "muted"), r, 0)
            val = label("-")
            val.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(val, r, 1)
            self.info[key] = val
        lay.addLayout(grid)
        return frame

    def _build_settings(self) -> QFrame:
        frame, lay = panel("Material settings")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.e_name = QLineEdit()
        self.e_name.setPlaceholderText("e.g. DD11")
        self.e_thick = QLineEdit()
        self.e_thick.setPlaceholderText("e.g. 3")
        self.e_dist, self.e_margin = QLineEdit(), QLineEdit()
        grid.addWidget(label("Material", "muted"), 0, 0)
        grid.addWidget(label("Thickness (mm)", "muted"), 0, 1)
        grid.addWidget(self.e_name, 1, 0)
        grid.addWidget(self.e_thick, 1, 1)
        grid.addWidget(label("Sheet format (mm)", "muted"), 2, 0, 1, 2)
        sheets = QHBoxLayout()
        self.sheet_group = QButtonGroup(self)
        self.sheet_btns: list[QPushButton] = []
        for size in SHEET_PRESETS:
            b = button(f"{fmt_num(size[0])} x {fmt_num(size[1])}", slot=lambda s=size: self.set_sheet(s))
            b.setCheckable(True)
            b.setProperty("card", True)
            self.sheet_group.addButton(b)
            sheets.addWidget(b)
            self.sheet_btns.append(b)
        self.custom_btn = button("Custom", slot=self.show_custom_sheet)
        self.custom_btn.setCheckable(True)
        self.custom_btn.setProperty("card", True)
        self.sheet_group.addButton(self.custom_btn)
        sheets.addWidget(self.custom_btn)
        sheets.addStretch(1)
        grid.addLayout(sheets, 3, 0, 1, 2)
        self.custom_row = QWidget()
        cr = QHBoxLayout(self.custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.e_sheet_w, self.e_sheet_h = QLineEdit(), QLineEdit()
        for e in (self.e_sheet_w, self.e_sheet_h):
            e.setFixedWidth(90)
        cr.addWidget(self.e_sheet_w)
        cr.addWidget(label("x"))
        cr.addWidget(self.e_sheet_h)
        cr.addStretch(1)
        grid.addWidget(self.custom_row, 4, 0, 1, 2)
        grid.addWidget(label("Distance (mm)", "muted"), 5, 0)
        grid.addWidget(label("Margin (mm)", "muted"), 5, 1)
        grid.addWidget(self.e_dist, 6, 0)
        grid.addWidget(self.e_margin, 6, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        for e in (self.e_name, self.e_thick, self.e_dist, self.e_margin, self.e_sheet_w, self.e_sheet_h):
            e.textEdited.connect(lambda _t: self.on_settings_edited())
        return frame

    def _build_nesting(self) -> QFrame:
        frame, lay = panel("Nesting & output")
        self.engine_seg = Segmented(list(ENGINE_LABELS.values()))
        self.engine_seg.set(ENGINE_LABELS.get(self.settings.engine, S.ENGINE_LIBNEST))
        self.engine_seg.changed.connect(self.on_engine)
        lay.addWidget(self.engine_seg)

        rot = QHBoxLayout()
        self.rot_check = QCheckBox("Allow Rotation")
        self.rot_check.setChecked(self.settings.allow_rotation)
        self.rot_check.toggled.connect(self.on_rotation)
        rot.addWidget(self.rot_check)
        rot.addStretch(1)
        rot.addWidget(label("Step (°)", "muted"))
        self.e_step = QLineEdit(fmt_num(self.settings.rotation_step))
        self.e_step.setFixedWidth(60)
        self.e_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.e_step.textEdited.connect(lambda _t: self.on_step())
        rot.addWidget(self.e_step)
        lay.addLayout(rot)
        self.rot_hint = label("", "muted")
        lay.addWidget(self.rot_hint)

        tl = QHBoxLayout()
        self.time_label = label(f"Time limit per sheet (s, max {MAX_TIME_LIMIT_S})")
        tl.addWidget(self.time_label)
        tl.addStretch(1)
        self.e_time = QLineEdit(str(self.settings.time_limit))
        self.e_time.setFixedWidth(60)
        self.e_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.e_time.textEdited.connect(lambda _t: self.on_time())
        tl.addWidget(self.e_time)
        lay.addLayout(tl)
        self.estimate = label("", "muted", wrap=True)
        lay.addWidget(self.estimate)

        lay.addWidget(label("Output folder", "muted"))
        row = QHBoxLayout()
        self.e_out = QLineEdit(self.settings.output_dir)
        self.e_out.textEdited.connect(lambda _t: self.on_output_edited())
        row.addWidget(self.e_out, 1)
        row.addWidget(button("Select...", slot=self.pick_output))
        lay.addLayout(row)
        urow = QHBoxLayout()
        urow.addWidget(label("Drawing units"))
        self.units = QComboBox()
        self.units.addItems(["mm", "cm", "m", "inch"])
        self.units.setCurrentText(self.settings.units)
        self.units.currentTextChanged.connect(self.on_units)
        urow.addWidget(self.units)
        urow.addWidget(label("Sheet, distance and margin are in mm.", "muted"))
        urow.addStretch(1)
        lay.addLayout(urow)
        self._sync_nesting_controls()
        return frame

    def _build_appearance(self) -> QFrame:
        frame, lay = panel("Appearance")
        lay.addWidget(label("Theme", "muted"))
        self.theme_seg = Segmented(["System", "Light", "Dark"])
        self.theme_seg.set(self.settings.appearance)
        self.theme_seg.changed.connect(self.on_theme)
        lay.addWidget(self.theme_seg)
        lay.addSpacing(8)
        lay.addWidget(label("Accent color", "muted"))
        row = QHBoxLayout()
        self.swatches: dict[str, QPushButton] = {}
        for color in ACCENTS:
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=color: self.on_accent(c))
            row.addWidget(b)
            self.swatches[color] = b
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self._style_swatches()
        return frame

    def _style_swatches(self) -> None:
        for color, b in self.swatches.items():
            border = "#ffffff" if color == self.settings.accent else "transparent"
            b.setStyleSheet(f"QPushButton {{ background: {color}; border: 2px solid {border}; border-radius: 14px; }}")

    # ------------------------------------------------------------- helpers ---
    def _options(self):
        return self.settings.to_options()

    def angles(self) -> list[float]:
        return rotation_angles(self.settings.allow_rotation, self.settings.rotation_step)

    def _refit_all(self) -> None:
        for m in self.model.materials:
            m.update_fit(self._options())

    def update_estimate(self) -> None:
        self._refit_all()
        self.estimate.setText(describe(self.model.materials, self.settings.to_options()))

    def _sync_nesting_controls(self) -> None:
        spyrrow = self.settings.engine == "spyrrow"
        self.e_step.setEnabled(self.settings.allow_rotation)
        self.e_time.setEnabled(spyrrow)
        self.time_label.setEnabled(spyrrow)
        angles = self.angles()
        n = len(angles)
        self.rot_hint.setText(
            f"{n} orientation{'s' if n != 1 else ''}: " + ", ".join(f"{a:g}°" for a in angles[:8]) + (" ..." if n > 8 else "")
            if self.settings.allow_rotation else "Parts keep their orientation (0°).")
        if spyrrow and self.settings.allow_rotation and n > 16:
            self.rot_hint.setText(self.rot_hint.text() + "  Many orientations slow spyrrow down; 4-12 is usually enough.")

    # ------------------------------------------------------------- refresh ---
    def refresh_all(self) -> None:
        self.refresh_materials()
        self.refresh_files()
        self.refresh_settings()
        self.refresh_preview()

    def refresh_materials(self) -> None:
        self.mat_list.blockSignals(True)
        self.mat_list.clear()
        for m in self.model.materials:
            item = QListWidgetItem(m.label)
            n = len(m.parts)
            item.setData(Qt.ItemDataRole.UserRole, f"{n} file{'s' if n != 1 else ''}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.mat_list.addItem(item)
        self.mat_list.setCurrentRow(self.model.selected)
        self.mat_list.blockSignals(False)
        self.remove_mat_btn.setEnabled(self.model.material is not None)

    def refresh_files(self) -> None:
        m = self.model.material
        self.files_title.setText(f"Files: {m.label}" if m else "Files")
        self.add_files_btn.setEnabled(m is not None)
        self.remove_files_btn.setEnabled(bool(m and m.parts))
        self.files_model.set_material(m)
        self.locate_btn.setVisible(bool(m and any(p.load_status == S.STATUS_MISSING for p in m.parts)))
        if m and m.parts:
            self.stack.setCurrentIndex(0)
            row = self.model.selected_part if 0 <= self.model.selected_part < len(m.parts) else -1
            if row >= 0:
                self.files_view.selectRow(row)
        else:
            self.empty_text.setText("No files yet\nClick + Add Files... to select one or more DXF files for this material."
                                    if m else "Add a material first, then add its DXF files.")
            self.stack.setCurrentIndex(1)
        self._update_total()

    def _update_total(self) -> None:
        m = self.model.material
        self.files_total.setText(f"Files: {len(m.parts)} · Total Parts: {m.total_parts}" if m else "Files: 0 · Total Parts: 0")
        self.update_estimate()

    def refresh_settings(self) -> None:
        m = self.model.material
        self._loading = True
        edits = (self.e_name, self.e_thick, self.e_dist, self.e_margin, self.e_sheet_w, self.e_sheet_h)
        for e in edits:
            e.setEnabled(m is not None)
            set_invalid(e, False)
        if m:
            self.e_name.setText(m.name)
            self.e_thick.setText(fmt_num(m.thickness) if m.thickness else "")
            self.e_dist.setText(fmt_num(m.distance))
            self.e_margin.setText(fmt_num(m.margin))
            self.e_sheet_w.setText(fmt_num(m.sheet[0]))
            self.e_sheet_h.setText(fmt_num(m.sheet[1]))
        else:
            for e in edits:
                e.clear()
        for b in self.sheet_btns + [self.custom_btn]:
            b.setEnabled(m is not None)
        self._style_sheet_buttons()
        self._loading = False

    def _style_sheet_buttons(self) -> None:
        m = self.model.material
        preset = next((i for i, s in enumerate(SHEET_PRESETS) if m and tuple(m.sheet) == s), None)
        custom = bool(m) and preset is None
        self.sheet_group.setExclusive(False)
        for i, b in enumerate(self.sheet_btns):
            b.setChecked(i == preset)
        self.custom_btn.setChecked(custom)
        self.sheet_group.setExclusive(True)
        self.custom_row.setVisible(custom)

    def refresh_preview(self) -> None:
        m = self.model.material
        part = m.parts[self.model.selected_part] if m and 0 <= self.model.selected_part < len(m.parts) else None
        for lab in self.info.values():
            lab.setText("-")
        self.preview_name.setText(part.name if part else "No file selected")
        show_part(self.preview, part, part.start_angle if part else 0.0)
        if part is None:
            return
        self.info["Status"].setText(part.status)
        angles = part.orientations(self._options())
        self.info["Rotation"].setText(f"start {part.start_angle:g}° · {len(angles)} position{'s' if len(angles) != 1 else ''}")
        if part.geometry_ok:
            self.info["Size"].setText(f"{part.width:.1f} x {part.height:.1f} mm")
            self.info["Area"].setText(f"{part.area / 1e6:.3f} m²")
            self.info["Contours"].setText(f"1 outer, {part.hole_count} inner")
        else:
            self.info["Contours"].setText(f"{part.outer_count} outer, {part.hole_count} inner")

    # ------------------------------------------------------------- actions ---
    def hint_add_material(self, ms: int = 1500, blinks: int = 5) -> None:
        """Point at the first thing to do: the '+ Add Material...' button blinks.

        The button looks exactly like it does under the mouse (hover colour), so the user
        sees what to press without reading the manual. `ms` is the total time, `blinks`
        the number of times it flashes within it.
        """
        self._hint_timer.stop()
        self._hint_toggles = blinks * 2 - 1             # on/off pairs, the last one ends the hint
        self._set_hint(True)
        self._hint_timer.setInterval(max(40, int(ms / max(1, blinks * 2))))
        self._hint_timer.start()

    def _blink_hint(self) -> None:
        self._set_hint(not self._hint_on)
        self._hint_toggles -= 1
        if self._hint_toggles <= 0:
            self._hint_timer.stop()
            self._set_hint(False)

    def _set_hint(self, on: bool) -> None:
        self._hint_on = on
        btn = self.add_material_btn
        btn.setProperty("hint", on)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def add_material(self) -> None:
        prev = self.model.material
        m = Material(name=self.model.new_material_name())
        if prev:                                    # inherit sheet defaults from the last material
            m.sheet, m.distance, m.margin = prev.sheet, prev.distance, prev.margin
        self.model.materials.append(m)
        self.model.selected, self.model.selected_part = len(self.model.materials) - 1, -1
        self.refresh_all()
        self.e_name.setFocus()

    def remove_material(self) -> None:
        m = self.model.material
        if not m:
            return
        if m.parts and QMessageBox.question(self, S.APP_NAME, f"Remove \"{m.label}\" and its {len(m.parts)} files from the list?") \
                != QMessageBox.StandardButton.Yes:
            return
        del self.model.materials[self.model.selected]
        self.model.selected = min(self.model.selected, len(self.model.materials) - 1)
        self.model.selected_part = 0
        self.refresh_all()

    def rename_material(self, row: int, text: str) -> bool:
        """Inline rename. 'DD11' keeps the thickness, '2mm DD11' sets name and thickness.
        Rejects empty text and names that another material already uses."""
        if not 0 <= row < len(self.model.materials):
            return False
        text = text.strip()
        if not text:
            return False
        m = self.model.materials[row]
        match = re.match(r"^(\d+(?:[.,]\d+)?)\s*mm\s+(.+)$", text, re.IGNORECASE)
        thickness, name = (float(match.group(1).replace(",", ".")), match.group(2).strip()) if match else (m.thickness, text)
        if self.model.label_taken(Material(name=name, thickness=thickness).label, exclude=row):
            self.set_status(f"A material named \"{Material(name=name, thickness=thickness).label}\" already exists.")
            return False
        m.name, m.thickness = name, thickness
        QTimer.singleShot(0, self._after_rename)        # rebuilding the list while its editor closes would crash
        return True

    def _after_rename(self) -> None:
        self.refresh_materials()
        self.refresh_settings()
        m = self.model.material
        if m:
            self.files_title.setText(f"Files: {m.label}")
        self.update_estimate()

    def locate_missing(self) -> None:
        """Pick one moved file; every other missing file found in the same folder is relinked too."""
        from ..geometry.dxf_loader import load_part
        m = self.model.material
        if not m:
            return
        cur = m.parts[self.model.selected_part] if 0 <= self.model.selected_part < len(m.parts) else None
        target = cur if cur and cur.load_status == S.STATUS_MISSING else next(
            (p for p in m.parts if p.load_status == S.STATUS_MISSING), None)
        if target is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {target.name}", self.settings.last_dir or "",
                                              "DXF files (*.dxf *.DXF);;All files (*)")
        if not path:
            return
        self.settings.last_dir = str(Path(path).parent)
        relinked = self.relink({id(target): path}, Path(path).parent)
        self.set_status(f"{relinked} file(s) relinked." if relinked else "No missing file found there.")

    def relink(self, explicit: dict[int, str], folder: Path) -> int:
        """Reload missing parts: `explicit` maps id(part) -> file; others are looked up by name in `folder`."""
        from ..geometry.dxf_loader import load_part
        count = 0
        for mat in self.model.materials:
            for i, old in enumerate(mat.parts):
                if old.load_status != S.STATUS_MISSING:
                    continue
                new_path = explicit.get(id(old)) or str(folder / old.name)
                if not Path(new_path).is_file():
                    continue
                new = load_part(new_path, self.settings.units)
                new.qty, new.enabled = old.qty, old.enabled
                mat.parts[i] = new
                count += 1
            mat.update_fit(self._options())
        self.refresh_all()
        return count

    def sync_from_settings(self) -> None:
        """Show the project settings (engine, rotation, ...) after a project was opened."""
        s = self.settings
        self.engine_seg.set(ENGINE_LABELS.get(s.engine, S.ENGINE_LIBNEST))
        for w in (self.rot_check, self.units):
            w.blockSignals(True)
        self.rot_check.setChecked(s.allow_rotation)
        self.units.setCurrentText(s.units)
        for w in (self.rot_check, self.units):
            w.blockSignals(False)
        self.e_step.setText(fmt_num(s.rotation_step))
        self.e_time.setText(str(s.time_limit))
        self.e_out.setText(s.output_dir)
        self._sync_nesting_controls()
        self.files_model.refresh()

    def select_material(self, row: int) -> None:
        if row < 0 or row == self.model.selected:
            return
        self.model.selected, self.model.selected_part = row, 0
        self.refresh_files()
        self.refresh_settings()
        self.refresh_preview()

    def _on_row_changed(self, row: int) -> None:
        self.model.selected_part = row
        self.refresh_preview()

    def _on_data_changed(self, first_col: int = 0, last_col: int = 99) -> None:
        if self._refreshing:                                # refresh() below emits dataChanged again
            return
        self._refreshing = True
        try:
            self._refit_all()                               # start angle / step change what fits
            self.files_model.refresh()
        finally:
            self._refreshing = False
        self._update_total()
        self.mat_list.viewport().update()
        if first_col <= C_START <= last_col:
            self.refresh_preview()

    # ----------------------------------------------------- orientation actions ---
    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.files_view.selectionModel().selectedRows()})

    def _files_menu(self, pos) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        menu = QMenu(self)
        rot = menu.addMenu(f"Rotation for {len(rows)} selected (spyrrow)")
        for text, step in ROTATION_PRESETS:
            rot.addAction(text).triggered.connect(lambda _=False, s=step: self._set_rotation(rows, s))
        start = menu.addMenu(f"Start angle for {len(rows)} selected")
        for a in (0, 90, 180, 270):
            start.addAction(f"{a}°").triggered.connect(lambda _=False, a=a: self._set_start(rows, a))
        start.addAction("Custom...").triggered.connect(lambda _=False: self._ask_start(rows))
        menu.addSeparator()
        menu.addAction("Remove Selected").triggered.connect(lambda _=False: self.remove_selected_parts())
        menu.exec(self.files_view.viewport().mapToGlobal(pos))

    def _set_rotation(self, rows: list[int], step) -> None:
        m = self.model.material
        if m:
            for r in rows:
                m.parts[r].rotation_step = step
            self.files_model.refresh()

    def _set_start(self, rows: list[int], angle: float) -> None:
        m = self.model.material
        if m:
            for r in rows:
                m.parts[r].start_angle = round(angle % 360, 4)
            self.files_model.refresh()

    def _ask_start(self, rows: list[int]) -> None:
        m = self.model.material
        cur = m.parts[rows[0]].start_angle if m else 0.0
        v, ok = QInputDialog.getDouble(self, "Start angle", "Degrees, counter-clockwise from the DXF position:",
                                       cur, -360.0, 360.0, 1)
        if ok:
            self._set_start(rows, v)

    def add_files(self) -> None:
        m = self.model.material
        if not m:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Select DXF files", self.settings.last_dir or "",
                                                "DXF files (*.dxf *.DXF);;All files (*)")
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths: list[str]) -> None:
        m = self.model.material
        if not m or not paths:
            return
        self.settings.last_dir = str(Path(paths[0]).parent)
        self.settings.save()
        known = {str(Path(p.path).resolve()) for p in m.parts}
        dlg = ImportDialog(paths, self.settings.units, known, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.selected_parts()
        if not new:
            return
        first = len(m.parts)
        m.parts.extend(new)
        m.update_fit(self._options())
        self.model.selected_part = first
        self.refresh_materials()
        self.refresh_files()
        self.set_status(f"{len(new)} file(s) added to {m.label}.")

    def remove_selected_parts(self) -> None:
        m = self.model.material
        if not m:
            return
        rows = sorted({i.row() for i in self.files_view.selectionModel().selectedRows()}, reverse=True)
        for r in rows:
            del m.parts[r]
        if rows:
            self.model.selected_part = min(rows[-1], len(m.parts) - 1)
            self.refresh_materials()
            self.refresh_files()
            self.refresh_preview()

    def apply_all_qty(self) -> None:
        m = self.model.material
        try:
            q = max(0, min(9999, int(self.all_qty.text())))
        except ValueError:
            return
        if m:
            for p in m.parts:
                p.qty = q
            self.files_model.refresh()
            self._update_total()

    # -------------------------------------------------------- settings edits ---
    def on_settings_edited(self) -> None:
        m = self.model.material
        if self._loading or not m:
            return
        thick = parse_float(self.e_thick.text(), 0)
        set_invalid(self.e_thick, thick is None and bool(self.e_thick.text().strip()))
        candidate = Material(name=self.e_name.text().strip(), thickness=thick or 0.0)
        taken = self.model.label_taken(candidate.label, exclude=self.model.selected)
        set_invalid(self.e_name, taken)
        if taken:
            self.set_status(f"A material named \"{candidate.label}\" already exists. Change the name or the thickness.")
        else:
            m.name, m.thickness = candidate.name, candidate.thickness
        for edit, attr in ((self.e_dist, "distance"), (self.e_margin, "margin")):
            v = parse_float(edit.text(), 0)
            set_invalid(edit, v is None)
            if v is not None:
                setattr(m, attr, v)
        w, h = parse_float(self.e_sheet_w.text(), 0, False), parse_float(self.e_sheet_h.text(), 0, False)
        set_invalid(self.e_sheet_w, w is None)
        set_invalid(self.e_sheet_h, h is None)
        if w and h:
            m.sheet = (w, h)
        self._style_sheet_buttons()
        m.update_fit(self._options())
        self.mat_list.blockSignals(True)
        self.mat_list.item(self.model.selected).setText(m.label)
        self.mat_list.blockSignals(False)
        self.mat_list.viewport().update()
        self.files_title.setText(f"Files: {m.label}")
        self.files_model.refresh()
        self.update_estimate()

    def set_sheet(self, size: tuple[float, float]) -> None:
        m = self.model.material
        if m:
            m.sheet = size
            self.refresh_settings()
            m.update_fit(self._options())
            self.files_model.refresh()
            self.update_estimate()

    def show_custom_sheet(self) -> None:
        self.custom_row.setVisible(True)
        self.e_sheet_w.setFocus()

    def on_engine(self, text: str) -> None:
        key = next(k for k, v in ENGINE_LABELS.items() if v == text)
        if not available_engines().get(key):
            module = "spyrrow" if key == "spyrrow" else "python-libnest2d"
            self.set_status(f"Engine '{key}' is not installed (pip install {module}).")
            self.engine_seg.set(ENGINE_LABELS.get(self.settings.engine, S.ENGINE_LIBNEST))
            return
        self.settings.engine = key
        self.settings.save()
        self._sync_nesting_controls()
        self.update_estimate()
        self.files_model.refresh()

    def on_rotation(self, on: bool) -> None:
        self.settings.allow_rotation = on
        self.settings.save()
        self._sync_nesting_controls()
        self._refit_all()
        self.files_model.refresh()
        self.update_estimate()

    def on_step(self) -> None:
        v = parse_float(self.e_step.text(), 0, False)
        bad = v is None or v >= 360
        set_invalid(self.e_step, bad)
        if bad:
            return
        self.settings.rotation_step = v
        self.settings.save()
        self._sync_nesting_controls()
        self._refit_all()
        self.files_model.refresh()
        self.update_estimate()

    def on_time(self) -> None:
        try:
            v = int(self.e_time.text())
            bad = not 1 <= v <= MAX_TIME_LIMIT_S
            if v > MAX_TIME_LIMIT_S:
                self.set_status(f"The time limit is at most {MAX_TIME_LIMIT_S} s per sheet.")
        except ValueError:
            bad, v = True, 0
        set_invalid(self.e_time, bad)
        if not bad:
            self.settings.time_limit = v
            self.settings.save()
            self.update_estimate()

    def on_output_edited(self) -> None:
        self.settings.output_dir = self.e_out.text().strip()
        self.settings.save()

    def pick_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder", self.settings.output_dir or "")
        if d:
            self.set_output_dir(d)

    def set_output_dir(self, d: str) -> None:
        self.e_out.setText(d)
        self.on_output_edited()

    def on_units(self, value: str) -> None:
        if value == self.settings.units:
            return
        from ..geometry.dxf_loader import load_part
        self.settings.units = value
        self.settings.save()
        for m in self.model.materials:      # the geometry depends on the units: read the files again
            fresh = []
            for old in m.parts:
                new = load_part(old.path, value)
                new.qty, new.enabled = old.qty, old.enabled
                fresh.append(new)
            m.parts = fresh
            m.update_fit(self._options())
        self.refresh_all()
        self.set_status(f"Drawing units set to {value}; files were read again.")

    def on_theme(self, value: str) -> None:
        self.settings.appearance = value
        self.settings.save()
        self.theme_changed.emit()

    def on_accent(self, color: str) -> None:
        self.settings.accent = color
        self.settings.save()
        self._style_swatches()
        self.theme_changed.emit()
