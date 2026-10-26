"""libnest2d (python-libnest2d, import name pynest2d): "box" and "libnest2d" engines.

libnest2d's NFP placer is only reliable for CONVEX polygons (tested: crosses and L-shapes
overlap), so the "libnest2d" engine nests the convex hull of each part and "box" nests the
bounding rectangle."""
from __future__ import annotations

import math
from typing import Callable

import pynest2d as n
from shapely.geometry import Polygon

from ..geometry.dxf_loader import signed_area
from ..models import Material, NestOptions, Ring
from .common import RawPlacement, transform_ring

COORD = n.MM_IN_COORDS          # internal units per mm
ACCURACY = 0.65                 # NFP accuracy (0..1); higher = slower, tighter
MIN_SPACING = 0.01              # mm. With exactly 0 libnest2d places parts on top of each other (tested)


def _points(ring: Ring, want_ccw: bool) -> list:
    pts = list(ring)
    if (signed_area(pts) > 0) != want_ccw:
        pts.reverse()
    return [n.Point(n.mm(x), n.mm(y)) for x, y in pts]


def prepare(part, engine: str) -> tuple[Ring, float, float, float]:
    """(outline for libnest2d, mx, my, start). libnest2d knows only global rotations, so the part's
    start angle is applied by rotating the part first; (mx, my) is the shift that puts the rotated
    outline's bounding box back at the origin (needed to map the result to the original drawing)."""
    ring, mx, my = part.outer, 0.0, 0.0
    start = part.start_angle % 360
    if start:
        ring = transform_ring(ring, math.radians(start), 0.0, 0.0)
        mx, my = min(x for x, _ in ring), min(y for _, y in ring)
        ring = [(x - mx, y - my) for x, y in ring]
    if engine == "box":
        w, h = max(x for x, _ in ring), max(y for _, y in ring)
        return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)], mx, my, start
    return list(Polygon(ring).convex_hull.exterior.coords)[:-1], mx, my, start


def layout(material: Material, options: NestOptions, angles: list[float],
           progress: Callable[[int, int], None] | None,
           should_stop: Callable[[], bool] | None) -> tuple[list[list[RawPlacement]], bool]:
    W, H = material.sheet
    margin, dist = material.margin, max(material.distance, MIN_SPACING)
    ccw = n.Item.orientation() == n.Orientation.COUNTER_CLOCKWISE     # winding libnest2d expects
    prepared = {pi: prepare(p, options.engine) for pi, p in enumerate(material.parts) if p.nestable}
    owners: list[tuple[int, int]] = []
    items = []
    for pi, part in enumerate(material.parts):
        if pi in prepared:
            for c in range(1, part.qty + 1):
                items.append(n.Item(_points(prepared[pi][0], ccw)))
                owners.append((pi, c))
    total = len(items)
    if not items:
        return [], False

    # Items are inflated by distance/2 inside the bin: grow the bin by `dist` so the margin stays exact.
    box = n.Box(n.mm(W - 2 * margin + dist), n.mm(H - 2 * margin + dist))
    cfg = n.NfpConfig()
    cfg.rotations = [math.radians(a) for a in angles]
    cfg.alignment = n.Alignment.BOTTOM_LEFT
    cfg.starting_point = n.Alignment.BOTTOM_LEFT
    cfg.explore_holes = False       # no visible effect in python-libnest2d 0.1.3 (tested)
    cfg.accuracy = ACCURACY
    cfg.parallel = False            # single-threaded on purpose

    def on_progress(remaining: int) -> None:
        if progress:
            progress(total - remaining, total)

    def on_stop() -> bool:
        return bool(should_stop and should_stop())

    n.nest(items, box, n.mm(dist), cfg, n.NestControl(on_progress, on_stop))

    by_bin: dict[int, list[RawPlacement]] = {}
    for item, (pi, copy) in zip(items, owners):
        b = item.binId()
        if b < 0:
            continue
        tr = item.translation()
        _, mx, my, start = prepared[pi]
        rl = item.rotation()
        c, s = math.cos(rl), math.sin(rl)
        # libnest2d placed  R(rl) * (R(start) q - m) + t  ==  R(rl + start) q + (t - R(rl) m)
        tx = tr.x() / COORD + W / 2 - (c * mx - s * my)
        ty = tr.y() / COORD + H / 2 - (s * mx + c * my)
        by_bin.setdefault(b, []).append(RawPlacement(pi, copy, (math.degrees(rl) + start) % 360, tx, ty))
    return [by_bin[b] for b in sorted(by_bin)], on_stop()
