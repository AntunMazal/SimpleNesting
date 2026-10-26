"""Project files: round trip, relinking, missing / changed files, stale results (no Qt)."""
import os
import shutil
import tempfile
import time
from pathlib import Path

from SimpleNesting import project, strings as S
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions, fingerprint
from SimpleNesting.nesting.engine import nest_material
from SimpleNesting.settings import Settings
from tests.test_core import make_plate
from tests.test_shapes import CROSS, L_SHAPE, poly_dxf


def build(tmp):
    src = tmp / "drawings"
    src.mkdir()
    make_plate(src / "a.dxf", 450, 400, hole_r=60)
    poly_dxf(src / "cross.dxf", CROSS)
    poly_dxf(src / "l.dxf", L_SHAPE)
    parts = []
    for name, qty in (("a", 3), ("cross", 4), ("l", 2)):
        p = load_part(str(src / f"{name}.dxf"))
        p.qty = qty
        parts.append(p)
    parts[2].enabled = False
    mat = Material(name="DD11", thickness=3, parts=parts)
    mat2 = Material(name="DC01", thickness=1, sheet=(3000, 1500), distance=2, margin=4)
    return src, [mat, mat2]


def test_round_trip_and_results():
    tmp = Path(tempfile.mkdtemp())
    src, mats = build(tmp)
    opts = NestOptions(engine="libnest2d", rotation_step=90)
    res = nest_material(mats[0], opts)
    s = Settings(engine="libnest2d", output_dir="/tmp/out")
    path = tmp / "proj" / "job.sng"
    path.parent.mkdir()
    data = project.to_dict(mats, project.settings_block(s), [res], path.parent, selected=1)
    project.write(path, data)

    back = project.read(path)
    loaded, report = project.materials_from_dict(back, path.parent, "mm")
    assert report.missing == [] and report.changed == []
    assert [m.label for m in loaded] == ["3mm DD11", "1mm DC01"] and loaded[0].uid == mats[0].uid
    assert [(p.qty, p.enabled) for p in loaded[0].parts] == [(3, True), (4, True), (2, False)]
    assert loaded[1].sheet == (3000, 1500) and loaded[1].distance == 2 and loaded[1].margin == 4
    assert back["selected"] == 1 and back["settings"]["output_dir"] == "/tmp/out"
    results = project.results_from_dict(back, loaded, opts, report)
    assert len(results) == 1 and not results[0].stale
    r = results[0]
    assert r.nested == res.nested and len(r.sheets) == len(res.sheets) and abs(r.yield_pct - res.yield_pct) < 1e-9
    a = [(p.part_index, p.copy, p.rotation_deg) for p in res.sheets[0].placements]
    b = [(p.part_index, p.copy, p.rotation_deg) for p in r.sheets[0].placements]
    assert a == b
    # geometry is rebuilt from the DXF files and the placements, identical to the original
    assert all(abs(x1 - x2) < 1e-6 for p, q in zip(res.sheets[0].placements, r.sheets[0].placements)
               for (x1, _), (x2, _) in zip(p.outer, q.outer))
    # snapshots: equal for the same state, different after an edit
    snap = project.snapshot(loaded, project.settings_block(s), results)
    assert snap == project.snapshot(loaded, project.settings_block(s), results)
    loaded[0].parts[0].qty += 1
    assert snap != project.snapshot(loaded, project.settings_block(s), results)


def test_relink_missing_and_changed():
    tmp = Path(tempfile.mkdtemp())
    src, mats = build(tmp)
    opts = NestOptions(engine="libnest2d")
    res = nest_material(mats[0], opts)
    path = tmp / "job.sng"
    project.write(path, project.to_dict(mats, {}, [res], tmp))

    # 1. the whole tree moves: absolute paths break, relative paths still work
    moved = Path(tempfile.mkdtemp()) / "moved"
    shutil.copytree(tmp, moved)
    shutil.rmtree(tmp)
    loaded, report = project.materials_from_dict(project.read(moved / "job.sng"), moved, "mm")
    assert report.missing == [] and all(p.status == S.STATUS_OK for p in loaded[0].parts)
    results = project.results_from_dict(project.read(moved / "job.sng"), loaded, opts, report)
    assert len(results) == 1 and not results[0].stale          # moving does not make the result stale

    # 2. a file disappears: status Missing file, its result cannot be rebuilt
    os.remove(moved / "drawings" / "cross.dxf")
    loaded, report = project.materials_from_dict(project.read(moved / "job.sng"), moved, "mm")
    assert report.missing == ["cross.dxf"]
    assert loaded[0].parts[1].status == S.STATUS_MISSING and not loaded[0].parts[1].geometry_ok
    data = project.read(moved / "job.sng")
    assert project.results_from_dict(data, loaded, opts, report) == [] and report.skipped_results == 1

    # 3. a drawing changes: it is flagged, and the result becomes stale
    poly_dxf(moved / "drawings" / "cross.dxf", CROSS)
    time.sleep(0.02)
    make_plate(moved / "drawings" / "a.dxf", 500, 400, hole_r=60)
    os.utime(moved / "drawings" / "a.dxf", (time.time() + 5, time.time() + 5))
    loaded, report = project.materials_from_dict(data, moved, "mm")
    assert "a.dxf" in report.changed
    results = project.results_from_dict(data, loaded, opts, report)
    assert results and results[0].stale


def test_fingerprint_and_errors():
    tmp = Path(tempfile.mkdtemp())
    src, mats = build(tmp)
    opts = NestOptions(engine="libnest2d")
    f0 = fingerprint(mats[0], opts)
    assert f0 == fingerprint(mats[0], NestOptions(engine="libnest2d", time_limit_s=99))   # time limit does not matter
    for change in (lambda: setattr(mats[0].parts[0], "qty", 9), lambda: setattr(mats[0], "margin", 9.0),
                   lambda: setattr(mats[0], "sheet", (2000.0, 1000.0))):
        fp = fingerprint(mats[0], opts)
        change()
        assert fingerprint(mats[0], opts) != fp
    assert fingerprint(mats[0], NestOptions(engine="spyrrow")) != fingerprint(mats[0], opts)
    bad = tmp / "bad.sng"
    bad.write_text("{not json")
    for text in ("{not json", '{"format": "something-else"}', '{"format": "simple-nesting-project", "version": 99}'):
        bad.write_text(text)
        try:
            project.read(bad)
            raise AssertionError("expected ProjectError")
        except project.ProjectError:
            pass


if __name__ == "__main__":
    test_round_trip_and_results()
    test_relink_missing_and_changed()
    test_fingerprint_and_errors()
    print("project OK")
