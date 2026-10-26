"""Engines, free rotation angles, multi-sheet spyrrow layout and the time estimate."""
import math
import tempfile
from pathlib import Path

import ezdxf
from ezdxf import bbox

from SimpleNesting.export.dxf_exporter import LAYER_INNER, LAYER_OUTER, export_material
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions, rotation_angles
from SimpleNesting.nesting.engine import available_engines, nest_material
from SimpleNesting.nesting.estimate import describe, estimate_sheets, fmt_duration
from tests.test_shapes import CROSS, L_SHAPE, poly_dxf


def make_parts(tmp, specs):
    parts = []
    for name, pts, qty in specs:
        poly_dxf(tmp / f"{name}.dxf", pts, holes=[(60, 60, 25)] if name == "l" else ())
        p = load_part(str(tmp / f"{name}.dxf"))
        p.qty = qty
        parts.append(p)
    return parts


def test_angles():
    assert rotation_angles(False, 90) == [0.0]
    assert rotation_angles(True, 90) == [0.0, 90.0, 180.0, 270.0]
    assert len(rotation_angles(True, 45)) == 8 and len(rotation_angles(True, 15)) == 24
    assert rotation_angles(True, 100) == [0.0, 100.0, 200.0, 300.0]
    assert rotation_angles(True, 0) == [0.0]


def test_fit_check_uses_all_angles():
    tmp = Path(tempfile.mkdtemp())
    poly_dxf(tmp / "long.dxf", [(0, 0), (1300, 0), (1300, 40), (0, 40)])
    p = load_part(str(tmp / "long.dxf"))
    m = Material(name="T", sheet=(1000, 1000), margin=0, parts=[p])      # fits only diagonally
    m.update_fit(NestOptions(allow_rotation=False))
    assert p.too_large
    m.update_fit(NestOptions(rotation_step=90))
    assert p.too_large
    m.update_fit(NestOptions(rotation_step=45))
    assert not p.too_large


def test_free_angle_export_matches_layout():
    tmp = Path(tempfile.mkdtemp())
    parts = make_parts(tmp, [("cross", CROSS, 3), ("l", L_SHAPE, 3)])
    mat = Material(name="T", thickness=3, parts=parts)
    opts = NestOptions(engine="libnest2d", rotation_step=30)
    mat.update_fit(opts)
    res = nest_material(mat, opts)
    assert res.warnings == [] and res.nested == 6
    sheet = res.sheets[0]
    doc = ezdxf.readfile(export_material(mat, res, str(tmp / "out"))[0])
    cut = [e for e in doc.modelspace() if e.dxf.layer in (LAYER_OUTER, LAYER_INNER)]
    ex = bbox.extents(cut)
    xs = [x for p in sheet.placements for x, _ in p.outer]
    ys = [y for p in sheet.placements for _, y in p.outer]
    assert abs(ex.extmin.x - min(xs)) < 0.1 and abs(ex.extmax.x - max(xs)) < 0.1
    assert abs(ex.extmin.y - min(ys)) < 0.1 and abs(ex.extmax.y - max(ys)) < 0.1
    assert any(c.dxftype() == "CIRCLE" for c in doc.modelspace())


def test_spyrrow_multi_sheet():
    assert available_engines()["spyrrow"]
    tmp = Path(tempfile.mkdtemp())
    parts = make_parts(tmp, [("cross", CROSS, 14), ("l", L_SHAPE, 8)])
    mat = Material(name="T", thickness=3, sheet=(1000, 700), margin=5, distance=3, parts=parts)
    opts = NestOptions(engine="spyrrow", rotation_step=90, time_limit_s=3)
    mat.update_fit(opts)
    res = nest_material(mat, opts)
    assert len(res.sheets) >= 2, len(res.sheets)
    assert res.nested == res.requested == 22, (res.nested, res.requested)
    assert res.warnings == [], res.warnings          # no overlaps, margins respected, spacing kept
    assert all(s.yield_pct > 20 for s in res.sheets[:-1])
    assert any(abs(pl.rotation_deg % 360) > 1 for s in res.sheets for pl in s.placements)  # rotation used
    files = export_material(mat, res, str(tmp / "out"))
    assert len(files) == len(res.sheets)


def test_estimate():
    tmp = Path(tempfile.mkdtemp())
    parts = make_parts(tmp, [("cross", CROSS, 14)])
    mat = Material(name="T", sheet=(1000, 700), parts=parts)
    n = estimate_sheets([mat])
    assert n >= 2
    text = describe([mat], NestOptions(engine="spyrrow", time_limit_s=30))
    assert "up to" in text and fmt_duration(30 * n) in text
    assert "few seconds" in describe([mat], NestOptions(engine="libnest2d"))
    assert fmt_duration(130) == "2 min 10 s" and fmt_duration(45) == "45 s"




def test_zero_spacing_is_safe():
    """distance = 0 used to make libnest2d / Box overlap parts (spyrrow was fine)."""
    tmp = Path(tempfile.mkdtemp())
    parts = make_parts(tmp, [("cross", CROSS, 4), ("l", L_SHAPE, 4)])
    parts.append(load_part(str(_rect(tmp, "r", 450, 400))))
    parts[-1].qty = 3
    for engine in ("box", "libnest2d"):
        mat = Material(name="T", sheet=(2500, 1250), margin=5, distance=0.0, parts=parts)
        opts = NestOptions(engine=engine)
        mat.update_fit(opts)
        res = nest_material(mat, opts)
        assert res.nested == res.requested == 11 and res.warnings == [], (engine, res.warnings[:2])


def _rect(tmp, name, w, h):
    from tests.test_core import make_plate
    make_plate(tmp / f"{name}.dxf", w, h, hole_r=40)
    return tmp / f"{name}.dxf"


if __name__ == "__main__":
    test_angles(); test_fit_check_uses_all_angles(); test_free_angle_export_matches_layout()
    test_estimate(); test_spyrrow_multi_sheet(); test_zero_spacing_is_safe()
    print("engines OK")
