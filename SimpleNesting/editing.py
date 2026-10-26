"""Collision-free manual editing of ONE sheet (pure Python + shapely, no Qt).

Rules: parts keep at least `distance` between each other and at least `margin` from the sheet edge.
A pose is (rotation, tx, ty): polygon = R(rot) * local_outline + (tx, ty) - the same representation the
nesting result and the DXF export use, so edits need no conversion.

The collision logic rests on one fact: the distance between two shapes is 1-Lipschitz. A part with
clearance c can move c in ANY direction (or rotate by c / R, R = its largest radius around the pivot)
without touching anything. A drag is therefore traced like a sphere: leap by the clearance, stop at
contact. That finds the exact contact position without fixed-step sampling. Leaps are never shorter than
min(0.5 mm, spacing) and never longer than the clearance, so a thin part cannot be skipped over."""
from __future__ import annotations

import math
from dataclasses import dataclass

from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.ops import nearest_points
from shapely.strtree import STRtree

from .models import Material, MaterialResult, Placement, SheetResult
from .nesting.common import transform_ring
from .nesting.engine import summarize, validate_layout

EPS = 1e-3                  # mm: a pose is valid if clearance >= -EPS
MIN_SPACING = 0.01          # same minimum as the nesting engines
MAX_ITER = 600


@dataclass
class Item:
    part_index: int
    copy: int
    rot: float              # degrees, counter-clockwise
    tx: float
    ty: float
    poly: Polygon


@dataclass
class MoveResult:
    dx: float               # displacement actually applied
    dy: float
    blocked: bool           # stopped by a part / the sheet edge before the target
    teleported: bool = False


@dataclass
class RotateResult:
    delta: float            # degrees actually rotated
    blocked: bool
    nudge: tuple[float, float] = (0.0, 0.0)


class _Session:
    """The other parts as seen from the part being edited."""

    def __init__(self, editor: "SheetEditor", i: int):
        self.ids = [k for k in range(len(editor.items)) if k != i]
        self.polys = [editor.items[k].poly for k in self.ids]
        self.tree = STRtree(self.polys) if self.polys else None


