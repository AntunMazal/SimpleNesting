"""Start angle (all engines) and per-part rotation step (spyrrow)."""
import math
import tempfile
from pathlib import Path

from shapely.geometry import Polygon

from SimpleNesting import project
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions
from SimpleNesting.nesting import libnest_engine
from SimpleNesting.nesting.engine import nest_material
from tests.test_shapes import CROSS, L_SHAPE, NOTCH, poly_dxf


def parts_from(tmp, specs):
    out = []
    for name, pts, qty in specs:
        poly_dxf(tmp / f"{name}.dxf", pts)
        p = load_part(str(tmp / f"{name}.dxf"))
        p.qty = qty
        out.append(p)
    return out


def test_orientations():
    from SimpleNesting.models import Part
    p = Part(path="x.dxf")
    g = NestOptions(engine="libnest2d", rotation_step=90)
    assert p.orientations(g) == [0.0, 90.0, 180.0, 270.0]
    p.start_angle = 30
    assert p.orientations(g) == [30.0, 120.0, 210.0, 300.0]
    p.rotation_step = 180                                     # libnest2d ignores the per-part step ...
    assert p.orientations(g) == [30.0, 120.0, 210.0, 300.0]
    s = NestOptions(engine="spyrrow", rotation_step=90)       # ... spyrrow uses it
    assert p.orientations(s) == [30.0, 210.0]
    p.rotation_step = 0
    assert p.orientations(s) == [30.0]                        # fixed
    p.rotation_step = None
    assert p.orientations(s) == [30.0, 120.0, 210.0, 300.0]   # global step
    assert p.orientations(NestOptions(engine="spyrrow", allow_rotation=False)) == [30.0]
    p.start_angle = -90
    assert p.orientations(NestOptions(engine="spyrrow", rotation_step=180)) == [270.0, 90.0]


def test_fit_uses_start_angle_and_own_step():
    tmp = Path(tempfile.mkdtemp())
    p = parts_from(tmp, [("bar", [(0, 0), (1300, 0), (1300, 40), (0, 40)], 1)])[0]
    m = Material(name="T", sheet=(1000, 1000), margin=0, parts=[p])
    spy = NestOptions(engine="spyrrow", allow_rotation=False)
    m.update_fit(spy)
    assert p.too_large                                        # 0 deg only, 1300 mm does not fit
    p.start_angle = 45
    m.update_fit(spy)
    assert not p.too_large                                    # diagonal start fits
    p.start_angle, p.rotation_step = 0, 45
    m.update_fit(spy)
    assert not p.too_large                                    # own step 45 finds the diagonal
    m.update_fit(NestOptions(engine="libnest2d", allow_rotation=False))
    assert p.too_large                                        # libnest2d ignores the per-part step


def test_libnest_start_angle_maps_back_to_the_drawing():
    """The pre-rotated part libnest2d placed must be exactly the original part rotated by
    (start + libnest rotation) and moved: compare libnest2d's own placed outline with ours."""
    tmp = Path(tempfile.mkdtemp())
    parts = parts_from(tmp, [("l", L_SHAPE, 3), ("cross", CROSS, 3), ("notch", NOTCH, 2)])
    parts[0].start_angle, parts[1].start_angle, parts[2].start_angle = 90, 30, 0
    mat = Material(name="T", sheet=(1500, 900), parts=parts)
    seen = []
    real_nest = libnest_engine.n.nest

    def spy(items, box, dist, cfg, ctl):
        k = real_nest(items, box, dist, cfg, ctl)
        seen.extend([(it.binId(), [(p.x() / libnest_engine.COORD, p.y() / libnest_engine.COORD)
                                   for p in it.transformedContour()]) for it in items])
        return k
    libnest_engine.n.nest = spy
    try:
        for engine in ("libnest2d", "box"):
            seen.clear()
            opts = NestOptions(engine=engine, rotation_step=90)
            mat.update_fit(opts)
            res = nest_material(mat, opts)
            assert res.nested == res.requested == 8 and res.warnings == [], (engine, res.warnings)
            theirs = [Polygon(c) for b, c in seen if b == 0]
            ours = list((Polygon(pl.outer).convex_hull if engine == "libnest2d" else Polygon(pl.outer).envelope)
                          for pl in res.sheets[0].placements)
            W, H = mat.sheet
            from shapely import affinity
            theirs = [affinity.translate(t, W / 2, H / 2) for t in theirs]
            for o in ours:                                    # every outline of ours has a twin placed by libnest2d
                assert any(o.symmetric_difference(t).area < 0.5 for t in theirs), (engine, o.bounds)
    finally:
        libnest_engine.n.nest = real_nest


def test_spyrrow_uses_each_parts_own_step():
    tmp = Path(tempfile.mkdtemp())
    parts = parts_from(tmp, [("l", L_SHAPE, 6), ("cross", CROSS, 5), ("notch", NOTCH, 5)])
    parts[0].rotation_step = 180                              # two positions
    parts[1].rotation_step, parts[1].start_angle = 0, 45      # fixed at 45 deg
    parts[2].rotation_step, parts[2].start_angle = 90, 22.5   # four positions, offset 22.5 deg
    mat = Material(name="T", sheet=(1500, 900), parts=parts)
    opts = NestOptions(engine="spyrrow", rotation_step=15, time_limit_s=3)
    mat.update_fit(opts)
    res = nest_material(mat, opts)
    assert res.nested == res.requested == 16 and res.warnings == [], res.warnings
    seen = {i: set() for i in range(3)}
    for s in res.sheets:
        for pl in s.placements:
            seen[pl.part_index].add(round(pl.rotation_deg % 360, 2))
    assert seen[0] <= {0.0, 180.0}, seen[0]
    assert seen[1] == {45.0}, seen[1]
    assert seen[2] <= {22.5, 112.5, 202.5, 292.5}, seen[2]
    assert len(seen[0]) >= 1 and len(seen[2]) >= 1


def test_project_keeps_orientation_data():
    tmp = Path(tempfile.mkdtemp())
    parts = parts_from(tmp, [("l", L_SHAPE, 1), ("cross", CROSS, 1)])
    parts[0].start_angle, parts[0].rotation_step = 90, 180
    parts[1].start_angle, parts[1].rotation_step = 0, None
    mat = Material(name="T", parts=parts)
    data = project.to_dict([mat], {}, [], tmp)
    loaded, _ = project.materials_from_dict(data, tmp, "mm")
    assert [(p.start_angle, p.rotation_step) for p in loaded[0].parts] == [(90.0, 180.0), (0.0, None)]
    old = project.to_dict([mat], {}, [], tmp)                 # projects saved before this feature still open
    for pd in old["materials"][0]["parts"]:
        pd.pop("start_angle"), pd.pop("rotation_step")
    loaded, _ = project.materials_from_dict(old, tmp, "mm")
    assert [(p.start_angle, p.rotation_step) for p in loaded[0].parts] == [(0.0, None), (0.0, None)]


if __name__ == "__main__":
    test_orientations(); test_fit_uses_start_angle_and_own_step(); test_project_keeps_orientation_data()
    test_libnest_start_angle_maps_back_to_the_drawing(); test_spyrrow_uses_each_parts_own_step()
    print("orientation OK")
