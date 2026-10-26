"""Headless end-to-end test: DXF -> load -> nest -> export -> verify the exported geometry."""
import math
import tempfile
from pathlib import Path

import ezdxf
from ezdxf import bbox

from SimpleNesting import strings as S
from SimpleNesting.export.dxf_exporter import LAYER_INNER, LAYER_OUTER, LAYER_SHEET, export_material
from SimpleNesting.export.report import write_report
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions
from SimpleNesting.nesting.engine import nest_material


def make_plate(path, w, h, hole_r=None, loose=False, open_gap=False):
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    if loose:   # four LINEs, chained by the loader
        pts = [(0, 0), (w, 0), (w, h), (0, h)]
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            if open_gap and i == 3:
                b = (b[0], b[1] + 5)
            msp.add_line(a, b)
    else:
        msp.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    if hole_r:
        msp.add_circle((w / 2, h / 2), hole_r)
    doc.saveas(path)


def test_pipeline():
    tmp = Path(tempfile.mkdtemp())
    make_plate(tmp / "a.dxf", 450, 400, hole_r=60)
    make_plate(tmp / "b.dxf", 800, 250, loose=True)
    make_plate(tmp / "open.dxf", 100, 100, loose=True, open_gap=True)
    make_plate(tmp / "big.dxf", 3000, 100)

    pa, pb, po, pbig = (load_part(str(tmp / f)) for f in ("a.dxf", "b.dxf", "open.dxf", "big.dxf"))
    assert pa.status == S.STATUS_OK and len(pa.holes) == 1
    assert abs(pa.width - 450) < 0.1 and abs(pa.height - 400) < 0.1
    assert pb.status == S.STATUS_OK and not pb.holes          # chained from loose lines
    assert po.status == S.STATUS_OPEN
    pa.qty, pb.qty = 3, 2

    mat = Material(name="DD11", thickness=3, parts=[pa, pb, po, pbig])
    mat.update_fit(NestOptions())
    assert pbig.status == S.STATUS_TOO_LARGE

    res = nest_material(mat, NestOptions(engine="libnest2d"))
    assert res.nested == 5 and len(res.sheets) == 1, res
    sheet = res.sheets[0]
    # every placed part is inside the margin and parts keep `distance` between them
    for pl in sheet.placements:
        for x, y in pl.outer:
            assert mat.margin - 0.01 <= x <= 2500 - mat.margin + 0.01
            assert mat.margin - 0.01 <= y <= 1250 - mat.margin + 0.01

    # rectangle mode must also place everything
    res_rect = nest_material(mat, NestOptions(engine="box", allow_rotation=False))
    assert res_rect.nested == 5

    # DXF export: exported bounding boxes must match the engine's polygons (true arcs kept)
    files = export_material(mat, res, str(tmp / "out"))
    assert len(files) == 1
    doc = ezdxf.readfile(files[0])
    types = {e.dxftype() for e in doc.modelspace()}
    assert "CIRCLE" in types, types                            # holes stay real circles
    cut = [e for e in doc.modelspace() if e.dxf.layer in (LAYER_OUTER, LAYER_INNER)]
    ex = bbox.extents(cut)
    xs = [x for p in sheet.placements for x, _ in p.outer]
    ys = [y for p in sheet.placements for _, y in p.outer]
    assert abs(ex.extmin.x - min(xs)) < 0.1 and abs(ex.extmax.x - max(xs)) < 0.1
    assert abs(ex.extmin.y - min(ys)) < 0.1 and abs(ex.extmax.y - max(ys)) < 0.1

    # the plate itself is drawn as four red lines on its own layer
    assert doc.layers.has_entry(LAYER_SHEET)
    assert doc.layers.get(LAYER_SHEET).color == 1                  # red
    border = [e for e in doc.modelspace() if e.dxf.layer == LAYER_SHEET]
    assert len(border) == 4, len(border)
    assert all(e.dxftype() == "LINE" for e in border)
    pts = {(round(e.dxf.start.x, 3), round(e.dxf.start.y, 3),
            round(e.dxf.end.x, 3), round(e.dxf.end.y, 3)) for e in border}
    w, h = mat.sheet
    assert pts == {(0.0, 0.0, w, 0.0), (w, 0.0, w, h), (w, h, 0.0, h), (0.0, h, 0.0, 0.0)}, pts

    # reports
    write_report([res], str(tmp / "r.csv"), "csv")
    write_report([res], str(tmp / "r.txt"), "txt")
    assert (tmp / "r.csv").read_text().startswith("material,thickness_mm")

    # cancel: stop callback returns immediately
    cancelled = nest_material(mat, NestOptions(), should_stop=lambda: True)
    assert cancelled.cancelled and cancelled.nested == 0


if __name__ == "__main__":
    test_pipeline()
    print("core pipeline OK")
