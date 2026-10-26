"""User settings (JSON) and logging, both stored in ~/.simple-nesting/."""
from __future__ import annotations

import json
import logging
import logging.handlers
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import MAX_TIME_LIMIT_S, NestOptions

APP_DIR = Path.home() / ".simple-nesting"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "error.log"
AUTOSAVE_FILE = APP_DIR / "autosave.json"

ACCENTS = ["#d9480f", "#1a73d9", "#1f9d55", "#8e44ad", "#34495e"]


@dataclass
class Settings:
    last_dir: str = ""
    output_dir: str = ""
    units: str = "mm"
    appearance: str = "Dark"            # System | Light | Dark
    accent: str = ACCENTS[0]
    report_format: str = "CSV"          # CSV | TXT
    include_parts_list: bool = True
    auto_save_report: bool = False
    engine: str = "spyrrow"             # box | libnest2d | spyrrow
    allow_rotation: bool = True
    rotation_step: float = 90.0
    time_limit: int = 20                # seconds per sheet, spyrrow only
    recent_projects: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            s = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            s.time_limit = max(1, min(int(s.time_limit), MAX_TIME_LIMIT_S))
            return s
        except Exception:
            return cls()

    def add_recent(self, path: str, limit: int = 8) -> None:
        path = str(Path(path))
        self.recent_projects = [path] + [p for p in self.recent_projects if p != path][:limit - 1]

    def to_options(self) -> NestOptions:
        return NestOptions(engine=self.engine, allow_rotation=self.allow_rotation,
                           rotation_step=self.rotation_step,
                           time_limit_s=max(1, min(int(self.time_limit), MAX_TIME_LIMIT_S)),
                           output_dir=self.output_dir.strip(), units=self.units)

    def save(self) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            logging.getLogger(__name__).exception("Cannot save settings")


def setup_logging() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.INFO)
