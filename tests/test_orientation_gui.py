"""Start angle / rotation step in the Setup tab (headless Qt), incl. a real run through the worker process."""
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = tempfile.mkdtemp()

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from SimpleNesting import strings as S
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.gui import theme
from SimpleNesting.gui.app import MainWindow
from SimpleNesting.gui.file_model import C_QTY, C_ROT, C_START, parse_rotation
from SimpleNesting.models import Material
from SimpleNesting.settings import Settings
from tests.test_shapes import CROSS, L_SHAPE, NOTCH, poly_dxf

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "Dark", "#d9480f")
    tmp = Path(tempfile.mkdtemp())
    poly_dxf(tmp / "bar.dxf", [(0, 0), (1300, 0), (1300, 40), (0, 40)])
    poly_dxf(tmp / "cross.dxf", CROSS)
    poly_dxf(tmp / "lshape.dxf", L_SHAPE)
    poly_dxf(tmp / "notch.dxf", NOTCH)
    parts = [load_part(str(tmp / f"{n}.dxf")) for n in ("bar", "cross", "lshape", "notch")]
    for p, q in zip(parts, (1, 4, 6, 4)):
        p.qty = q

    win = MainWindow(Settings(engine="spyrrow", time_limit=3, output_dir=str(tmp / "out")))
    win.show()
    win.model.materials = [Material(name="DD11", thickness=3, parts=parts, sheet=(1000, 1000), margin=0)]
    win.model.selected, win.model.selected_part = 0, 0
    win.setup.refresh_all()
    fm, view = win.setup.files_model, win.setup.files_view
    QTest.qWait(100)

    # parse helper: presets, plain numbers, garbage
    assert parse_rotation("Global") == (True, None) and parse_rotation("Fixed") == (True, 0.0)
    assert parse_rotation("180°  (2 positions)") == (True, 180.0) and parse_rotation("22,5") == (True, 22.5)
    assert parse_rotation("abc")[0] is False and parse_rotation("400")[0] is False

    # --- the bar (1300 mm) does not fit a 1000 mm sheet at 0 / 90 deg ...
    assert parts[0].too_large and parts[0].status == S.STATUS_TOO_LARGE
    # ... edit its start angle through the model (as the delegate does)
    assert fm.setData(fm.index(0, C_START), 45.0)
    QTest.qWait(30)
    assert parts[0].start_angle == 45.0 and fm.index(0, C_START).data() == "45°"
    assert not parts[0].too_large                     # global step 90 from 45 deg: 45/135/225/315, the diagonal fits
    assert fm.setData(fm.index(0, C_ROT), "Fixed")
    assert parts[0].rotation_step == 0.0 and fm.index(0, C_ROT).data() == "Fixed"
    QTest.qWait(30)
    assert not parts[0].too_large and parts[0].status == S.STATUS_OK
    assert fm.setData(fm.index(0, C_START), 0.0)      # fixed at 0 deg: 1300 mm cannot fit
    QTest.qWait(30)
    assert parts[0].too_large and parts[0].status == S.STATUS_TOO_LARGE
    assert fm.setData(fm.index(0, C_START), 45.0)
    QTest.qWait(30)
    assert not parts[0].too_large
    assert "1 position: 45°" in fm.index(0, C_ROT).data(Qt.ItemDataRole.ToolTipRole)
    assert "start 45" in win.setup.info["Rotation"].text()
    assert not fm.setData(fm.index(0, C_ROT), "banana")               # invalid input is rejected
    win._tick_state()
    assert win._dirty and win.windowTitle().startswith("*")           # edits make the project dirty

    # --- the preview shows the part turned by its start angle
    view.selectRow(2)
    parts[2].start_angle = 0
    win.setup.refresh_preview()
    w0 = win.setup.preview.scene().itemsBoundingRect()
    win.setup._set_start([2], 90)
    QTest.qWait(30)
    w1 = win.setup.preview.scene().itemsBoundingRect()
    assert parts[2].start_angle == 90.0 and abs(w0.width() - w1.height()) < 1e-6 or abs(w0.width() - w1.width()) > 1

    # --- context-menu actions on several rows
    win.setup._set_rotation([1, 2, 3], 180.0)
    win.setup._set_start([1, 2, 3], 0)
    QTest.qWait(30)
    assert [p.rotation_step for p in parts[1:]] == [180.0] * 3 and [p.start_angle for p in parts[1:]] == [0.0] * 3
    win.setup._set_rotation([3], None)
    assert parts[3].rotation_step is None and fm.index(3, C_ROT).data() == "Global"

    # --- engine awareness: libnest2d ignores a per-part step -> the cell is muted and the tooltip says so
    win.setup.engine_seg.changed.emit(S.ENGINE_LIBNEST)
    QTest.qWait(30)
    assert "ignored" in fm.index(1, C_ROT).data(Qt.ItemDataRole.ToolTipRole)
    win.setup.engine_seg.changed.emit(S.ENGINE_SPYRROW)
    assert "ignored" not in fm.index(1, C_ROT).data(Qt.ItemDataRole.ToolTipRole)

    # --- a real run: cross fixed at 30 deg, L-shape only 0/180, notch uses the global 90 deg step
    parts[1].start_angle, parts[1].rotation_step = 30.0, 0.0
    parts[2].start_angle, parts[2].rotation_step = 0.0, 180.0
    parts[3].rotation_step = None
    parts[0].enabled = False
    fm.refresh()
    win.setup.screen_shot = win.grab().save(str(OUT / "setup_orientation.png"))
    win.run_nesting()
    t0 = time.time()
    while win.controller.running and time.time() - t0 < 120:
        app.processEvents()
        time.sleep(0.02)
    assert not win.controller.running and win.results, win.status.text()
    res = win.results[0]
    assert res.nested == res.requested == 14 and res.warnings == [], (res.nested, res.requested, res.warnings)
    seen = {1: set(), 2: set(), 3: set()}
    for s in res.sheets:
        for pl in s.placements:
            seen[pl.part_index].add(round(pl.rotation_deg % 360, 2))
    assert seen[1] == {30.0}, seen[1]
    assert seen[2] <= {0.0, 180.0}, seen[2]
    assert seen[3] <= {0.0, 90.0, 180.0, 270.0}, seen[3]
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)
    win.close()
    print("orientation GUI OK")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
