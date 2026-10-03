"""Small persistent state (JSON files) kept per Moodle site: snapshots for get_updates, web cookies."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class State:
    def __init__(self, base: Path, site_url: str) -> None:
        slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^https?://", "", site_url.lower())).strip("-") or "moodle"
        self.dir = base / slug

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def load(self, name: str, default: Any = None) -> Any:
        p = self.path(name)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def save(self, name: str, value: Any, *, private: bool = False) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        if private:
            os.chmod(tmp, 0o600)
        tmp.replace(p)
