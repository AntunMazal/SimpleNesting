from __future__ import annotations

from dataclasses import dataclass, field

from .. import strings as S
from ..models import Material


@dataclass
class AppModel:
    materials: list[Material] = field(default_factory=list)
    selected: int = -1                  # selected material
    selected_part: int = -1             # selected file inside that material

    @property
    def material(self) -> Material | None:
        return self.materials[self.selected] if 0 <= self.selected < len(self.materials) else None

    def label_taken(self, label: str, exclude: int = -1) -> bool:
        """True if another material already uses this label (case-insensitive: the label is also
        the output folder name, and Windows folders are case-insensitive)."""
        key = label.strip().casefold()
        return any(i != exclude and m.label.casefold() == key for i, m in enumerate(self.materials))

    def new_material_name(self) -> str:
        """Name for a freshly added material: '' (shown as 'New Material'), then 'New Material 2', ..."""
        if not self.label_taken(S.NEW_MATERIAL):
            return ""
        n = 2
        while self.label_taken(f"{S.NEW_MATERIAL} {n}"):
            n += 1
        return f"{S.NEW_MATERIAL} {n}"
