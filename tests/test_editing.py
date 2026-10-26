"""Collision-free manual editing: exact contact, sliding, rotation, teleport into holes, snapping, and a
randomised stress test that must never produce an overlap."""
import math
import random
import tempfile
import time
from pathlib import Path

import ezdxf

from SimpleNesting.editing import EPS, SheetEditor
from SimpleNesting.geometry.dxf_loader import load_part
from SimpleNesting.models import Material, NestOptions, Placement, SheetResult
from SimpleNesting.nesting.common import transform_ring
from SimpleNesting.nesting.engine import nest_material
from tests.test_core import make_plate
from tests.test_shapes import CROSS, L_SHAPE, NOTCH, poly_dxf

TMP = Path(tempfile.mkdtemp())


def rect(name, w, h, hole_r=None):
    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    if hole_r:
        doc.modelspace().add_circle((w / 2, h / 2), hole_r)
    doc.saveas(TMP / f"{name}.dxf")
    return load_part(str(TMP / f"{name}.dxf"))


def editor_for(parts, poses, sheet=(1000, 500), margin=5, distance=3):
    """poses: [(part_index, rot, tx, ty)] -> SheetEditor on a hand-made sheet."""
    mat = Material(name="T", sheet=sheet, margin=margin, distance=distance, parts=parts)
    sheet_res = SheetResult(1, [Placement(pi, n, rot, tx, ty, [], []) for n, (pi, rot, tx, ty) in enumerate(poses, 1)], 0, 0)
    return SheetEditor(mat, sheet_res)


def test_contact_slide_wall_pull_snap():
    sq = rect("sq", 100, 100)
    ed = editor_for([sq], [(0, 0, 100, 100), (0, 0, 400, 100)])
    r = ed.move(1, -500, 0)                                   # B towards A: stops exactly `distance` away
    assert r.blocked and abs(ed.items[1].tx - 203.0) < EPS and abs(r.dx - (203 - 400)) < EPS
    ed = editor_for([sq], [(0, 0, 100, 100), (0, 0, 400, 100)])
    r = ed.move(1, -500, 40)                                  # diagonal: hits A's face, then slides up along it
    assert r.blocked and abs(ed.items[1].tx - 203.0) < EPS and abs(ed.items[1].ty - 140.0) < 0.05, ed.pose(1)
    ed = editor_for([sq], [(0, 0, 100, 100), (0, 0, 400, 100)])
    ed.move(1, 5000, 0)                                       # sheet edge: margin 5 -> tx = 1000 - 5 - 100
    assert abs(ed.items[1].tx - 895) < EPS
    ed = editor_for([sq], [(0, 0, 100, 100), (0, 0, 400, 100)])
    r = ed.pull(1, -1, 0)                                     # pull left until it touches A
    assert abs(ed.items[1].tx - 203) < EPS and r.blocked
    ed.pull(1, 0, 1)
    assert abs(ed.items[1].ty - (500 - 5 - 100)) < EPS        # ... then up to the margin
    ed = editor_for([sq], [(0, 0, 100, 100), (0, 0, 210, 100)])
    assert ed.snap(1, 5).dx == 0 and abs(ed.items[1].tx - 210) < EPS     # 7 mm of clearance: outside the 5 mm range
    r = ed.snap(1, 10)
    assert abs(ed.items[1].tx - 203) < EPS and r.dx < 0       # closes the gap to exactly 3 mm
    ed = editor_for([sq], [(0, 0, 9, 100)])
    ed.snap(0, 5)
    assert abs(ed.items[0].tx - 5) < EPS                      # edge snap to the margin
    assert ed.min_gap() == float("inf")


def test_no_tunnelling_through_thin_parts():
    sq, bar = rect("sq2", 100, 100), rect("thin", 2, 300)
    ed = editor_for([sq, bar], [(0, 0, 50, 100), (1, 0, 300, 50)])
    ed.move(0, 600, 0)                                        # a 2 mm wall is between: must stop in front of it
    assert ed.items[0].tx + 100 <= 300 - 3 + EPS, ed.items[0].tx


