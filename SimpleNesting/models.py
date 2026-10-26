"""Plain, picklable data classes shared by the GUI, the worker process and the exporters."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import strings as S

Point = tuple[float, float]
Ring = list[Point]


MAX_TIME_LIMIT_S = 30                  # hard cap for the spyrrow time limit (per sheet)


def fmt_num(v: float) -> str:
    return f"{v:g}"


def rotation_angles(allow: bool, step: float) -> list[float]:
    """Allowed rotation angles in degrees: 0, step, 2*step, ... below 360."""
    if not allow or step <= 0 or step >= 360:
        return [0.0]
    out, a = [], 0.0
    while a < 360 - 1e-9:
        out.append(round(a, 6))
        a += step
    return out


def _rotated_extent(ring: "Ring", deg: float) -> tuple[float, float]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    xs = [c * x - s * y for x, y in ring]
    ys = [s * x + c * y for x, y in ring]
    return max(xs) - min(xs), max(ys) - min(ys)


@dataclass
class Part:
    path: str
    qty: int = 1
    enabled: bool = True
    load_status: str = S.STATUS_OK          # status produced by the DXF loader
    too_large: bool = False                 # set by Material.update_fit()
    # Geometry, in mm, normalised so the outline's bounding-box minimum is (0, 0)
    outer: Ring = field(default_factory=list)
    holes: list[Ring] = field(default_factory=list)
    outer_count: int = 0                    # separate outlines found in the file (1 = OK)
    hole_count: int = 0                     # inner contours
    note: str = ""                           # human readable problem description
    raw_lines: list[Ring] = field(default_factory=list)       # all lines, kept only for files with problems
    problem_lines: list[Ring] = field(default_factory=list)   # open / stray edges (to highlight)
    problem_points: list[Point] = field(default_factory=list) # free ends of an unclosed contour
    hull: Ring = field(default_factory=list)  # convex hull of the outline (cheap rotated-size checks)
    origin: Point = (0.0, 0.0)              # bbox minimum in the (unit-scaled) source drawing
    unit_scale: float = 1.0                 # drawing units -> mm
    width: float = 0.0
    height: float = 0.0
    area: float = 0.0                       # net area (outline minus holes), mm^2
    # (index in collect_entities(), is_outer) for exporting the original entities
    source_entities: list[tuple[int, bool]] = field(default_factory=list)
    start_angle: float = 0.0                # degrees, counter-clockwise from the DXF position (all engines)
    rotation_step: float | None = None      # None = global step, 0 = fixed, 180 = 2 positions, 90 = 4 ... (spyrrow only)
    file_size: int = 0                      # of the DXF file when it was read (detects changes)
    file_mtime: float = 0.0

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def status(self) -> str:
        if self.load_status != S.STATUS_OK:
            return self.load_status
        return S.STATUS_TOO_LARGE if self.too_large else S.STATUS_OK

    @property
    def geometry_ok(self) -> bool:
        return self.load_status == S.STATUS_OK and bool(self.outer)

    def orientations(self, options: "NestOptions") -> list[float]:
        """Absolute orientations (degrees from the DXF position) this part may take.
        spyrrow: the part's own step (or the global one); libnest2d / Box: only the global step.
        The start angle shifts the whole set."""
        if options.engine == "spyrrow" and self.rotation_step is not None:
            base = rotation_angles(self.rotation_step > 0, self.rotation_step)
        else:
            base = options.angles
        out: list[float] = []
        for a in base:
            v = round((self.start_angle + a) % 360, 6)
            if v not in out:
                out.append(v)
        return out

    @property
    def nestable(self) -> bool:
        return self.enabled and self.qty > 0 and self.geometry_ok and not self.too_large


@dataclass
class Material:
    name: str = ""
    thickness: float = 0.0
    sheet: tuple[float, float] = (2500.0, 1250.0)
    distance: float = 3.0
    margin: float = 5.0
    parts: list[Part] = field(default_factory=list)
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)   # stable id (names can change)

    @property
    def label(self) -> str:
        t = f"{fmt_num(self.thickness)}mm" if self.thickness > 0 else ""
        text = f"{t} {self.name}".strip()
        return text or S.NEW_MATERIAL

    @property
    def total_parts(self) -> int:
        return sum(p.qty for p in self.parts if p.enabled)

    def update_fit(self, options: "NestOptions") -> None:
        """Flag parts that fit on the usable sheet area at none of their allowed orientations."""
        uw = self.sheet[0] - 2 * self.margin
        uh = self.sheet[1] - 2 * self.margin
        for p in self.parts:
            if not p.geometry_ok:
                p.too_large = False
                continue
            p.too_large = not any(self._fits(p, a, uw, uh) for a in p.orientations(options))

    @staticmethod
    def _fits(p: Part, angle: float, uw: float, uh: float) -> bool:
        w, h = (p.width, p.height) if angle % 360 == 0 else _rotated_extent(p.hull or p.outer, angle)
        return w <= uw + 1e-9 and h <= uh + 1e-9


@dataclass
class NestOptions:
    engine: str = "libnest2d"               # "box" | "libnest2d" | "spyrrow"
    allow_rotation: bool = True
    rotation_step: float = 90.0             # degrees
    time_limit_s: int = 20                  # spyrrow only: max seconds per sheet
    output_dir: str = ""                    # empty = do not write DXF files
    units: str = "mm"                       # drawing units of the DXF files

    @property
    def angles(self) -> list[float]:
        return rotation_angles(self.allow_rotation, self.rotation_step)


# ----------------------------------------------------------------- results ---
@dataclass
class Placement:
    part_index: int
    copy: int                               # 1-based copy number
    rotation_deg: float
    tx: float                               # translation on the sheet (mm), applied after rotation
    ty: float
    outer: Ring
    holes: list[Ring]


@dataclass
class SheetResult:
    index: int                              # 1-based
    placements: list[Placement]
    used_area: float                        # mm^2 (net)
    yield_pct: float


@dataclass
class PartSummary:
    part_index: int
    name: str
    requested: int
    nested: int
    sheet_counts: dict[int, int]            # sheet number -> copies on it


@dataclass
class MaterialResult:
    label: str
    thickness: float
    sheet: tuple[float, float]
    sheets: list[SheetResult]
    parts: list[PartSummary]
    requested: int
    nested: int
    yield_pct: float
    elapsed_s: float = 0.0
    cancelled: bool = False
    dxf_files: list[str] = field(default_factory=list)
    # Geometry needed to redraw the result without the source material
    part_names: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)    # layout problems found by the final check
    material_uid: str = ""
    fingerprint: str = ""                   # of the inputs this result was computed from
    stale: bool = False                     # inputs changed after the nesting
    edited: bool = False                    # changed by hand in the Edit tab
    edit_rev: int = 0                       # bumped on every manual edit (unsaved-changes check)
    original: list | None = None            # layout before the first manual edit (memory only, for Revert)


def fingerprint(material: Material, options: NestOptions) -> str:
    """Hash of everything that changes a nesting result. Files are identified by name, size and
    modification time, so moving a project folder does not make results stale."""
    data = {
        "sheet": list(material.sheet), "distance": material.distance, "margin": material.margin,
        "engine": options.engine, "angles": options.angles, "units": options.units,
        "parts": [[Path(p.path).name, p.qty if p.enabled else 0, p.file_size, round(p.file_mtime, 3),
                   p.start_angle, p.rotation_step]
                  for p in material.parts],
    }
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
