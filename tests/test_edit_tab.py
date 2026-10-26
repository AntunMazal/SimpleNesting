"""Edit tab (headless Qt): free drag with red overlap, back to the last free position, rotation while dragging,
moving parts between sheets, numeric entry, remove / place, undo, revert, export."""
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = tempfile.mkdtemp()

import ezdxf
from ezdxf import bbox
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from SimpleNesting import editing as E, project, strings as S
from SimpleNesting.export.dxf_exporter import LAYER_INNER, LAYER_OUTER, export_material
from SimpleNesting.gui import theme
from SimpleNesting.gui.app import MainWindow
from SimpleNesting.models import Material
from SimpleNesting.nesting.engine import nest_material
from SimpleNesting.settings import Settings
from tests.test_editing import rect

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
SHIFT, NONE = Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.NoModifier


def answer(button):
    QMessageBox.question = staticmethod(lambda *a, **k: button)


def pos(view, x, y):
    return view.mapFromScene(QPointF(x, -y))             # world mm (y up) -> viewport pixels


def centroid(tab, key):
    k, i = tab._ki(key)
    px, py = tab.editors[k].pivot(i)
    return px + tab._offs[k][0], py + tab._offs[k][1]      # on the canvas


def main():
    OUT.mkdir(exist_ok=True, parents=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "Dark", "#d9480f")
    tmp = Path(tempfile.mkdtemp())
    parts = []
    for i, (w, h, r, q) in enumerate([(450, 300, 50, 2), (300, 200, 0, 3), (200, 200, 0, 3)]):
        parts.append(rect(f"e{i}", w, h, r or None))
        parts[-1].qty = q
    win = MainWindow(Settings(engine="libnest2d", output_dir=str(tmp / "out")))
    win.show()
    mat = Material(name="DD11", thickness=3, sheet=(2000, 1000), margin=5, distance=3, parts=parts)
    win.model.materials, win.model.selected = [mat], 0
    opts = win.options()
    mat.update_fit(opts)
    res = nest_material(mat, opts)
    E.set_layout(mat, res, [[(0, 1, 0, 50, 50), (0, 2, 0, 550, 50), (1, 1, 0, 50, 450), (1, 2, 0, 450, 450),
                             (1, 3, 0, 850, 450), (2, 1, 0, 1300, 50), (2, 2, 0, 1300, 450), (2, 3, 0, 1650, 50)]])
    assert res.warnings == [] and not res.edited
    win.results = [res]
    win._saved_snapshot = win._snapshot()
    win.show_tab(S.TAB_EDIT)
    tab, view = win.edit, win.edit.view
    vp = view.viewport()
    app.processEvents()

    def tol():                                                 # the mouse has whole pixels: 3 px expressed in mm
        return 3.0 / view.transform().m11()

    assert len(tab.editors) == 1 and "Sheet 1 / 1" in tab.sheet_label.text() and not res.edited

    def press(key, x, y, mods=NONE):
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, mods, pos(view, x, y))

    def move(x, y):
        QTest.mouseMove(vp, pos(view, x, y))

    def release(x, y):
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, NONE, pos(view, x, y))

    # --- 1. free drag into empty space; no overlap, committed
    K1 = (2, 1)
    cx, cy = centroid(tab, K1)
    press(K1, cx, cy)
    for k in range(1, 9):
        move(cx, cy + 150 * k / 8)
    assert tab._drag["valid"] and not view._blocked
    release(cx, cy + 150)
    app.processEvents()
    assert abs(centroid(tab, K1)[1] - (cy + 150)) < tol() and res.edited and len(tab.undo) == 1
    assert res.warnings == [] and tab.editors[0].min_gap() >= 3 - 2 * E.EPS

    # --- 2. the part passes OVER another one: red while overlapping, normal again on a free spot;
    #        released while overlapping it jumps back to the last free position of THIS drag
    cx, cy = centroid(tab, K1)
    over, free = centroid(tab, (2, 3)), (1700.0, 750.0)
    press(K1, cx, cy)
    move(*over)
    assert tab._drag["valid"] is False and K1 in view._blocked, "overlap must turn the part red"
    win.grab().save(str(OUT / "edit_drag_red.png"))
    move(*free)
    assert tab._drag["valid"] and K1 not in view._blocked, "a free position is not red"
    last_free = tab._drag["last_valid"]
    move(*over)
    assert K1 in view._blocked
    release(*over)
    app.processEvents()
    assert K1 not in view._blocked and tab._drag is None
    assert abs(centroid(tab, K1)[0] - free[0]) < tol() and abs(centroid(tab, K1)[1] - free[1]) < tol(), (centroid(tab, K1), last_free)
    assert res.warnings == [] and tab.editors[0].min_gap() >= 3 - 2 * E.EPS
    assert "Overlap" in win.status.text()

    # --- 3. a release on an overlap with nowhere better to go: the part returns to where the drag started
    cx, cy = centroid(tab, K1)
    pose_before = tab.editors[0].pose(tab.editors[0].index_of(K1))
    press(K1, cx, cy)
    move(*over)
    release(*over)
    app.processEvents()
    assert res.warnings == [] and tab.editors[0].min_gap() >= 3 - 2 * E.EPS

    # --- 4. rotation WHILE dragging: R, R, Shift+R and the mouse wheel turn the part in hand
    cx, cy = centroid(tab, K1)
    rot0 = tab.editors[0].items[tab.editors[0].index_of(K1)].rot
    press(K1, cx, cy)
    move(cx + 50, cy)
    QTest.keyClick(view, Qt.Key.Key_R)
    QTest.keyClick(view, Qt.Key.Key_R)
    QTest.keyClick(view, Qt.Key.Key_R, SHIFT)
    assert abs(tab._drag["rot_extra"] - 15.0) < 1e-9, tab._drag["rot_extra"]
    tab._on_wheel(1)                                           # the mouse wheel: +15 degrees
    move(cx + 100, cy)
    assert tab._drag["valid"] and abs(tab._drag["rot_extra"] - 30.0) < 1e-9
    release(cx + 100, cy)
    app.processEvents()
    i = tab.editors[0].index_of(K1)
    assert abs(((tab.editors[0].items[i].rot - rot0 - 30 + 180) % 360) - 180) < 1e-6
    assert abs(centroid(tab, K1)[0] - (cx + 100)) < tol()       # moved and turned in one gesture, about its centroid
    assert res.warnings == [] and tab.editors[0].min_gap() >= 3 - 2 * E.EPS

    # --- 5. drag a part from sheet 1 onto sheet 2 (a new, empty one)
    tab.add_sheet()
    view.fit()
    app.processEvents()
    assert len(tab.editors) == 2 and len(res.sheets) == 2 and res.sheets[1].placements == []
    K2 = (1, 3)
    cx, cy = centroid(tab, K2)
    ox, oy = tab._offs[1]
    target = (ox + 500, oy + 300)
    press(K2, cx, cy)
    for k in range(1, 13):
        move(cx + (target[0] - cx) * k / 12, cy + (target[1] - cy) * k / 12)
    assert tab._drag["target"] == 1 and tab._drag["valid"] and K2 not in view._blocked
    release(*target)
    app.processEvents()
    assert tab._where[K2] == 1 and any((p.part_index, p.copy) == K2 for p in res.sheets[1].placements)
    assert not any((p.part_index, p.copy) == K2 for p in res.sheets[0].placements)
    gx, gy = centroid(tab, K2)
    assert abs(gx - target[0]) < tol() and abs(gy - target[1]) < tol()
    assert res.nested == 8 and res.warnings == [] and "sheet 2" in win.status.text()
    win.grab().save(str(OUT / "edit_two_sheets.png"))
    n_undo = len(tab.undo)
    tab.do_undo()                                              # the move between sheets is one undo step
    assert tab._where[K2] == 0 and len(tab.undo) == n_undo - 1
    tab.do_redo()
    assert tab._where[K2] == 1

    # --- 6. dropping onto an occupied spot on the other sheet: back to the last free position
    K3 = (1, 2)
    cx, cy = centroid(tab, K3)
    press(K3, cx, cy)
    for k in range(1, 13):
        move(cx + (target[0] - cx) * k / 12, cy + (target[1] - cy) * k / 12)
    assert tab._drag["valid"] is False and K3 in view._blocked
    release(*target)
    app.processEvents()
    assert res.warnings == [] and res.nested == 8
    for ed in tab.editors:
        assert ed.min_gap() >= 3 - 2 * E.EPS

    # --- 7. 'Move to a Sheet with Room' finds the room itself
    tab._select(K3)
    k_before = tab._where[K3]
    tab.send_selected()
    assert tab._where[K3] != k_before and res.warnings == [], win.status.text()

    # --- 8. remove -> Not placed -> place on a sheet with room
    tab._select((2, 3))
    tab.remove_selected()
    assert tab.unplaced.rowCount() == 1 and res.nested == 7
    tab.unplaced.selectRow(0)
    tab.place_selected()
    assert tab.unplaced.rowCount() == 0 and res.nested == 8 and res.warnings == []

    # --- 9. buttons, numeric entry, keyboard
    K4 = (2, 2)
    tab._select(K4)
    k, i = tab._ki()
    rot_before = tab.editors[k].items[i].rot
    n_undo = len(tab.undo)
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_R)                         # not dragging: R rotates the selected part by the step
    k, i = tab._ki()
    assert abs(((tab.editors[k].items[i].rot - rot_before - 15 + 180) % 360) - 180) < 1e-6 and len(tab.undo) == n_undo + 1
    QTest.keyClick(tab, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(tab.undo) == n_undo
    tab.sx.setValue(1.0)                                       # outside the 5 mm margin
    pose = tab.editors[k].pose(tab._ki()[1])
    tab.apply_fields()
    assert tab.editors[k].pose(tab._ki()[1]) == pose and "Not possible" in win.status.text()

    # --- 10. export: one DXF per non-empty sheet, equal to what is on screen
    tab.export_dxf()
    files = {Path(f).name: Path(f) for f in res.dxf_files}
    assert len(files) == sum(1 for s in res.sheets if s.placements)
    for sheet in res.sheets:
        if not sheet.placements:
            continue
        doc = ezdxf.readfile(files[f"sheet_{sheet.index:02d}.dxf"])
        cut = [e for e in doc.modelspace() if e.dxf.layer in (LAYER_OUTER, LAYER_INNER)]
        ex = bbox.extents(cut)
        xs = [x for p in sheet.placements for x, _ in p.outer]
        ys = [y for p in sheet.placements for _, y in p.outer]
        assert abs(ex.extmin.x - min(xs)) < 0.1 and abs(ex.extmax.x - max(xs)) < 0.1
        assert abs(ex.extmin.y - min(ys)) < 0.1 and abs(ex.extmax.y - max(ys)) < 0.1

    # --- 11. Report tab note, project keeps the edits and both sheets
    win.show_tab(S.TAB_REPORT)
    assert "Edited by hand" in win.report.stale_label.text()
    path = tmp / "edited.sng"
    win._tick_state()
    assert win._write(path)
    win2 = MainWindow(Settings(engine="libnest2d"))
    win2.show()
    win2.load_project_data(project.read(path), path)
    assert win2.results and win2.results[0].edited and len(win2.results[0].sheets) == 2
    assert [len(s.placements) for s in win2.results[0].sheets] == [len(s.placements) for s in res.sheets]
    win2.show_tab(S.TAB_EDIT)
    assert len(win2.edit.editors) == 2
    answer(QMessageBox.StandardButton.Discard)
    win2.close()

    # --- 12. running the nesting again asks first; 'No' keeps the edits. Revert restores the nesting result.
    answer(QMessageBox.StandardButton.No)
    win.run_nesting()
    assert not win.controller.running and win.results[0].edited
    win.show_tab(S.TAB_EDIT)
    answer(QMessageBox.StandardButton.Yes)
    tab.do_revert()
    assert not res.edited and len(res.sheets) == 1 and res.nested == 8 and not tab.undo
    answer(QMessageBox.StandardButton.Discard)
    win.close()
    print("edit tab OK")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
