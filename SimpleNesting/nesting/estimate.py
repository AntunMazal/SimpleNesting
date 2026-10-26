"""Rough upper bound for the nesting time, shown before the run."""
from __future__ import annotations

import math

from ..models import MAX_TIME_LIMIT_S, Material, NestOptions

FILL_GUESS = 0.7        # typical sheet utilisation, used to guess the number of sheets


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    m, s = divmod(seconds, 60)
    return f"{m} min {s} s" if s else f"{m} min"


def estimate_sheets(materials: list[Material]) -> int:
    total = 0
    for m in materials:
        area = sum(p.area * p.qty for p in m.parts if p.nestable)
        if area <= 0:
            continue
        usable = (m.sheet[0] - 2 * m.margin) * (m.sheet[1] - 2 * m.margin)
        total += max(1, math.ceil(area / (usable * FILL_GUESS)))
    return total


def describe(materials: list[Material], options: NestOptions) -> str:
    sheets = estimate_sheets(materials)
    if sheets == 0:
        return "Add DXF files to see a time estimate."
    if options.engine != "spyrrow":
        return "Estimated: a few seconds."
    limit = max(1, min(options.time_limit_s, MAX_TIME_LIMIT_S))
    upper = sheets * limit
    return (f"Estimated: up to {fmt_duration(upper)} (about {sheets} sheet{'s' if sheets != 1 else ''} "
            f"x {limit} s). Often shorter.")
