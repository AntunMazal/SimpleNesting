"""Drives the real PySide6 GUI headless (QT_QPA_PLATFORM=offscreen): import report, setup, nesting, report."""
import copy
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ezdxf
from PySide6.QtWidgets import QApplication

from SimpleNesting import strings as S
from SimpleNesting.gui import theme
from SimpleNesting.gui.app import MainWindow
from SimpleNesting.gui.import_dialog import ImportDialog
from SimpleNesting.models import Material
from SimpleNesting.settings import Settings
from tests.test_core import make_plate
from tests.test_shapes import CROSS, L_SHAPE, poly_dxf

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")


def pump(app, seconds=0.0, until=None, timeout=90):
    t0 = time.time()
    while time.time() - t0 < (timeout if until else seconds):
        app.processEvents()
        if until and until():
            break
        time.sleep(0.02)


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    tmp = Path(tempfile.mkdtemp())
    paths = []
    for name, w, h, r in (("28a-a", 450, 400, 60), ("28a-b", 450, 250, None), ("28a-c", 800, 400, 90),
                          ("28a-d", 300, 400, None), ("28a-e", 800, 250, None), ("28a-f", 450, 150, None)):
        make_plate(tmp / f"{name}.dxf", w, h, hole_r=r)
        paths.append(str(tmp / f"{name}.dxf"))
    for nm, pts in (("cross", CROSS), ("lshape", L_SHAPE)):
        poly_dxf(tmp / f"{nm}.dxf", pts, holes=[(150, 150, 20), (250, 150, 20)] if nm == "cross" else ())
        paths.append(str(tmp / f"{nm}.dxf"))
    make_plate(tmp / "open.dxf", 100, 100, loose=True, open_gap=True)
    doc = ezdxf.new("R2010"); msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    msp.add_lwpolyline([(200, 0), (300, 0), (300, 100), (200, 100)], close=True)
    doc.saveas(tmp / "two_parts.dxf")
    paths += [str(tmp / "open.dxf"), str(tmp / "two_parts.dxf")]

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    settings = Settings(output_dir=str(tmp / "out"), engine="spyrrow", time_limit=4)
    theme.apply(app, "Dark", settings.accent)
    win = MainWindow(settings)
    win.show()
    pump(app, 0.3)

    # --- import dialog with the contour report
    dlg = ImportDialog(paths, "mm", set(), win)
    dlg.show()
    pump(app, until=lambda: dlg._done)
    pump(app, 0.3)
    rows = {Path(p.path).name: (p.status, p.outer_count, p.hole_count) for p in dlg.parts.values()}
    for k, v in sorted(rows.items()):
        print(f"  import: {k:14s} {v}")
    assert rows["open.dxf"][0] == S.STATUS_OPEN and rows["two_parts.dxf"][0] == S.STATUS_MULTI
    assert rows["28a-a.dxf"] == (S.STATUS_OK, 1, 1) and rows["cross.dxf"] == (S.STATUS_OK, 1, 2)
    dlg.table.selectRow(sorted(Path(p).name for p in paths).index("open.dxf"))
    pump(app, 0.2)
    dlg.grab().save(str(OUT / "import_open.png"))
    dlg.table.selectRow(sorted(Path(p).name for p in paths).index("cross.dxf"))
    pump(app, 0.2)
    dlg.grab().save(str(OUT / "import_ok.png"))
    selected = dlg.selected_parts()
    assert len(selected) == 8, len(selected)            # the two problem files are not selectable
    dlg.accept()

    # --- setup tab
    m = Material(name="DD11", thickness=3, parts=selected)
    for p, q in zip(m.parts, (1, 1, 2, 1, 1, 3, 3, 3)):
        p.qty = q
    win.model.materials += [m, Material(name="DC01", thickness=1, parts=[copy.deepcopy(selected[1])])]
    win.model.selected, win.model.selected_part = 0, 0
    win.setup.refresh_all()
    pump(app, 0.3)
    win.grab().save(str(OUT / "setup.png"))
    win.setup.add_material()
    pump(app, 0.2)
    win.grab().save(str(OUT / "setup_new_material.png"))
    win.setup.select_material(0)

    # --- performance: 200 files in one material
    big = Material(name="BIG", thickness=2)
    for i in range(200):
        c = copy.deepcopy(selected[0]); c.path = f"/x/part_{i:03d}.dxf"; big.parts.append(c)
    win.model.materials.append(big)
    t = time.perf_counter(); win.setup.select_material(len(win.model.materials) - 1); pump(app, 0.0); app.processEvents()
    print(f"  select a material with 200 files: {(time.perf_counter() - t) * 1000:.0f} ms")
    t = time.perf_counter(); win.setup.files_view.selectRow(50); app.processEvents()
    print(f"  click on a row (200 files): {(time.perf_counter() - t) * 1000:.0f} ms")
    del win.model.materials[-1]
    win.model.selected, win.model.selected_part = 0, 0
    win.setup.refresh_all()
    del win.model.materials[-1]                         # remove the empty "New Material" too
    win.setup.refresh_all()

    # --- nesting through the real worker process
    win.run_nesting()
    pump(app, until=lambda: not win.controller.running)
    pump(app, 0.4)
    win.grab().save(str(OUT / "report.png"))
    print("  status:", win.status.text())
    print("  results:", [(r.label, r.nested, r.requested, len(r.sheets), round(r.yield_pct, 1), r.warnings) for r in win.results])
    assert win.results and all(not r.warnings for r in win.results)

    # --- edit tab: the layout that can be corrected by hand
    win.show_tab(S.TAB_EDIT)
    pump(app, 0.6)
    win.grab().save(str(OUT / "edit.png"))
    win.show_tab(S.TAB_REPORT)
    pump(app, 0.2)
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard)   # unsaved changes on close
    win.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
