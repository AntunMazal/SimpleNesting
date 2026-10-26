# Third-Party Licenses

SimpleNesting uses the following third-party libraries:

| Library | Version | Author / Project | Licence | Used for |
|---------|---------|------------------|---------|----------|
| PySide6 (Qt 6) | 6.12 | Qt for Python Team, The Qt Company Ltd. | LGPL-3.0-only OR GPL-2.0/3.0 | the whole GUI |
| ezdxf | 1.4.5 | Manfred Moitzi | MIT | reading and writing DXF |
| python-libnest2d | 0.1.3 | Ultimaker B.V. (original *pynest2d*) and contributors | LGPL-3.0-or-later | Box and libnest2d engines |
| spyrrow | 0.9 | Paul Durand-Lupinski (Reeverse Systems) | MIT | spyrrow (precise) engine |
| shapely / GEOS | 2.2 | Sean Gillies and the GEOS project | BSD-3-Clause | geometry, overlap and margin checks |
| pyclipper | 1.4 | Angus Johnson, Maxime Chalton, Lukas Treyer, Gregor Ratajc | MIT | polygon offsetting (clipping) |
| numpy | 2.5 | Travis E. Oliphant et al. | BSD-3-Clause | arrays (through ezdxf) |
| fontTools | 4.67 | Just van Rossum et al. | MIT | font handling (through ezdxf) |
| pyparsing | 3.3 | Paul McGuire | MIT | parsing (through ezdxf) |

Because PySide6 and python-libnest2d are LGPL, SimpleNesting is distributed
as a **folder** (`--onedir`) so these libraries remain replaceable.