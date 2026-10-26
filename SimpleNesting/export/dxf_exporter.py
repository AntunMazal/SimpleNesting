"""Write nested sheets as DXF files for the laser / plasma machine.

The ORIGINAL entities (true arcs, splines, polylines) are copied and transformed, so the
output is not a polygon approximation. Outlines go to layer CUT_OUTER, holes to CUT_INNER.
The plate itself is drawn as four lines on layer SHEET_BOUNDARY (red), so the operator
sees where the parts sit on the sheet; that layer is not meant to be cut.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import ezdxf
from ezdxf import units
from ezdxf.math import Matrix44

from ..geometry.dxf_loader import collect_entities, read_dxf
from ..models import Material, MaterialResult

LAYER_OUTER = "CUT_OUTER"
LAYER_INNER = "CUT_INNER"
LAYER_SHEET = "SHEET_BOUNDARY"         # the plate itself, drawn in red (not to be cut)


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip(" .") or "material"


def export_material(material: Material, result: MaterialResult, out_dir: str) -> list[str]:
    folder = Path(out_dir) / safe_name(material.label)
    folder.mkdir(parents=True, exist_ok=True)
    cache: dict[str, list] = {}         # source path -> entity list (documents stay referenced)
    written = []
    for sheet in result.sheets:
        if not sheet.placements:
            continue
        doc = ezdxf.new("R2010")
        doc.units = units.MM
        doc.layers.add(LAYER_OUTER, color=7)
        doc.layers.add(LAYER_INNER, color=1)
        doc.layers.add(LAYER_SHEET, color=1)          # red: the plate outline
        msp = doc.modelspace()
        sw, sh = material.sheet                        # plate size in mm
        for a, b in (((0.0, 0.0), (sw, 0.0)), ((sw, 0.0), (sw, sh)),
                     ((sw, sh), (0.0, sh)), ((0.0, sh), (0.0, 0.0))):
            msp.add_line(a, b, dxfattribs={"layer": LAYER_SHEET})
        for pl in sheet.placements:
            part = material.parts[pl.part_index]
            if part.path not in cache:
                cache[part.path] = collect_entities(read_dxf(part.path))
            entities = cache[part.path]
            m = Matrix44.chain(
                Matrix44.scale(part.unit_scale, part.unit_scale, part.unit_scale),
                Matrix44.translate(-part.origin[0], -part.origin[1], 0),
                Matrix44.z_rotate(math.radians(pl.rotation_deg)),
                Matrix44.translate(pl.tx, pl.ty, 0))
            for idx, is_outer in part.source_entities:
                copy = entities[idx].copy()
                msp.add_entity(copy)
                copy.transform(m)
                copy.dxf.layer = LAYER_OUTER if is_outer else LAYER_INNER
        target = folder / f"sheet_{sheet.index:02d}.dxf"
        doc.saveas(target)
        written.append(str(target))
    return written
