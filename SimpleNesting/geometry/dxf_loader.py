"""DXF -> closed polygons (outline + holes): ezdxf reads, shapely builds the contours,
pyclipper cleans the vertices."""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field

import ezdxf
import pyclipper
import shapely
import shapely.prepared
from ezdxf import path as ezpath
from ezdxf import recover
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize_full, unary_union

from .. import strings as S
from ..models import Part, Ring

log = logging.getLogger(__name__)

SUPPORTED_TYPES = ("LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE")
UNIT_SCALES = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4}

FLATTEN_DISTANCE = 0.05     # mm, max chord error when arcs/splines become polygons
GRID = 0.01                 # mm, endpoints closer than this are treated as the same point
ENTITY_MATCH = 0.05         # mm, entity -> contour matching tolerance (export)
CLIPPER_SCALE = 1000        # mm -> integer units (1 um); Clipper only works on integers
CLEAN_DISTANCE = 0.01       # mm, vertices closer than this are merged


def read_dxf(path: str):
    try:
        return ezdxf.readfile(path)
    except (ezdxf.DXFError, UnicodeDecodeError):
        doc, _auditor = recover.readfile(path)
        return doc


def collect_entities(doc) -> list:
    """Modelspace entities that take part in a contour. The order is deterministic, so the
    indices stored in Part.source_entities stay valid when the file is read again for export."""
    out = []
    for e in doc.modelspace():
        if e.dxftype() not in SUPPORTED_TYPES:
            continue
        layer = doc.layers.get(e.dxf.layer) if doc.layers.has_entry(e.dxf.layer) else None
        if layer is not None and (layer.is_off() or layer.is_frozen()):
            continue
        out.append(e)
    return out


def signed_area(ring: Ring) -> float:
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _flatten(entity, scale: float) -> list[tuple[float, float]]:
    p = ezpath.make_path(entity)
    out: list[tuple[float, float]] = []
    for v in p.flattening(FLATTEN_DISTANCE / scale):
        pt = (v.x * scale, v.y * scale)
        if not out or math.dist(out[-1], pt) > 1e-9:
            out.append(pt)
    t = entity.dxftype()
    closed = t == "CIRCLE" or (t in ("LWPOLYLINE", "POLYLINE") and entity.is_closed)
    if closed and len(out) > 2 and math.dist(out[0], out[-1]) > 1e-9:
        out.append(out[0])
    return out


def _clean(ring: Ring) -> Ring:
    scaled = [(round(x * CLIPPER_SCALE), round(y * CLIPPER_SCALE)) for x, y in ring]
    cleaned = pyclipper.CleanPolygon(scaled, CLEAN_DISTANCE * CLIPPER_SCALE)
    return [(x / CLIPPER_SCALE, y / CLIPPER_SCALE) for x, y in cleaned]


def _geoms(collection) -> list:
    return list(collection.geoms) if hasattr(collection, "geoms") else ([] if collection.is_empty else [collection])


@dataclass
class ContourInfo:
    status: str
    main: Polygon | None = None
    outer_count: int = 0
    hole_count: int = 0
    note: str = ""
    problem_lines: list = field(default_factory=list)
    problem_points: list = field(default_factory=list)


