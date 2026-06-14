# presets.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bundled solar-system presets."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SolarSystem


def load_builtin_solar_system() -> SolarSystem:
    path = Path(__file__).resolve().parent / "presets" / "solar_system.json"
    with path.open("r", encoding="utf-8") as preset_file:
        return SolarSystem.from_dict(json.load(preset_file))
