"""Real-world-like shapes: concave parts must never overlap; shapely contour building."""
import tempfile
from pathlib import Path

import ezdxf

from SimpleNesting import strings as S
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions
from SimpleNesting.nesting.engine import nest_material, validate_layout

CROSS = [(100, 0), (250, 0), (250, 100), (350, 100), (350, 250), (250, 250), (250, 350), (100, 350),
         (100, 250), (0, 250), (0, 100), (100, 100)]
L_SHAPE = [(0, 0), (400, 0), (400, 120), (120, 120), (120, 380), (0, 380)]
NOTCH = [(0, 0), (300, 0), (300, 40), (260, 40), (260, 90), (300, 90), (300, 200), (0, 200)]


def poly_dxf(path, pts, holes=()):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(pts, close=True)
    for cx, cy, r in holes:
        msp.add_circle((cx, cy), r)
    doc.saveas(path)


def test_concave_parts_do_not_overlap():
    tmp = Path(tempfile.mkdtemp())
    parts = []
    for name, pts, qty in (("cross", CROSS, 4), ("l", L_SHAPE, 4), ("notch", NOTCH, 3)):
        poly_dxf(tmp / f"{name}.dxf", pts, holes=[(50, 50, 20)] if name == "l" else ())
        p = load_part(str(tmp / f"{name}.dxf"))
        assert p.status == S.STATUS_OK, (name, p.status)
        p.qty = qty
        parts.append(p)
    mat = Material(name="T", thickness=3, parts=parts)
    mat.update_fit(NestOptions())
    for allow_rot in (False, True):
        for mode in ("libnest2d", "box"):
            res = nest_material(mat, NestOptions(engine=mode, allow_rotation=allow_rot))
            assert res.nested == res.requested == 11, res
            assert res.warnings == [], (mode, allow_rot, res.warnings)


def test_validation_catches_overlap():
    tmp = Path(tempfile.mkdtemp())
    poly_dxf(tmp / "a.dxf", CROSS)
    p = load_part(str(tmp / "a.dxf"))
    p.qty = 2
    mat = Material(name="T", thickness=3, parts=[p])
    res = nest_material(mat, NestOptions())
    sheet = res.sheets[0]
    sheet.placements[1] = sheet.placements[0]          # force two parts onto the same spot
    assert validate_layout(mat, [sheet])


def test_contours_from_arcs_and_loose_entities():
    tmp = Path(tempfile.mkdtemp())
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    # rounded rectangle from separate LINEs and ARCs (as exported by many CAD programs)
    msp.add_line((20, 0), (180, 0)); msp.add_arc((180, 20), 20, 270, 360)
    msp.add_line((200, 20), (200, 80)); msp.add_arc((180, 80), 20, 0, 90)
    msp.add_line((180, 100), (20, 100)); msp.add_arc((20, 80), 20, 90, 180)
    msp.add_line((0, 80), (0, 20)); msp.add_arc((20, 20), 20, 180, 270)
    msp.add_circle((100, 50), 15)
    doc.saveas(tmp / "rr.dxf")
    p = load_part(str(tmp / "rr.dxf"))
    assert p.status == S.STATUS_OK and len(p.holes) == 1
    assert abs(p.width - 200) < 0.1 and abs(p.height - 100) < 0.1
    assert abs(p.area - (200 * 100 - (4 - 3.14159265) * 400 - 3.14159265 * 225)) < 5
    assert sum(1 for _, outer in p.source_entities if outer) == 8      # 4 lines + 4 arcs
    assert sum(1 for _, outer in p.source_entities if not outer) == 1  # the circle


def test_bad_inputs():
    tmp = Path(tempfile.mkdtemp())
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (100, 0)); doc.modelspace().add_line((100, 0), (100, 50))
    doc.saveas(tmp / "open.dxf")
    assert load_part(str(tmp / "open.dxf")).status == S.STATUS_OPEN
    doc = ezdxf.new("R2010")
    doc.saveas(tmp / "empty.dxf")
    assert load_part(str(tmp / "empty.dxf")).status == S.STATUS_EMPTY
    # a part lying inside the hole of another one -> not a single outline
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (300, 0), (300, 300), (0, 300)], close=True)
    msp.add_circle((150, 150), 100)
    msp.add_circle((150, 150), 50)
    doc.saveas(tmp / "island.dxf")
    assert load_part(str(tmp / "island.dxf")).status == S.STATUS_MULTI
    (tmp / "junk.dxf").write_text("this is not a dxf file")
    assert load_part(str(tmp / "junk.dxf")).status in (S.STATUS_ERROR, S.STATUS_EMPTY)


if __name__ == "__main__":
    test_concave_parts_do_not_overlap()
    test_validation_catches_overlap()
    test_contours_from_arcs_and_loose_entities()
    test_bad_inputs()
    print("shapes OK")
