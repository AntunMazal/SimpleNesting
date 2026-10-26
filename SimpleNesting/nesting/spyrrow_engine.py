"""spyrrow engine (Python wrapper of the sparrow nester, MIT).

sparrow solves STRIP packing: fixed strip height, it minimises the strip LENGTH. A sheet has a
fixed length too, so sheets are produced one after another:
  1. pack everything that is left into a strip as high as the usable sheet,
  2. keep the parts that lie inside the sheet width, send the rest to the next sheet,
  3. repeat until nothing is left (or nothing more fits).
sparrow is an anytime heuristic: more time = better result, so the time limit applies PER SHEET.
Holes are not supported by spyrrow (parts are never nested into holes of other parts)."""
from __future__ import annotations

import math
from typing import Callable

from ..models import MAX_TIME_LIMIT_S, Material, NestOptions
from .common import RawPlacement, transform_ring

WORKERS = 1         # no parallelism yet
SEED = 0            # same input -> same layout


def layout(material: Material, options: NestOptions, angles: list[float],
           progress: Callable[[int, int], None] | None,
           should_stop: Callable[[], bool] | None,
           phase: Callable[[str, int], None] | None) -> tuple[list[list[RawPlacement]], bool]:
    import spyrrow

    W, H = material.sheet
    m, d = material.margin, material.distance
    usable_w, usable_h = W - 2 * m, H - 2 * m
    # spyrrow keeps `min_items_separation` from the strip edges too, so the strip is `d` larger on
    # every side; strip coordinates map to sheet coordinates by (m - d).
    strip_h = usable_h + 2 * d
    limit_x = usable_w + d
    shift = m - d

    limit = max(1, min(int(options.time_limit_s), MAX_TIME_LIMIT_S))      # hard cap, whatever the caller passes
    parts = material.parts
    remaining = {pi: p.qty for pi, p in enumerate(parts) if p.nestable}
    closed = {pi: parts[pi].outer + [parts[pi].outer[0]] for pi in remaining}
    total = sum(remaining.values())
    copies = {pi: 0 for pi in remaining}
    orientations = {pi: parts[pi].orientations(options) for pi in remaining}     # per part: start angle + own step
    sheets: list[list[RawPlacement]] = []
    done = 0
    cancelled = False

    while remaining:
        if should_stop and should_stop():
            cancelled = True
            break
        items = [spyrrow.Item(f"p{pi}", closed[pi], demand=q, allowed_orientations=orientations[pi])
                 for pi, q in remaining.items()]
        if phase:
            phase(f"sheet {len(sheets) + 1}: placing {sum(remaining.values())} parts", limit)
        instance = spyrrow.StripPackingInstance(material.label, strip_height=strip_h, items=items)
        cfg = spyrrow.StripPackingConfig(
            early_termination=True, total_computation_time=limit, num_workers=WORKERS,
            seed=SEED, min_items_separation=d if d > 0 else None)
        solution = instance.solve(cfg)

        sheet: list[RawPlacement] = []
        kept: dict[int, int] = {}
        for pl in solution.placed_items:
            pi = int(pl.id[1:])
            rot = pl.rotation % 360
            tx, ty = pl.translation
            outer = transform_ring(parts[pi].outer, math.radians(rot), tx, ty)
            if max(x for x, _ in outer) > limit_x + 1e-6:
                continue                                    # belongs to a later sheet
            copies[pi] += 1
            kept[pi] = kept.get(pi, 0) + 1
            sheet.append(RawPlacement(pi, copies[pi], rot, tx + shift, ty + shift))
        if not sheet:
            break                                           # nothing fits any more
        sheets.append(sheet)
        for pi, k in kept.items():
            remaining[pi] -= k
            if remaining[pi] <= 0:
                del remaining[pi]
        done += len(sheet)
        if progress:
            progress(done, total)
    return sheets, cancelled
