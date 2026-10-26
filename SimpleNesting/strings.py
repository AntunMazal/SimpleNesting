"""All user-visible text lives here so a translation is just another module."""

APP_NAME = "SimpleNesting"

TAB_SETUP = "Setup"
TAB_REPORT = "Report"
TAB_EDIT = "Edit"

# Part statuses
STATUS_OK = "OK"
STATUS_OPEN = "Open contour"
STATUS_TOO_LARGE = "Larger than sheet"
STATUS_DUPLICATE = "Duplicate (skipped)"
STATUS_EMPTY = "No geometry found"
STATUS_MULTI = "Multiple outlines"
STATUS_ERROR = "Unreadable file"
STATUS_INVALID = "Invalid contour"
STATUS_MISSING = "Missing file"

# Status bar
MSG_READY = "Ready."
MSG_NESTING = "Nesting..."
MSG_DONE = "Done."
MSG_CANCELLED = "Cancelled."
MSG_NOTHING_TO_NEST = "Nothing to nest: add DXF files to a material first."
MSG_FIRST_STEP = 'Start here: click "+ Add Material..." to create your first material.'

# Nesting modes
ENGINE_BOX = "Box (Fast)"
ENGINE_LIBNEST = "libnest2d (Hull)"
ENGINE_SPYRROW = "spyrrow (Precise)"

NEW_MATERIAL = "New Material"


# Help / About
MSG_HELP_TITLE = "SimpleNesting Help"
MSG_ABOUT_TITLE = "About SimpleNesting"

# Quick start guide
MSG_HELP_INTRO = """Welcome to SimpleNesting!

Quick start:

1. + Add Material... → name and set sheet format, margin & spacing.
2. + Add Files... → pick DXF parts, set quantity.
3. Choose engine (Box, libnest2d, spyrrow) and rotation step.
4. ▶ RUN NESTING → get sheets, yield % and parts per sheet.
5. Export DXF and CSV reports.

Use the Setup, Report and Edit tabs for full control.

See Help > Features for an overview of available algorithms.
"""

MSG_HELP_FEATURES = """Available nesting algorithms:

• Box (Fast) – simple heuristic for convex parts.
• libnest2d (Hull) – convex‑hull placer, best for convex shapes.
• spyrrow (Precise) – strip‑packing, handles concave parts.

All engines can be compared side‑by‑side; every parameter is adjustable:
• Material thickness, sheet size, margins, spacing
• Quantities, start angle, rotation step
• Time limit, engine selection

The Edit tab lets you manually improve the layout: drag, rotate and snap parts
for the best fit before finalizing.
"""

MSG_ABOUT_CONTENT = """SimpleNesting
Version 1.0.0

Desktop nesting tool for laser / plasma cutting.

Made with Claude (Anthropic) as AI pair programmer.

Third‑party libraries used (see full list in the license text below).
"""

# Licenses
MSG_LICENSES_TITLE = "Third-Party Licenses"
MSG_LICENSES_CONTENT = """SimpleNesting uses the following third-party libraries:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PySide6 (Qt 6)          LGPL-3.0-only OR GPL-2.0/3.0
The Qt Company Ltd. / Qt for Python Team
https://pyside.org
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ezdxf                   MIT
Manfred Moitzi
https://github.com/mozman/ezdxf
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
python-libnest2d        LGPL-3.0-or-later
Ultimaker B.V. (original pynest2d) and contributors
https://github.com/HamzaYslmn/python-libnest2d
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
spyrrow                 MIT
Paul Durand-Lupinski (Reeverse Systems)
https://github.com/PaulDurandLupinski/spyrrow
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
shapely / GEOS          BSD-3-Clause
Sean Gillies and the GEOS project
https://shapely.readthedocs.io
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
pyclipper               MIT
Angus Johnson, Maxime Chalton, Lukas Treyer, Gregor Ratajc
https://github.com/fonttools/pyclipper
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
numpy                   BSD-3-Clause
Travis E. Oliphant et al.
https://numpy.org
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
fontTools               MIT
Just van Rossum et al.
https://github.com/fonttools/fonttools
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
pyparsing               MIT
Paul McGuire
https://github.com/pyparsing/pyparsing
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Because PySide6 and python-libnest2d are LGPL, SimpleNesting is distributed
as a folder (--onedir) so these libraries remain replaceable.
"""