def test_rotation_about_centroid_blocks_and_nudges():
    bar, block = rect("bar", 400, 40), rect("blk", 100, 100)
    ed = editor_for([bar, block], [(0, 0, 100, 100), (1, 0, 250, 200)])
    px, py = ed.pivot(0)
    assert abs(px - 300) < 1e-6 and abs(py - 120) < 1e-6      # centroid of the outer contour
    r = ed.rotate(0, 90)                                      # the block is in the way of a vertical bar
    assert r.blocked and 0 < r.delta < 90, r
    assert ed.clearance(ed._session(0), ed.items[0].poly)[0] >= -EPS and ed.min_gap() >= 3 - 2 * EPS
    assert math.hypot(*ed.pivot(0)) > 0 and abs(ed.pivot(0)[0] - 300) < 1e-6   # it turned about the centroid
    short = rect("short", 100, 40)
    ed = editor_for([short], [(0, 0, 100, 5)])                # next to the bottom margin
    r = ed.rotate(0, 90)
    assert r.blocked and r.delta < 90
    ed = editor_for([short], [(0, 0, 100, 5)])
    r = ed.rotate(0, 90, nudge=32)                            # nudged up: the full turn is possible
    assert not r.blocked and abs(r.delta - 90) < 1e-9 and r.nudge[1] > 0
    assert abs(ed.items[0].rot - 90) < 1e-6 and ed.clearance(ed._session(0), ed.items[0].poly)[0] >= -EPS


def test_teleport_into_a_hole():
    plate, small = rect("plate", 600, 600, hole_r=200), rect("small", 100, 100)
    ed = editor_for([plate, small], [(0, 0, 100, 100), (1, 0, 800, 100)], sheet=(1000, 800))
    r = ed.move_to(1, 800 - 600, 100 + 100, teleport=False)   # walking: blocked by the plate
    assert r.blocked and ed.items[1].tx > 700 - 1, ed.pose(1)
    ed = editor_for([plate, small], [(0, 0, 100, 100), (1, 0, 800, 100)], sheet=(1000, 800))
    r = ed.move_to(1, 350, 350, teleport=True)                # lift and place: inside the hole, 200 mm radius
    assert r.teleported and ed.pose(1) == (0.0, 350, 350) and ed.min_gap() >= 3
    assert not ed.move_to(1, 150, 150, teleport=True).teleported   # not valid: it would sit in the plate


def test_pose_api_and_placements_match_the_export_representation():
    sq = rect("sq3", 100, 100, hole_r=20)
    ed = editor_for([sq], [(0, 0, 100, 100), (0, 0, 400, 100)])
    assert not ed.set_pose(1, 0, 150, 100)                    # overlaps A
    assert ed.set_pose(1, 30, 400, 200)
    for pl, it in zip(ed.placements(), ed.items):
        part = ed.material.parts[pl.part_index]
        ref = transform_ring(part.outer, math.radians(pl.rotation_deg), pl.tx, pl.ty)
        ours = list(it.poly.exterior.coords)[:-1]
        assert all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 for a, b in zip(sorted(ref), sorted(ours)))


