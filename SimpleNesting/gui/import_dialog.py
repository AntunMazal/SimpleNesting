"""Import dialog: reads the selected DXF files in a background thread and reports, per file,
how many outer and inner contours it has, before anything is added to the material."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QProgressBar,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import strings as S
from ..geometry.dxf_loader import load_part
from ..models import Part
from . import theme
from .sheet_view import SheetView, show_part
from .widgets import button, label

C_ADD, C_FILE, C_OUTER, C_INNER, C_SIZE, C_STATUS = range(6)


class LoadWorker(QObject):
    loaded = Signal(int, object)        # row, Part
    finished = Signal()

    def __init__(self, paths: list[str], units: str):
        super().__init__()
        self.paths, self.units, self._cancel = paths, units, False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        for i, path in enumerate(self.paths):
            if self._cancel:
                break
            self.loaded.emit(i, load_part(path, self.units))
        self.finished.emit()


class ImportDialog(QDialog):
    def __init__(self, paths: list[str], units: str, known: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import DXF files")
        self.resize(1080, 620)
        self.paths = sorted(paths, key=lambda p: Path(p).name.lower())
        self.known = known
        self.parts: dict[int, Part] = {}
        self._done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.addWidget(label("Import report", "h1"))
        self.summary = label("Reading files...", "muted")
        root.addWidget(self.summary)
        self.bar = QProgressBar()
        self.bar.setRange(0, len(self.paths))
        root.addWidget(self.bar)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)
        self.table = QTableWidget(len(self.paths), 6)
        self.table.setHorizontalHeaderLabels(["Add", "File", "Outer", "Inner", "Size (mm)", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(C_FILE, QHeaderView.ResizeMode.Stretch)
        for c, w in ((C_ADD, 44), (C_OUTER, 60), (C_INNER, 60), (C_SIZE, 100), (C_STATUS, 150)):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        for i, p in enumerate(self.paths):
            name = QTableWidgetItem(Path(p).name)
            name.setToolTip(p)
            self.table.setItem(i, C_FILE, name)
            for c in (C_OUTER, C_INNER, C_SIZE, C_STATUS):
                self.table.setItem(i, c, QTableWidgetItem("..."))
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(i, C_ADD, chk)
        self.table.itemChanged.connect(lambda _: self._update_buttons())
        self.table.currentCellChanged.connect(lambda r, *_: self._show(r))
        body.addWidget(self.table, 3)

        right = QVBoxLayout()
        self.view = SheetView()
        self.view.setMinimumWidth(340)
        right.addWidget(self.view, 1)
        self.note = label("", "muted", wrap=True)
        self.note.setMinimumHeight(52)
        right.addWidget(self.note)
        body.addLayout(right, 2)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.cancel_btn = button("Cancel", slot=self.reject)
        self.add_btn = button("Add Selected", "primary", self.accept)
        self.add_btn.setEnabled(False)
        foot.addWidget(self.cancel_btn)
        foot.addWidget(self.add_btn)
        root.addLayout(foot)

        self.thread = QThread(self)
        self.worker = LoadWorker(self.paths, units)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.loaded.connect(self._on_loaded)
        self.worker.finished.connect(self._on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()

    # ---------------------------------------------------------------- slots ---
    def _on_loaded(self, row: int, part: Part) -> None:
        self.parts[row] = part
        dup = str(Path(part.path).resolve()) in self.known
        ok = part.status == S.STATUS_OK and not dup
        status = S.STATUS_DUPLICATE if dup else part.status
        c = theme.colors()
        t = self.table
        t.blockSignals(True)
        t.item(row, C_OUTER).setText(str(part.outer_count) if part.status != S.STATUS_ERROR else "-")
        t.item(row, C_INNER).setText(str(part.hole_count) if part.status != S.STATUS_ERROR else "-")
        t.item(row, C_SIZE).setText(f"{part.width:.0f} x {part.height:.0f}" if part.geometry_ok else "-")
        st = t.item(row, C_STATUS)
        st.setText(status)
        st.setForeground(QBrush(QColor(c["ok_fg"] if ok else c["warn_fg"])))
        if not part.geometry_ok or dup:
            for col in (C_OUTER, C_INNER):
                t.item(row, col).setForeground(QBrush(QColor(c["warn_fg"])))
        chk = t.item(row, C_ADD)
        chk.setCheckState(Qt.CheckState.Checked if ok else Qt.CheckState.Unchecked)
        if dup or not part.geometry_ok:
            chk.setFlags(Qt.ItemFlag.NoItemFlags)       # cannot be added (nothing to nest)
        t.blockSignals(False)
        self.bar.setValue(len(self.parts))
        if t.currentRow() < 0:
            t.selectRow(row)
        self._update_buttons()

    def _on_finished(self) -> None:
        self._done = True
        self._update_buttons()

    def _update_buttons(self) -> None:
        total = len(self.paths)
        good = sum(1 for p in self.parts.values() if p.status == S.STATUS_OK)
        bad = len(self.parts) - good
        selected = len(self.selected_parts())
        text = f"{len(self.parts)} of {total} files read: {good} OK" + (f", {bad} need attention" if bad else "")
        self.summary.setText(text if not self._done else text + ". Files with problems cannot be added; fix the drawing and import it again.")
        self.add_btn.setText(f"Add Selected ({selected})")
        self.add_btn.setEnabled(selected > 0)
        self.bar.setVisible(not self._done)

    def _show(self, row: int) -> None:
        part = self.parts.get(row)
        if part is None:
            return
        self.note.setText(part.note or ("1 outer contour" + (f", {part.hole_count} inner contour(s)." if part.hole_count else ", no holes.")))
        show_part(self.view, part)

    def selected_parts(self) -> list[Part]:
        out = []
        for i in range(len(self.paths)):
            item = self.table.item(i, C_ADD)
            if i in self.parts and item and item.checkState() == Qt.CheckState.Checked:
                out.append(self.parts[i])
        return out

    def _stop(self) -> None:
        self.worker.cancel()
        self.thread.quit()
        self.thread.wait(3000)

    def accept(self) -> None:
        self._stop()
        super().accept()

    def reject(self) -> None:
        self._stop()
        super().reject()
