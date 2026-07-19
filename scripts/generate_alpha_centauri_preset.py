#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the built-in Alpha Centauri preset."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import SCHEMA_VERSION, SolarSystem  # noqa: E402

G = 6.67430e-11
AU = 149_597_870_700.0
DAY = 86_400.0
YEAR = 365.25 * DAY
SOLAR_MASS = 1.98847e30
EARTH_MASS = 5.97237e24
SOLAR_RADIUS = 696_340_000.0
EARTH_RADIUS = 6_371_000.0


def circular_child_state(
    parent_position: list[float],
    parent_velocity: list[float],
    parent_mass_kg: float,
    semi_major_axis_au: float,
    angle_deg: float,
) -> tuple[list[float], list[float]]:
    radius_m = semi_major_axis_au * AU
    angle = math.radians(angle_deg)
    local_position = [radius_m * math.cos(angle), radius_m * math.sin(angle), 0.0]
    local_speed = math.sqrt(G * parent_mass_kg / radius_m)
    local_velocity = [-local_speed * math.sin(angle), local_speed * math.cos(angle), 0.0]
    return (
        [parent_position[index] + local_position[index] for index in range(3)],
        [parent_velocity[index] + local_velocity[index] for index in range(3)],
    )


def star_states() -> dict[str, tuple[list[float], list[float]]]:
    mass_a = 1.0788 * SOLAR_MASS
    mass_b = 0.9092 * SOLAR_MASS
    mass_proxima = 0.1221 * SOLAR_MASS

    ab_semimajor_m = 23.299 * AU
    ab_eccentricity = 0.52
    ab_separation_m = ab_semimajor_m * (1.0 + ab_eccentricity)
    ab_relative_speed = math.sqrt(
        G * (mass_a + mass_b) * (2.0 / ab_separation_m - 1.0 / ab_semimajor_m)
    )
    position_a_x = -ab_separation_m * mass_b / (mass_a + mass_b)
    position_b_x = ab_separation_m * mass_a / (mass_a + mass_b)
    velocity_a_y = -ab_relative_speed * mass_b / (mass_a + mass_b)
    velocity_b_y = ab_relative_speed * mass_a / (mass_a + mass_b)

    proxima_separation_m = 13_000.0 * AU
    proxima_semimajor_m = 8_700.0 * AU
    proxima_speed = math.sqrt(
        G * (mass_a + mass_b + mass_proxima) * (2.0 / proxima_separation_m - 1.0 / proxima_semimajor_m)
    )
    ab_counter_velocity_y = -mass_proxima * proxima_speed / (mass_a + mass_b)

    return {
        "alpha-centauri-a": (
            [position_a_x, 0.0, 0.0],
            [0.0, velocity_a_y + ab_counter_velocity_y, 0.0],
        ),
        "alpha-centauri-b": (
            [position_b_x, 0.0, 0.0],
            [0.0, velocity_b_y + ab_counter_velocity_y, 0.0],
        ),
        "proxima-centauri": (
            [0.0, proxima_separation_m, 0.0],
            [-proxima_speed, 0.0, 0.0],
        ),
    }


def body(
    body_id: str,
    name: str,
    kind: str,
    mass_kg: float,
    radius_m: float,
    position_m: list[float],
    velocity_mps: list[float],
    color: str,
    parent_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": body_id,
        "name": name,
        "kind": kind,
        "mass_kg": mass_kg,
        "radius_m": radius_m,
        "position_m": position_m,
        "velocity_mps": velocity_mps,
        "color": color,
    }
    if parent_id is not None:
        data["parent_id"] = parent_id
    return data


