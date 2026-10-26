"""Nesting entry point: picks the engine, builds the result and checks it independently."""
from __future__ import annotations

import importlib.util
import math
import time
from typing import Callable

from shapely.geometry import Polygon
from shapely.strtree import STRtree

from ..models import (Material, MaterialResult, NestOptions, PartSummary, Placement, SheetResult,
                      fingerprint)
from .common import RawPlacement, transform_ring

ENGINES = {"box": "pynest2d", "libnest2d": "pynest2d", "spyrrow": "spyrrow"}   # engine -> module


def available_engines() -> dict[str, bool]:
    return {name: importlib.util.find_spec(mod) is not None for name, mod in ENGINES.items()}


def validate_layout(material: Material, sheets: list[SheetResult], tol_area: float = 0.01) -> list[str]:
    """Independent shapely check of the final layout: overlaps and parts outside the margin."""
    W, H = material.sheet
    m = material.margin
    warnings = []
    for sh in sheets:
        polys = [Polygon(p.outer, p.holes) for p in sh.placements]
        for poly, pl in zip(polys, sh.placements):
            x0, y0, x1, y1 = poly.bounds
            if x0 < m - 0.05 or y0 < m - 0.05 or x1 > W - m + 0.05 or y1 > H - m + 0.05:
                warnings.append(f"Sheet {sh.index}: {material.parts[pl.part_index].name} is outside the margin")
        tree = STRtree(polys)
        for i, poly in enumerate(polys):
            for j in tree.query(poly):
                if j > i and poly.intersection(polys[j]).area > tol_area:
                    a, b = sh.placements[i], sh.placements[j]
                    warnings.append(f"Sheet {sh.index}: {material.parts[a.part_index].name} overlaps "
                                    f"{material.parts[b.part_index].name}")
    return warnings


def nest_material(material: Material, options: NestOptions,
                  progress: Callable[[int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None,
                  phase: Callable[[str, int], None] | None = None) -> MaterialResult:
    t0 = time.perf_counter()
    angles = options.angles              # global step (libnest2d); spyrrow reads each part's own orientations
    if options.engine == "spyrrow":
        from . import spyrrow_engine
        raw_sheets, cancelled = spyrrow_engine.layout(material, options, angles, progress, should_stop, phase)
    elif options.engine in ("box", "libnest2d"):
        from . import libnest_engine
        raw_sheets, cancelled = libnest_engine.layout(material, options, angles, progress, should_stop)
    else:
        raise ValueError(f"Unknown nesting engine: {options.engine}")

    res = build_result(material, raw_sheets, time.perf_counter() - t0, cancelled)
    res.material_uid, res.fingerprint = material.uid, fingerprint(material, options)
    return res


def summarize(material: Material, sheets: list[SheetResult]) -> list[PartSummary]:
    placed: dict[int, dict[int, int]] = {}
    for sh in sheets:
        for pl in sh.placements:
            counts = placed.setdefault(pl.part_index, {})
            counts[sh.index] = counts.get(sh.index, 0) + 1
    return [PartSummary(pi, part.name, part.qty, sum(placed.get(pi, {}).values()), placed.get(pi, {}))
            for pi, part in enumerate(material.parts) if part.enabled]


def build_result(material: Material, raw_sheets: list[list[RawPlacement]], elapsed_s: float = 0.0,
                 cancelled: bool = False, validate: bool = True) -> MaterialResult:
    """Placements (part, copy, rotation, translation) -> geometry, yields and the final check."""
    sheet_area = material.sheet[0] * material.sheet[1]
    sheets: list[SheetResult] = []
    placed_counts: dict[int, dict[int, int]] = {}
    for number, raw in enumerate(raw_sheets, start=1):
        placements = []
        for r in raw:
            part = material.parts[r.part_index]
            rot_deg = round(r.rot_deg % 360, 4)
            rad = math.radians(rot_deg)
            placements.append(Placement(
                part_index=r.part_index, copy=r.copy, rotation_deg=rot_deg, tx=r.tx, ty=r.ty,
                outer=transform_ring(part.outer, rad, r.tx, r.ty),
                holes=[transform_ring(h, rad, r.tx, r.ty) for h in part.holes]))
            counts = placed_counts.setdefault(r.part_index, {})
            counts[number] = counts.get(number, 0) + 1
        used = sum(material.parts[p.part_index].area for p in placements)
        sheets.append(SheetResult(index=number, placements=placements, used_area=used,
                                  yield_pct=100.0 * used / sheet_area))
    total_used = sum(s.used_area for s in sheets)

    summaries = summarize(material, sheets)

    return MaterialResult(
        label=material.label, thickness=material.thickness, sheet=material.sheet, sheets=sheets,
        parts=summaries, requested=sum(s.requested for s in summaries), nested=sum(s.nested for s in summaries),
        yield_pct=100.0 * total_used / (sheet_area * sum(1 for s in sheets if s.placements)) if total_used else 0.0,
        elapsed_s=elapsed_s, cancelled=cancelled,
        part_names=[p.name for p in material.parts],
        warnings=validate_layout(material, sheets) if validate else [])
