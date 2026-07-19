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
    "binary_system.json",
)


def _load_preset(filename: str) -> SolarSystem:
    path = Path(__file__).resolve().parent / "presets" / filename
    with path.open("r", encoding="utf-8") as preset_file:
        return SolarSystem.from_dict(json.load(preset_file))


def load_builtin_solar_system() -> SolarSystem:
    return _load_preset("solar_system.json")


def load_builtin_solar_systems() -> list[SolarSystem]:
    return [_load_preset(filename) for filename in BUILTIN_PRESET_FILES]


def load_builtin_solar_system_by_id(system_id: str) -> SolarSystem:
    system = next(
        (item for item in load_builtin_solar_systems() if item.id == system_id),
        None,
    )
    if system is None:
        raise ValueError(f"unknown bundled preset id {system_id}")
    return system