def build_preset() -> dict[str, Any]:
    states = star_states()
    mass_a = 1.0788 * SOLAR_MASS
    mass_b = 0.9092 * SOLAR_MASS
    mass_proxima = 0.1221 * SOLAR_MASS
    position_a, velocity_a = states["alpha-centauri-a"]
    position_b, velocity_b = states["alpha-centauri-b"]
    position_proxima, velocity_proxima = states["proxima-centauri"]

    proxima_d_position, proxima_d_velocity = circular_child_state(position_proxima, velocity_proxima, mass_proxima, 0.029, 0.0)
    proxima_b_position, proxima_b_velocity = circular_child_state(position_proxima, velocity_proxima, mass_proxima, 0.0485, 120.0)
    proxima_c_position, proxima_c_velocity = circular_child_state(position_proxima, velocity_proxima, mass_proxima, 1.49, 240.0)
    alpha_a_candidate_position, alpha_a_candidate_velocity = circular_child_state(position_a, velocity_a, mass_a, 2.0, 80.0)

    preset = {
        "schema_version": SCHEMA_VERSION,
        "id": "builtin-binary-system",
        "name": "Alpha Centauri",
        "epoch": "Approximate Alpha Centauri seed",
        "description": (
            "Built-in Alpha Centauri-inspired preset using SI units, approximate 2D state vectors, "
            "three known stars, confirmed Proxima b/d, and selected candidate planets. This is a "
            "readable simulation seed, not a current-date ephemeris."
        ),
        "settings": {
            "visible_step_s": 1_000.0 * YEAR,
            "accuracy_profile": "fast",
            "distance_unit": "AU",
            "view_mode": "fit_system",
            "simulation_scope": "auto",
            "trail_sample_interval_s": 1_000.0 * YEAR,
        },
        "groups": [
            {
                "id": "alpha-centauri-system",
                "name": "Alpha Centauri",
                "kind": "triple_system",
                "body_ids": [],
            },
            {
                "id": "alpha-centauri-ab-system",
                "name": "Alpha Centauri AB",
                "kind": "binary_system",
                "parent_group_id": "alpha-centauri-system",
                "body_ids": ["alpha-centauri-a", "alpha-centauri-b"],
            },
            {
                "id": "proxima-centauri-system",
                "name": "Proxima Centauri System",
                "kind": "planetary_system",
                "parent_group_id": "alpha-centauri-system",
                "body_ids": ["proxima-centauri"],
            },
        ],
        "bodies": [
            body("alpha-centauri-a", "Alpha Centauri A", "star", mass_a, 1.2234 * SOLAR_RADIUS, position_a, velocity_a, "#fff1a8"),
            body("alpha-centauri-b", "Alpha Centauri B", "star", mass_b, 0.8632 * SOLAR_RADIUS, position_b, velocity_b, "#ffd166"),
            body("proxima-centauri", "Proxima Centauri", "star", mass_proxima, 0.1542 * SOLAR_RADIUS, position_proxima, velocity_proxima, "#ff7b54"),
            body("proxima-centauri-d", "Proxima d", "planet", 0.26 * EARTH_MASS, 0.53 * EARTH_RADIUS, proxima_d_position, proxima_d_velocity, "#b8f2e6", "proxima-centauri"),
            body("proxima-centauri-b", "Proxima b", "planet", 1.27 * EARTH_MASS, 1.05 * EARTH_RADIUS, proxima_b_position, proxima_b_velocity, "#3a86ff", "proxima-centauri"),
            body("proxima-centauri-c-candidate", "Proxima c Candidate", "planet", 7.0 * EARTH_MASS, 2.0 * EARTH_RADIUS, proxima_c_position, proxima_c_velocity, "#7f8c8d", "proxima-centauri"),
            body("alpha-centauri-a-candidate", "Alpha Centauri A Candidate", "planet", 100.0 * EARTH_MASS, 24_622_000.0, alpha_a_candidate_position, alpha_a_candidate_velocity, "#9b59b6", "alpha-centauri-a"),
        ],
    }
    return SolarSystem.from_dict(preset).to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Overwrite src/presets/binary_system.json. Without this, print the preset JSON.",
    )
    args = parser.parse_args()
    preset = build_preset()
    if args.write:
        path = REPO_ROOT / "src" / "presets" / "binary_system.json"
        path.write_text(json.dumps(preset, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(preset, indent=2))


if __name__ == "__main__":
    main()
