"""CSV / TXT reports."""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import MaterialResult, fmt_num

CSV_HEADER = ["material", "thickness_mm", "sheet_size", "sheets", "yield_percent",
              "part_name", "qty_nested", "qty_requested", "sheet_no"]


def _sheet_size(r: MaterialResult) -> str:
    return f"{fmt_num(r.sheet[0])}x{fmt_num(r.sheet[1])}"


def write_csv(results: list[MaterialResult], path: str, include_parts: bool = True) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for r in results:
            head = [r.label, fmt_num(r.thickness), _sheet_size(r), len(r.sheets), f"{r.yield_pct:.1f}"]
            if not include_parts:
                w.writerow(head + ["", r.nested, r.requested, ""])
                continue
            for ps in r.parts:
                if not ps.sheet_counts:
                    w.writerow(head + [ps.name, 0, ps.requested, ""])
                for sheet_no, count in sorted(ps.sheet_counts.items()):
                    w.writerow(head + [ps.name, count, ps.requested, sheet_no])


def write_txt(results: list[MaterialResult], path: str, include_parts: bool = True) -> None:
    lines = ["SimpleNesting nesting report", "=" * 24, ""]
    for r in results:
        lines += [f"Material : {r.label}",
                  f"Sheet    : {_sheet_size(r)} mm   Sheets: {len(r.sheets)}",
                  f"Yield    : {r.yield_pct:.1f}%",
                  f"Parts    : {r.nested} / {r.requested} nested"]
        if r.cancelled:
            lines.append("Note     : nesting was cancelled, result is partial")
        if include_parts:
            lines.append("")
            for ps in r.parts:
                where = ", ".join(f"sheet {s} x{c}" for s, c in sorted(ps.sheet_counts.items())) or "not placed"
                lines.append(f"  {ps.nested}/{ps.requested}  {ps.name}  ({where})")
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def write_report(results: list[MaterialResult], path: str, fmt: str, include_parts: bool = True) -> None:
    (write_csv if fmt.lower() == "csv" else write_txt)(results, path, include_parts)
