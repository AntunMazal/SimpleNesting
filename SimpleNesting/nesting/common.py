"""Pieces shared by all nesting engines."""
from __future__ import annotations

import math
from typing import NamedTuple

from ..models import Ring


class RawPlacement(NamedTuple):
    """One placed copy. The part's normalised outline (bbox minimum at the origin) is rotated by
    rot_deg (counter-clockwise, about the origin) and then moved by (tx, ty) on the sheet, mm."""
    part_index: int
    copy: int
    rot_deg: float
    tx: float
    ty: float


def transform_ring(ring: Ring, rot_rad: float, tx: float, ty: float) -> Ring:
    c, s = math.cos(rot_rad), math.sin(rot_rad)
    return [(c * x - s * y + tx, s * x + c * y + ty) for x, y in ring]
