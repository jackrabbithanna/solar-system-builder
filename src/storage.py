# storage.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Local JSON library for user-created solar systems."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from gi.repository import GLib

from .models import SolarSystem

APP_ID = "io.github.jackrabbithanna.solarsystembuilder"


class Library:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(GLib.get_user_data_dir()) / APP_ID / "systems"

    def list_systems(self) -> list[SolarSystem]:
        if not self.root.exists():
            return []
        systems: list[SolarSystem] = []
        for path in sorted(self.root.glob("*.json")):
            systems.append(self.load(path.stem))
        return systems

    def load(self, system_id: str) -> SolarSystem:
        with self._path(system_id).open("r", encoding="utf-8") as system_file:
            return SolarSystem.from_dict(json.load(system_file))

    def save(self, system: SolarSystem) -> None:
        system.validate()
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self._path(system.id)
        data = json.dumps(system.to_dict(), indent=2, sort_keys=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.root, delete=False) as temp_file:
            temp_file.write(data)
            temp_file.write("\n")
            temp_name = temp_file.name
        os.replace(temp_name, destination)

    def delete(self, system_id: str) -> None:
        self._path(system_id).unlink(missing_ok=True)

    def _path(self, system_id: str) -> Path:
        if "/" in system_id or "\\" in system_id:
            raise ValueError("invalid system id")
        return self.root / f"{system_id}.json"
