"""Project files (JSON, extension .sng). Pure Python, no Qt.

A project stores the materials with their DXF files (absolute + relative path, quantity, size and
modification time), the project settings and the nesting results as placements only (part, copy,
rotation, translation). Geometry is never stored: the DXF files are read again when a project is
opened, so a changed drawing is noticed and the results are marked out of date."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import strings as S
from .geometry.dxf_loader import load_part
from .models import Material, MaterialResult, NestOptions, Part, fingerprint
from .nesting.common import RawPlacement
from .nesting.engine import build_result

FORMAT = "simple-nesting-project"
VERSION = 1
EXT = ".sng"
SETTING_KEYS = ("engine", "allow_rotation", "rotation_step", "time_limit", "units", "output_dir")


class ProjectError(Exception):
    pass


@dataclass
class LoadReport:
    missing: list[str] = field(default_factory=list)       # file names that were not found
    changed: list[str] = field(default_factory=list)       # file names that differ from the saved state
    skipped_results: int = 0


def settings_block(settings) -> dict:
    return {k: getattr(settings, k) for k in SETTING_KEYS}


def _rel(path: str, project_dir: Path | None) -> str | None:
    if project_dir is None:
        return None
    try:
        return os.path.relpath(path, project_dir).replace("\\", "/")
    except ValueError:                                       # other drive on Windows
        return None


def to_dict(materials: list[Material], settings: dict, results: list[MaterialResult],
            project_dir: Path | None = None, selected: int = 0, include_results: bool = True) -> dict:
    data = {
        "format": FORMAT, "version": VERSION, "app": S.APP_NAME,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "settings": dict(settings),
        "selected": selected,
        "materials": [{
            "uid": m.uid, "name": m.name, "thickness": m.thickness, "sheet": list(m.sheet),
            "distance": m.distance, "margin": m.margin,
            "parts": [{"path": p.path, "rel": _rel(p.path, project_dir), "qty": p.qty, "enabled": p.enabled,
                       "start_angle": p.start_angle, "rotation_step": p.rotation_step,
                       "size": p.file_size, "mtime": p.file_mtime} for p in m.parts],
        } for m in materials],
    }
    if include_results:
        data["results"] = [{
            "material_uid": r.material_uid, "fingerprint": r.fingerprint, "elapsed": r.elapsed_s,
            "cancelled": r.cancelled, "dxf_files": r.dxf_files, "warnings": r.warnings, "edited": r.edited,
            "sheets": [[[pl.part_index, pl.copy, pl.rotation_deg, pl.tx, pl.ty] for pl in s.placements]
                       for s in r.sheets],
        } for r in results]
    return data


def snapshot(materials: list[Material], settings: dict, results: list[MaterialResult]) -> str:
    """Compact, location-independent signature of the project state (for the 'unsaved changes' check)."""
    d = to_dict(materials, settings, [], None, 0, include_results=False)
    d.pop("saved_at")
    d["results"] = [[r.material_uid, r.fingerprint, r.nested, len(r.sheets), r.edited, r.edit_rev] for r in results]
    return json.dumps(d, sort_keys=True)


def write(path: str | Path, data: dict) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)                                   # atomic: a crash never leaves half a project


def read(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectError(f"Cannot read the project file: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ProjectError("This is not a SimpleNesting project file.")
    if int(data.get("version", 0)) > VERSION:
        raise ProjectError("This project was saved by a newer version of SimpleNesting.")
    return data


def resolve_path(entry: dict, project_dir: Path | None) -> str | None:
    candidates = [entry.get("path", "")]
    if entry.get("rel") and project_dir is not None:
        candidates.append(str((project_dir / entry["rel"]).resolve()))
    return next((c for c in candidates if c and Path(c).is_file()), None)


def materials_from_dict(data: dict, project_dir: Path | None, units: str,
                        progress: Callable[[int, int], None] | None = None) -> tuple[list[Material], LoadReport]:
    report = LoadReport()
    total = sum(len(m.get("parts", [])) for m in data.get("materials", []))
    done = 0
    materials = []
    for md in data.get("materials", []):
        m = Material(name=md.get("name", ""), thickness=float(md.get("thickness", 0)),
                     sheet=tuple(md.get("sheet", (2500, 1250))), distance=float(md.get("distance", 3)),
                     margin=float(md.get("margin", 5)), uid=md.get("uid") or Material().uid)
        for pd in md.get("parts", []):
            found = resolve_path(pd, project_dir)
            if found:
                part = load_part(found, units)
                if part.file_size != pd.get("size", part.file_size) or abs(part.file_mtime - pd.get("mtime", part.file_mtime)) > 1e-3:
                    report.changed.append(Path(found).name)
            else:
                part = Part(path=pd.get("path", ""), load_status=S.STATUS_MISSING,
                            note=f"File not found: {pd.get('path', '')}",
                            file_size=int(pd.get("size", 0)), file_mtime=float(pd.get("mtime", 0.0)))
                report.missing.append(part.name)
            part.qty, part.enabled = int(pd.get("qty", 1)), bool(pd.get("enabled", True))
            part.start_angle = float(pd.get("start_angle", 0.0))
            step = pd.get("rotation_step")
            part.rotation_step = None if step is None else float(step)
            m.parts.append(part)
            done += 1
            if progress:
                progress(done, total)
        materials.append(m)
    return materials, report


def results_from_dict(data: dict, materials: list[Material], options: NestOptions,
                      report: LoadReport | None = None) -> list[MaterialResult]:
    by_uid = {m.uid: m for m in materials}
    out = []
    for rd in data.get("results", []):
        m = by_uid.get(rd.get("material_uid"))
        sheets = [[RawPlacement(int(a), int(b), float(c), float(d), float(e)) for a, b, c, d, e in sheet]
                  for sheet in rd.get("sheets", [])]
        usable = m is not None and all(
            0 <= r.part_index < len(m.parts) and m.parts[r.part_index].geometry_ok for sheet in sheets for r in sheet)
        if not usable:                                       # material removed, or a part is missing / unreadable
            if report:
                report.skipped_results += 1
            continue
        res = build_result(m, sheets, float(rd.get("elapsed", 0.0)), bool(rd.get("cancelled", False)), validate=False)
        res.material_uid, res.fingerprint = m.uid, rd.get("fingerprint", "")
        res.dxf_files, res.warnings = list(rd.get("dxf_files", [])), list(rd.get("warnings", []))
        res.stale = fingerprint(m, options) != res.fingerprint
        res.edited = bool(rd.get("edited", False))
        out.append(res)
    return out