def _unique_rings(faces) -> list[Polygon]:
    """Every closed ring of the drawing as a filled polygon, duplicates removed. A plate with a
    hole yields two faces that share the hole ring."""
    seen, rings = set(), []
    for f in faces:
        for ring in [f.exterior, *f.interiors]:
            poly = Polygon(ring)
            x0, y0, x1, y1 = poly.bounds
            key = (round(poly.area, 3), round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
            if key not in seen:
                seen.add(key)
                rings.append(poly)
    return rings


def _free_ends(noded) -> list[tuple[float, float]]:
    """Endpoints that belong to exactly one line: the places where a contour is not closed."""
    counts: dict[tuple[int, int], list] = {}
    for g in _geoms(noded):
        if g.geom_type != "LineString":
            continue
        for x, y in (g.coords[0], g.coords[-1]):
            counts.setdefault((round(x / GRID), round(y / GRID)), []).append((x, y))
    return [pts[0] for pts in counts.values() if len(pts) == 1]


def build_contour(lines: list[LineString]) -> ContourInfo:
    """Shapely does the topology: lines are snapped to a grid (joins endpoints that differ by
    float noise), noded at crossings and polygonized. Rings are then classified by nesting depth:
    even depth = outline (material inside), odd depth = hole."""
    snapped = [shapely.set_precision(ls, GRID) for ls in lines]
    snapped = [ls for ls in snapped if not ls.is_empty and ls.length > 0]
    if not snapped:
        return ContourInfo(S.STATUS_EMPTY, note="No supported entities (LINE, ARC, CIRCLE, ELLIPSE, POLYLINE, SPLINE).")
    noded = unary_union(snapped)
    faces, cuts, dangles, invalid = polygonize_full(noded)
    faces = [f for f in _geoms(faces) if f.area > 1e-6]
    open_edges = _geoms(dangles) + _geoms(cuts)

    rings = sorted(_unique_rings(faces), key=lambda r: r.area, reverse=True)
    prepared = [shapely.prepared.prep(r) for r in rings]
    outer = inner = 0
    for j, r in enumerate(rings):
        pt = r.representative_point()
        depth = sum(1 for i in range(j) if prepared[i].contains(pt))
        if depth % 2 == 0:
            outer += 1
        else:
            inner += 1

    info = ContourInfo(S.STATUS_OK, outer_count=outer, hole_count=inner,
                       problem_lines=[list(g.coords) for g in open_edges])
    if open_edges:
        ends = _free_ends(noded)
        info.status = S.STATUS_OPEN
        info.problem_points = ends
        info.note = (f"{len(ends)} open end(s): the contour is not closed." if ends
                     else f"{len(_geoms(cuts))} stray line(s) inside the contour.")
    elif _geoms(invalid):
        info.status = S.STATUS_INVALID
        info.note = "Self-intersecting contour."
    elif not rings:
        info.status = S.STATUS_EMPTY
        info.note = "No closed contour found."
    elif outer > 1:
        info.status = S.STATUS_MULTI
        info.note = f"{outer} separate outlines in one file (a part inside a hole counts as one). One part per file is expected."
    else:
        info.main = max(faces, key=lambda f: Polygon(f.exterior).area)
    return info


def load_part(path: str, unit: str = "mm") -> Part:
    """Read one DXF file. Never raises: problems are reported through Part.load_status."""
    part = Part(path=path, unit_scale=UNIT_SCALES.get(unit, 1.0))
    scale = part.unit_scale
    try:
        st = os.stat(path)
        part.file_size, part.file_mtime = st.st_size, st.st_mtime
    except OSError:
        pass
    try:
        entities = collect_entities(read_dxf(path))
        ent_lines: list[tuple[int, LineString]] = []
        for idx, e in enumerate(entities):
            try:
                pts = _flatten(e, scale)
            except Exception:   # a single broken entity must not kill the file
                log.exception("Skipping entity %s in %s", e.dxftype(), path)
                continue
            if len(pts) >= 2:
                ls = LineString(pts)
                if ls.length > 0:
                    ent_lines.append((idx, ls))
        info = build_contour([ls for _, ls in ent_lines])
    except Exception:
        log.exception("Cannot read %s", path)
        part.load_status = S.STATUS_ERROR
        return part

    part.outer_count, part.hole_count = info.outer_count, info.hole_count
    part.note, part.problem_lines, part.problem_points = info.note, info.problem_lines, info.problem_points
    main = info.main
    if main is None:
        part.load_status = info.status
        part.raw_lines = [list(ls.coords) for _, ls in ent_lines][:5000]
        return part

    outer = _clean(list(main.exterior.coords)[:-1])
    holes = [h for h in (_clean(list(r.coords)[:-1]) for r in main.interiors) if len(h) >= 3]
    if len(outer) < 3:
        part.load_status = S.STATUS_EMPTY
        return part

    # Which source entity belongs to the outline and which to a hole (needed to export true arcs).
    rings = [main.exterior] + list(main.interiors)
    for idx, ls in ent_lines:
        mid = ls.interpolate(0.5, normalized=True)
        d = [mid.distance(r) for r in rings]
        k = min(range(len(d)), key=d.__getitem__)
        if d[k] <= ENTITY_MATCH:
            part.source_entities.append((idx, k == 0))

    xs = [p[0] for p in outer]
    ys = [p[1] for p in outer]
    ox, oy = min(xs), min(ys)
    shift = lambda r: [(x - ox, y - oy) for x, y in r]
    part.outer = shift(outer)
    part.holes = [shift(h) for h in holes]
    part.hull = [(x, y) for x, y in Polygon(part.outer).convex_hull.exterior.coords][:-1]
    part.origin = (ox, oy)
    part.width = max(xs) - ox
    part.height = max(ys) - oy
    part.area = abs(signed_area(outer)) - sum(abs(signed_area(h)) for h in holes)
    return part