class SheetEditor:
    def __init__(self, material: Material, sheet: SheetResult):
        self.material = material
        self.W, self.H = material.sheet
        self.margin = material.margin
        self.d = max(material.distance, MIN_SPACING)
        self.h_min = min(0.5, self.d)
        self._base: dict[int, Polygon] = {}
        self._sessions: dict[int, _Session] = {}
        self.items: list[Item] = []
        for pl in sheet.placements:
            self.items.append(Item(pl.part_index, pl.copy, pl.rotation_deg, pl.tx, pl.ty,
                                   self._poly(pl.part_index, pl.rotation_deg, pl.tx, pl.ty)))

    # --------------------------------------------------------------- geometry ---
    def _local(self, part_index: int) -> Polygon:
        if part_index not in self._base:
            p = self.material.parts[part_index]
            self._base[part_index] = Polygon(p.outer, p.holes)
        return self._base[part_index]

    def _poly(self, part_index: int, rot: float, tx: float, ty: float) -> Polygon:
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        return affinity.affine_transform(self._local(part_index), [c, -s, s, c, tx, ty])

    def pivot(self, i: int) -> tuple[float, float]:
        """Centroid of the OUTER contour of part i, in sheet coordinates."""
        it = self.items[i]
        c = Polygon(self.material.parts[it.part_index].outer).centroid
        r = math.radians(it.rot)
        return (math.cos(r) * c.x - math.sin(r) * c.y + it.tx, math.sin(r) * c.x + math.cos(r) * c.y + it.ty)

    def _session(self, i: int) -> _Session:
        if i not in self._sessions:
            self._sessions[i] = _Session(self, i)
        return self._sessions[i]

    # ------------------------------------------------------------- clearance ---
    def clearance(self, sess: _Session, poly: Polygon) -> tuple[float, str, int]:
        """(clearance in mm, limiter: 'L','B','R','T' = sheet edge or 'part', other item index)."""
        x0, y0, x1, y1 = poly.bounds
        m = self.margin
        c, kind = min((x0 - m, "L"), (y0 - m, "B"), (self.W - m - x1, "R"), (self.H - m - y1, "T"))
        other = -1
        if sess.tree is not None:
            idx, dist = sess.tree.query_nearest(poly, return_distance=True, all_matches=False)
            cp = float(dist[0]) - self.d
            if cp < c:
                c, kind, other = cp, "part", sess.ids[int(idx[0])]
        return c, kind, other

    def _normal(self, sess: _Session, poly: Polygon) -> tuple[float, float] | None:
        """Unit vector from the part towards what limits it."""
        _, kind, other = self.clearance(sess, poly)
        walls = {"L": (-1.0, 0.0), "R": (1.0, 0.0), "B": (0.0, -1.0), "T": (0.0, 1.0)}
        if kind in walls:
            return walls[kind]
        a, b = nearest_points(poly, self.items[other].poly)
        vx, vy = b.x - a.x, b.y - a.y
        n = math.hypot(vx, vy)
        return (vx / n, vy / n) if n > 1e-9 else None

    def is_valid_pose(self, part_index: int, rot: float, tx: float, ty: float, ignore: int = -1) -> bool:
        """Would this pose be free on this sheet? `ignore` = index of the part's own entry if it is on the sheet
        (a part coming from another sheet passes -1)."""
        return self.clearance(self._session(ignore), self._poly(part_index, rot, tx, ty))[0] >= -EPS

    def add_item(self, part_index: int, copy: int, rot: float, tx: float, ty: float) -> int:
        """Insert a part at an explicit pose (the caller has checked it with is_valid_pose)."""
        self.items.append(Item(part_index, copy, rot % 360, tx, ty, self._poly(part_index, rot, tx, ty)))
        self._sessions.clear()
        return len(self.items) - 1

    def is_valid(self, i: int, rot: float, tx: float, ty: float) -> bool:
        return self.clearance(self._session(i), self._poly(self.items[i].part_index, rot, tx, ty))[0] >= -EPS

    def min_gap(self) -> float:
        """Smallest distance between any two parts (diagnostics and tests)."""
        polys = [it.poly for it in self.items]
        if len(polys) < 2:
            return float("inf")
        tree = STRtree(polys)
        return min(float(tree.query_nearest(p, return_distance=True, exclusive=True, all_matches=False)[1][0])
                   for p in polys)

    # ------------------------------------------------------------------ commit ---
    def _apply(self, i: int, rot: float, tx: float, ty: float) -> None:
        it = self.items[i]
        it.rot, it.tx, it.ty = rot % 360, tx, ty
        it.poly = self._poly(it.part_index, it.rot, tx, ty)
        self._sessions.clear()

    def pose(self, i: int) -> tuple[float, float, float]:
        it = self.items[i]
        return it.rot, it.tx, it.ty

    def set_pose(self, i: int, rot: float, tx: float, ty: float) -> bool:
        """Numeric entry / undo: accepted only when the pose is valid."""
        if not self.is_valid(i, rot, tx, ty):
            return False
        self._apply(i, rot, tx, ty)
        return True

    def placements(self) -> list[Placement]:
        out = []
        for it in self.items:
            part = self.material.parts[it.part_index]
            rad = math.radians(it.rot)
            out.append(Placement(it.part_index, it.copy, round(it.rot % 360, 4), it.tx, it.ty,
                                 transform_ring(part.outer, rad, it.tx, it.ty),
                                 [transform_ring(h, rad, it.tx, it.ty) for h in part.holes]))
        return out

    # -------------------------------------------------------------- translation ---
    def _sweep(self, sess: _Session, pi: int, rot: float, x: float, y: float, ux: float, uy: float,
               length: float) -> tuple[float, bool]:
        """Trace from (x, y) along (ux, uy) up to `length`: (travelled, hit)."""
        t = 0.0
        for _ in range(MAX_ITER):
            if t >= length - 1e-9:
                return t, False
            c = self.clearance(sess, self._poly(pi, rot, x + ux * t, y + uy * t))[0]
            s = min(max(c, self.h_min), length - t)             # leap by the proven-safe clearance
            while s >= EPS and s > c and self.clearance(
                    sess, self._poly(pi, rot, x + ux * (t + s), y + uy * (t + s)))[0] < -EPS:
                s *= 0.5                                         # the leap overshot: halve it
            if s < EPS:
                return t, True
            t += s
        return t, True

    def move(self, i: int, dx: float, dy: float, slide: bool = True) -> MoveResult:
        """Move part i by (dx, dy). It stops at contact (gap = spacing) and, with `slide`, glides along
        what it touches for the rest of the motion."""
        it = self.items[i]
        sess = self._session(i)
        x0, y0 = it.tx, it.ty
        if self.clearance(sess, it.poly)[0] < -EPS:
            return MoveResult(0.0, 0.0, True)                    # starts invalid: do not make it worse
        x, y, rx, ry, blocked = x0, y0, dx, dy, False
        for _ in range(4 if slide else 1):
            length = math.hypot(rx, ry)
            if length < EPS:
                break
            ux, uy = rx / length, ry / length
            travelled, hit = self._sweep(sess, it.part_index, it.rot, x, y, ux, uy, length)
            x, y = x + ux * travelled, y + uy * travelled
            rx, ry = rx - ux * travelled, ry - uy * travelled
            if not hit:
                break
            blocked = True
            n = self._normal(sess, self._poly(it.part_index, it.rot, x, y)) if slide else None
            if n is None:
                break
            into = rx * n[0] + ry * n[1]
            if into > 0:                                         # drop the component that pushes into the obstacle
                rx, ry = rx - into * n[0], ry - into * n[1]
        if (x, y) != (x0, y0):
            self._apply(i, it.rot, x, y)
        return MoveResult(x - x0, y - y0, blocked)

    def move_to(self, i: int, tx: float, ty: float, teleport: bool = False) -> MoveResult:
        """Drag to an absolute position. With `teleport` ("lift and place") a valid target is taken
        directly, even across other parts - this is how a small part gets into the hole of a large one."""
        it = self.items[i]
        if teleport and self.is_valid(i, it.rot, tx, ty):
            dx, dy = tx - it.tx, ty - it.ty
            self._apply(i, it.rot, tx, ty)
            return MoveResult(dx, dy, False, True)
        return self.move(i, tx - it.tx, ty - it.ty)

    def pull(self, i: int, ux: float, uy: float) -> MoveResult:
        """Push part i in a direction until it touches its neighbour / the margin (gap = spacing)."""
        n = math.hypot(ux, uy)
        far = math.hypot(self.W, self.H)
        return self.move(i, ux / n * far, uy / n * far, slide=False)

    def snap(self, i: int, threshold: float) -> MoveResult:
        """If a part or the sheet edge is closer than `threshold`, close the gap to exactly the spacing."""
        it = self.items[i]
        sess = self._session(i)
        x0, y0 = it.tx, it.ty
        x, y = x0, y0
        for _ in range(6):
            poly = self._poly(it.part_index, it.rot, x, y)
            c = self.clearance(sess, poly)[0]
            if c <= EPS or c > threshold:
                break
            n = self._normal(sess, poly)
            if n is None:
                break
            x, y = x + n[0] * c, y + n[1] * c               # moving by the clearance cannot create an overlap
        if (x, y) != (x0, y0):
            self._apply(i, it.rot, x, y)
        return MoveResult(x - x0, y - y0, False)

    def _nudged(self, i: int, rot: float, tx: float, ty: float, nudge: float) -> tuple[float, float] | None:
        """Smallest shift (<= nudge mm, any direction) that makes the pose valid; (0, 0) if it already is."""
        if self.is_valid(i, rot, tx, ty):
            return (0.0, 0.0)
        for r in (0.25, 0.5, 1, 2, 4, 8, 16, 32):
            if r > nudge:
                break
            for k in range(16):
                ang = 2 * math.pi * k / 16
                ox, oy = r * math.cos(ang), r * math.sin(ang)
                if self.is_valid(i, rot, tx + ox, ty + oy):
                    return (ox, oy)
        return None

    # ----------------------------------------------------------------- rotation ---
    def _rotated(self, it: Item, delta_rad: float, px: float, py: float) -> tuple[float, float, float]:
        c, s = math.cos(delta_rad), math.sin(delta_rad)
        rx, ry = it.tx - px, it.ty - py
        return it.rot + math.degrees(delta_rad), c * rx - s * ry + px, s * rx + c * ry + py

    def rotate(self, i: int, delta_deg: float, pivot: tuple[float, float] | None = None,
               nudge: float = 0.0) -> RotateResult:
        """Rotate part i by delta_deg about `pivot` (default: centroid of its outer contour). It turns until
        it touches something. With `nudge` > 0 a blocked rotation retries the full angle with the part
        shifted by up to `nudge` mm in any direction."""
        it = self.items[i]
        sess = self._session(i)
        px, py = pivot or self.pivot(i)
        if self.clearance(sess, it.poly)[0] < -EPS:
            return RotateResult(0.0, True)
        radius = max(math.hypot(x - px, y - py) for x, y in it.poly.exterior.coords)
        target = math.radians(delta_deg)
        sign, length = (1.0 if target >= 0 else -1.0), abs(target)
        pi = it.part_index
        a, h_min = 0.0, self.h_min / max(radius, 1e-9)
        for _ in range(MAX_ITER):
            if a >= length - 1e-12:
                break
            rot, tx, ty = self._rotated(it, sign * a, px, py)
            c = self.clearance(sess, self._poly(pi, rot, tx, ty))[0]
            s = min(max(c / max(radius, 1e-9), h_min), length - a)
            while s * radius >= EPS and s * radius > c:
                r2, x2, y2 = self._rotated(it, sign * (a + s), px, py)
                if self.clearance(sess, self._poly(pi, r2, x2, y2))[0] >= -EPS:
                    break
                s *= 0.5
            if s * radius < EPS:
                break
            a += s
        blocked = a < length - 1e-9
        if blocked and nudge > 0:
            rot, tx, ty = self._rotated(it, target, px, py)
            off = self._nudged(i, rot, tx, ty, nudge)
            if off is not None:
                self._apply(i, rot, tx + off[0], ty + off[1])
                return RotateResult(delta_deg, False, off)
        if a > 0:
            rot, tx, ty = self._rotated(it, sign * a, px, py)
            self._apply(i, rot, tx, ty)
        return RotateResult(sign * math.degrees(a), blocked)

    def set_rotation(self, i: int, angle: float, nudge: float = 0.0) -> bool:
        """Absolute angle about the centroid, all or nothing (numeric entry)."""
        it = self.items[i]
        delta = ((angle - it.rot + 180.0) % 360.0) - 180.0
        px, py = self.pivot(i)
        rot, tx, ty = self._rotated(it, math.radians(delta), px, py)
        off = self._nudged(i, rot, tx, ty, nudge)
        if off is None:
            return False
        self._apply(i, rot, tx + off[0], ty + off[1])
        return True

    # ------------------------------------------------------ numeric entry, add, remove ---
    def bbox_min(self, i: int) -> tuple[float, float]:
        b = self.items[i].poly.bounds
        return b[0], b[1]

    def set_bbox_min(self, i: int, x: float, y: float) -> bool:
        """Place the lower-left corner of the part's bounding box at (x, y); only if the pose is valid."""
        it = self.items[i]
        x0, y0 = self.bbox_min(i)
        return self.set_pose(i, it.rot, it.tx + x - x0, it.ty + y - y0)

    def index_of(self, key: tuple[int, int]) -> int:
        return next((n for n, it in enumerate(self.items) if (it.part_index, it.copy) == key), -1)

    def remove(self, i: int) -> Item:
        self._sessions.clear()
        return self.items.pop(i)

    def add(self, part_index: int, copy: int, orientations: list[float] | tuple[float, ...] = (0.0,),
            step: float | None = None) -> int | None:
        """Put a new copy on the first free spot (bottom-left first, then pulled tight). None = no room."""
        step = step or max(10.0, 3 * self.d)
        m = self.margin
        polys = [it.poly for it in self.items]
        tree = STRtree(polys) if polys else None
        best = None
        for rot in orientations:
            bx0, by0, bx1, by1 = self._poly(part_index, rot, 0.0, 0.0).bounds
            w, h = bx1 - bx0, by1 - by0
            if w > self.W - 2 * m + EPS or h > self.H - 2 * m + EPS:
                continue
            y = m
            while y <= self.H - m - h + 1e-9 and (best is None or (y, 0.0) < best[0]):
                x = m
                while x <= self.W - m - w + 1e-9:
                    near = tree.query(box(x - self.d, y - self.d, x + w + self.d, y + h + self.d)) if tree else []
                    cand = None
                    if len(near) == 0:
                        ok = True
                    else:
                        cand = self._poly(part_index, rot, x - bx0, y - by0)
                        ok = all(cand.distance(polys[j]) >= self.d - EPS for j in near)
                    if ok:
                        if best is None or (y, x) < best[0]:
                            best = ((y, x), rot, x - bx0, y - by0)
                        break
                    x += step
                y += step
        if best is None:
            return None
        _, rot, tx, ty = best
        self.items.append(Item(part_index, copy, rot, tx, ty, self._poly(part_index, rot, tx, ty)))
        self._sessions.clear()
        i = len(self.items) - 1
        self.pull(i, -1, 0)
        self.pull(i, 0, -1)
        return i