def test_random_operations_never_overlap():
    parts = []
    for i, (w, h, r, q) in enumerate([(450, 400, 60, 3), (450, 250, 0, 3), (800, 400, 90, 2), (300, 400, 0, 3),
                                      (300, 200, 30, 4), (200, 200, 0, 4)]):
        parts.append(rect(f"s{i}", w, h, r or None))
        parts[-1].qty = q
    for n, pts, q in (("c", CROSS, 5), ("l", L_SHAPE, 5), ("n", NOTCH, 5)):
        poly_dxf(TMP / f"{n}.dxf", pts)
        p = load_part(str(TMP / f"{n}.dxf"))
        p.qty = q
        parts.append(p)
    mat = Material(name="T", sheet=(4000, 2000), margin=5, distance=3, parts=parts)
    opts = NestOptions(engine="libnest2d")
    mat.update_fit(opts)
    res = nest_material(mat, opts)
    ed = SheetEditor(mat, res.sheets[0])
    n_items = len(ed.items)
    assert n_items >= 30 and ed.min_gap() >= 3 - EPS, n_items
    rnd = random.Random(7)
    times: dict[str, list[float]] = {}
    blocked = ops = 0
    for step in range(2500):
        i = rnd.randrange(n_items)
        kind = rnd.choice(["move", "move_noslide", "rotate", "rotate_nudge", "snap", "pull", "teleport"])
        t0 = time.perf_counter()
        if kind in ("move", "move_noslide"):
            a, dist = rnd.uniform(0, 2 * math.pi), rnd.choice([5, 30, 150, 600])
            r = ed.move(i, dist * math.cos(a), dist * math.sin(a), slide=(kind == "move"))
            blocked += r.blocked
        elif kind.startswith("rotate"):
            r = ed.rotate(i, rnd.choice([-1, 1]) * rnd.uniform(1, 180), nudge=8 if kind.endswith("nudge") else 0)
            blocked += r.blocked
        elif kind == "snap":
            ed.snap(i, rnd.choice([2, 10, 50]))
        elif kind == "pull":
            a = rnd.choice([0, 90, 180, 270]) + rnd.uniform(-20, 20)
            ed.pull(i, math.cos(math.radians(a)), math.sin(math.radians(a)))
        else:
            ed.move_to(i, rnd.uniform(0, 3900), rnd.uniform(0, 1900), teleport=True)
        times.setdefault(kind, []).append(time.perf_counter() - t0)
        ops += 1
        assert ed.clearance(ed._session(i), ed.items[i].poly)[0] >= -2 * EPS, (step, kind)
        if step % 100 == 99:
            assert ed.min_gap() >= 3 - 2 * EPS, (step, kind, ed.min_gap())
    assert ed.min_gap() >= 3 - 2 * EPS
    for it in ed.items:
        x0, y0, x1, y1 = it.poly.bounds
        assert x0 >= 5 - 2 * EPS and y0 >= 5 - 2 * EPS and x1 <= 3995 + 2 * EPS and y1 <= 1995 + 2 * EPS
    print(f"  stress: {n_items} parts, {ops} random operations, {blocked} stopped by contact, min gap {ed.min_gap():.4f} mm")
    for kind, ts in sorted(times.items()):
        print(f"    {kind:13s} avg {1000 * sum(ts) / len(ts):6.2f} ms   max {1000 * max(ts):6.1f} ms   ({len(ts)}x)")
    assert max(max(ts) for ts in times.values()) < 1.0       # nothing takes a second



def nested_job(sheet=(1500, 900)):
    parts = []
    for i, (w, h, r, q) in enumerate([(450, 300, 50, 3), (300, 200, 0, 4), (200, 200, 0, 4), (500, 150, 0, 2)]):
        parts.append(rect(f"j{i}", w, h, r or None))
        parts[-1].qty = q
    mat = Material(name="T", sheet=sheet, margin=5, distance=3, parts=parts)
    opts = NestOptions(engine="libnest2d")
    mat.update_fit(opts)
    return mat, nest_material(mat, opts)


