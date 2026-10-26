"""Projects in the GUI (headless Qt): File menu, dirty flag, save/open, relink, stale banner, autosave,
hover links on the Report tab."""
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = tempfile.mkdtemp()                 # isolate settings + autosave

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from SimpleNesting import project, strings as S
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.gui import theme
from SimpleNesting.gui.app import MainWindow
from SimpleNesting.models import Material
from SimpleNesting.nesting.engine import nest_material
from SimpleNesting.settings import AUTOSAVE_FILE, Settings
from tests.test_core import make_plate
from tests.test_shapes import CROSS, L_SHAPE, poly_dxf


def pump(app, until, timeout=20):
    t0 = time.time()
    while not until() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    assert until(), "timed out"


def answer(button):
    QMessageBox.question = staticmethod(lambda *a, **k: button)


def make_window(app):
    win = MainWindow(Settings(engine="libnest2d"))
    win.show()
    return win


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    theme.apply(app, "Dark", "#d9480f")
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "drawings"
    src.mkdir()
    make_plate(src / "a.dxf", 450, 400, hole_r=60)
    poly_dxf(src / "cross.dxf", CROSS)
    poly_dxf(src / "l.dxf", L_SHAPE)

    win = make_window(app)
    assert win.windowTitle() == "Untitled - SimpleNesting"
    assert [a.text() for a in win.menuBar().actions()[0].menu().actions() if a.text()][:3] == \
        ["&New Project", "&Open Project...", "Open &Recent"]

    # --- build a small project, nest it, save it
    parts = []
    for name, qty in (("a", 3), ("cross", 4), ("l", 2)):
        p = load_part(str(src / f"{name}.dxf"))
        p.qty = qty
        parts.append(p)
    win.model.materials = [Material(name="DD11", thickness=3, parts=parts, sheet=(1000, 700))]
    win.model.selected = 0
    win.setup.refresh_all()
    win._tick_state()
    assert win.windowTitle().startswith("*Untitled"), win.windowTitle()
    opts = win.options()
    win.model.materials[0].update_fit(opts)
    win.results = [nest_material(win.model.materials[0], opts)]
    win.report.show_results(list(win.results))

    path = tmp / "proj" / "job.sng"
    path.parent.mkdir()
    assert win._write(path) and path.is_file()
    assert win.windowTitle() == "job - SimpleNesting" and str(path) in win.settings.recent_projects
    win._fill_recent()
    assert any("job.sng" in a.text() for a in win.recent_menu.actions())
    win._tick_state()
    assert not win._dirty

    # --- an edit marks the project dirty, and the result stale (banner on the Report tab)
    win.model.materials[0].parts[0].qty = 5
    win._tick_state()
    assert win.windowTitle().startswith("*job") and win.results[0].stale
    assert win.report.stale_label.isVisibleTo(win.report) is True
    win.model.materials[0].parts[0].qty = 3
    win._tick_state()
    assert not win._dirty and not win.results[0].stale          # editing back = clean and current again

    # --- open through the real threaded path in a fresh window
    win2 = make_window(app)
    win2.open_project(str(path))
    pump(app, lambda: win2.project_path == path)
    m = win2.model.materials[0]
    assert m.label == "3mm DD11" and [p.qty for p in m.parts] == [3, 4, 2] and m.sheet == (1000, 700)
    assert len(win2.results) == 1 and not win2.results[0].stale and win2.windowTitle() == "job - SimpleNesting"
    assert win2.results[0].nested == win.results[0].nested and not win2._dirty
    assert win2.report.tiles["Nested Parts"].text() == f"{win.results[0].nested} / {win.results[0].requested}"

    # --- new project asks about unsaved changes
    m.parts[0].qty = 9
    win2._tick_state()
    answer(QMessageBox.StandardButton.Cancel)
    win2.new_project()
    assert win2.model.materials, "Cancel must keep the project"
    answer(QMessageBox.StandardButton.Discard)
    win2.new_project()
    assert win2.model.materials == [] and win2.windowTitle() == "Untitled - SimpleNesting" and not win2._dirty
    win2.close()

    # --- a missing / moved file: flagged, then relinked from its new folder
    moved = tmp / "moved_drawings"
    src.rename(moved)
    win3 = make_window(app)
    win3.load_project_data(project.read(path), path)
    assert [p.status for p in win3.model.materials[0].parts] == [S.STATUS_MISSING] * 3
    assert win3.results == [] and win3.setup.locate_btn.isVisibleTo(win3.setup)
    assert "3 file(s) missing" in win3.status.text()
    n = win3.setup.relink({}, moved)
    assert n == 3 and all(p.status == S.STATUS_OK for p in win3.model.materials[0].parts)
    assert [p.qty for p in win3.model.materials[0].parts] == [3, 4, 2]       # quantities survive the relink
    win3.close()
    moved.rename(src)

    # --- autosave survives a "crash" (window never closed), restore marks the project dirty
    win.model.materials[0].parts[1].qty = 7
    win._tick_state()
    win.autosave()
    assert AUTOSAVE_FILE.is_file()
    win4 = make_window(app)
    answer(QMessageBox.StandardButton.Yes)
    win4.restore_autosave()
    pump(app, lambda: win4.model.materials and win4.project_path == path)
    win4._tick_state()
    assert win4.model.materials[0].parts[1].qty == 7 and win4._dirty and win4.windowTitle().startswith("*job")
    assert win4.save_project() and not win4._dirty and not AUTOSAVE_FILE.is_file()
    win4.close()

    # --- hover links on the Report tab (list <-> sheet)
    win5 = make_window(app)
    mat = Material(name="T", thickness=2, sheet=(700, 500), parts=[load_part(str(src / f"{n}.dxf")) for n in ("a", "cross", "l")])
    for p in mat.parts:
        p.qty = 3
    o = win5.options()
    mat.update_fit(o)
    res = nest_material(mat, o)
    assert len(res.sheets) >= 2, len(res.sheets)
    win5.report.show_results([res])
    rt = win5.report
    cross_pi = 1
    row = rt._row_of[cross_pi]
    rt._on_cell_entered(row, 0)                                       # mouse over the list entry
    lit = [k for k, it in rt.view._items.items() if it.pen().width() == 3]
    expected = {(pl.part_index, pl.copy) for pl in res.sheets[0].placements if pl.part_index == cross_pi}
    assert set(lit) == expected, (lit, expected)
    rt._restore()
    assert not [k for k, it in rt.view._items.items() if it.pen().width() == 3]
    key = next(iter(rt.view._items))                                  # mouse over a contour on the sheet
    rt.on_hover(key)
    assert rt.parts.currentRow() == rt._row_of[key[0]] or rt.parts.selectedItems()
    assert [k for k, it in rt.view._items.items() if it.pen().width() == 3] == [key]
    rt.on_hover(None)
    assert not rt.parts.selectedItems()
    only_later = next(ps for ps in res.parts if ps.sheet_counts and 1 not in ps.sheet_counts)  # a part only on sheet 2+
    rt._on_cell_clicked(rt._row_of[only_later.part_index], 0)         # click jumps to that sheet
    assert rt.sheet_no + 1 == min(only_later.sheet_counts) and rt._click_keys
    win5.close()
    print("project GUI OK")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