# ------------------------------------------------------------ result-level helpers ---
Raw = tuple[int, int, float, float, float]          # part index, copy, rotation, tx, ty


def raw_state(result: MaterialResult) -> list[list[Raw]]:
    return [[(p.part_index, p.copy, p.rotation_deg, p.tx, p.ty) for p in s.placements] for s in result.sheets]


def recompute(material: Material, result: MaterialResult) -> None:
    area = material.sheet[0] * material.sheet[1]
    for n, sh in enumerate(result.sheets, start=1):
        sh.index = n
        sh.used_area = sum(material.parts[p.part_index].area for p in sh.placements)
        sh.yield_pct = 100.0 * sh.used_area / area
    used = [sh for sh in result.sheets if sh.placements]
    result.yield_pct = 100.0 * sum(sh.used_area for sh in used) / (area * len(used)) if used else 0.0
    result.parts = summarize(material, result.sheets)
    result.requested = sum(p.requested for p in result.parts)
    result.nested = sum(p.nested for p in result.parts)
    result.warnings = validate_layout(material, result.sheets)


def mark_edited(result: MaterialResult) -> None:
    """Call BEFORE the first change: the layout is kept so that Revert can restore it."""
    if not result.edited:
        result.original = raw_state(result)
        result.edited = True
    result.edit_rev += 1