def test_add_remove_numeric_and_result_helpers():
    from SimpleNesting import editing as E
    mat, res = nested_job()
    n_before = res.nested
    assert res.requested == n_before == 13 and E.unplaced(mat, res) == {}
    ed = SheetEditor(mat, res.sheets[0])
    k = ed.index_of((1, 2))
    assert k >= 0 and ed.index_of((99, 9)) == -1
    # numeric entry: all or nothing
    x0, y0 = ed.bbox_min(k)
    assert not ed.set_bbox_min(k, 2.0, 2.0)                    # outside the 5 mm margin
    assert ed.bbox_min(k) == (x0, y0)
    assert ed.set_bbox_min(k, x0, y0)                          # the same place is valid
    ang0 = ed.items[k].rot
    ok = ed.set_rotation(k, ang0 + 90)
    assert (not ok) or abs(((ed.items[k].rot - ang0 - 90 + 180) % 360) - 180) < 1e-6
    assert ed.min_gap() >= 3 - 2 * EPS
    # remove -> unplaced -> add: the part lands on a free, valid spot
    item = ed.remove(k)
    E.commit_sheet(mat, res, 0, ed)
    assert res.edited and res.original is not None and res.edit_rev == 1
    assert E.unplaced(mat, res) == {item.part_index: [item.copy]} and res.nested == n_before - 1
    i = ed.add(item.part_index, item.copy, [0.0, 90.0])
    assert i is not None and ed.min_gap() >= 3 - 2 * EPS
    assert ed.clearance(ed._session(i), ed.items[i].poly)[0] >= -EPS
    E.commit_sheet(mat, res, 0, ed)
    assert E.unplaced(mat, res) == {} and res.nested == n_before and res.warnings == [] and res.edit_rev == 2
    # a part that cannot fit anywhere: add() says no
    huge = rect("huge", 1600, 100)
    mat.parts.append(huge)
    assert ed.add(len(mat.parts) - 1, 1, [0.0, 90.0]) is None
    mat.parts.pop()
    # an extra empty sheet takes a part at the bottom-left corner
    E.add_sheet(mat, res)
    assert len(res.sheets) == len(res.sheets) and res.sheets[-1].placements == []
    ed2 = SheetEditor(mat, res.sheets[-1])
    j = ed2.add(0, 1, [0.0])
    assert j is not None and abs(ed2.bbox_min(j)[0] - 5) < 0.2 and abs(ed2.bbox_min(j)[1] - 5) < 0.2
    # revert restores the nesting result exactly
    layout_after = E.raw_state(res)
    assert E.revert(mat, res) and not res.edited and E.revert(mat, res) is False
    assert len(res.sheets) == len(E.raw_state(res)) and res.nested == n_before
    assert E.raw_state(res) != layout_after


def test_project_keeps_edits_and_empty_sheets():
    from SimpleNesting import editing as E, project
    mat, res = nested_job()
    ed = SheetEditor(mat, res.sheets[0])
    ed.move(0, 5, 5)
    E.commit_sheet(mat, res, 0, ed)
    E.add_sheet(mat, res)
    data = project.to_dict([mat], {}, [res], TMP)
    loaded, rep = project.materials_from_dict(data, TMP, "mm")
    back = project.results_from_dict(data, [Material(name="T", sheet=mat.sheet, margin=5, distance=3, parts=mat.parts, uid=mat.uid)],
                                     NestOptions(engine="libnest2d"), rep)
    assert len(back) == 1 and back[0].edited and len(back[0].sheets) == len(res.sheets) and back[0].sheets[-1].placements == []
    assert abs(back[0].yield_pct - res.yield_pct) < 1e-9 and back[0].nested == res.nested
    a = project.snapshot([mat], {}, [res])
    E.mark_edited(res)
    assert project.snapshot([mat], {}, [res]) != a              # an edit makes the project dirty


if __name__ == "__main__":
    test_contact_slide_wall_pull_snap()
    test_no_tunnelling_through_thin_parts()
    test_rotation_about_centroid_blocks_and_nudges()
    test_teleport_into_a_hole()
    test_pose_api_and_placements_match_the_export_representation()
    test_add_remove_numeric_and_result_helpers()
    test_project_keeps_edits_and_empty_sheets()
    test_random_operations_never_overlap()
    print("editing OK")
