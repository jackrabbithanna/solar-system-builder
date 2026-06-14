# presets.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bundled solar-system presets."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SolarSystem

BUILTIN_PRESET_FILES = (
    "solar_system.json",
    "dwarf_planets.json",
)


def _load_preset(filename: str) -> SolarSystem:
    path = Path(__file__).resolve().parent / "presets" / filename
    with path.open("r", encoding="utf-8") as preset_file:
        return SolarSystem.from_dict(json.load(preset_file))


def load_builtin_solar_system() -> SolarSystem:
    return _load_preset("solar_system.json")


def load_builtin_solar_systems() -> list[SolarSystem]:
    return [_load_preset(filename) for filename in BUILTIN_PRESET_FILES]