def commit_sheet(material: Material, result: MaterialResult, k: int, editor: SheetEditor) -> None:
    mark_edited(result)
    result.sheets[k].placements = editor.placements()
    recompute(material, result)


def commit_sheets(material: Material, result: MaterialResult, editors: dict[int, SheetEditor]) -> None:
    """Write the state of several sheet editors (e.g. both sheets of a part moved between them) in one step."""
    mark_edited(result)
    for k, ed in editors.items():
        result.sheets[k].placements = ed.placements()
    recompute(material, result)


def set_layout(material: Material, result: MaterialResult, raw: list[list[Raw]]) -> None:
    sheets = []
    for n, items in enumerate(raw, start=1):
        pls = []
        for pi, copy, rot, tx, ty in items:
            part, rad = material.parts[pi], math.radians(rot)
            pls.append(Placement(pi, copy, round(rot % 360, 4), tx, ty, transform_ring(part.outer, rad, tx, ty),
                                 [transform_ring(h, rad, tx, ty) for h in part.holes]))
        sheets.append(SheetResult(n, pls, 0.0, 0.0))
    result.sheets = sheets
    recompute(material, result)


def add_sheet(material: Material, result: MaterialResult) -> None:
    mark_edited(result)
    result.sheets.append(SheetResult(len(result.sheets) + 1, [], 0.0, 0.0))
    recompute(material, result)


def revert(material: Material, result: MaterialResult) -> bool:
    if result.original is None:
        return False
    set_layout(material, result, result.original)
    result.edited, result.original = False, None
    result.edit_rev += 1
    return True


def unplaced(material: Material, result: MaterialResult) -> dict[int, list[int]]:
    """part index -> copy numbers that are not on any sheet (nestable parts only)."""
    placed: dict[int, set[int]] = {}
    for sh in result.sheets:
        for pl in sh.placements:
            placed.setdefault(pl.part_index, set()).add(pl.copy)
    out = {}
    for pi, part in enumerate(material.parts):
        if part.nestable:
            missing = [c for c in range(1, part.qty + 1) if c not in placed.get(pi, set())]
            if missing:
                out[pi] = missing
    return out
