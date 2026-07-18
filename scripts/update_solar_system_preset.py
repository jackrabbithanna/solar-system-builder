#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Refresh bundled preset vectors from JPL Horizons."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.constants import AU, DAY  # noqa: E402
from src.horizons import (  # noqa: E402
    HORIZONS_URL,
    OrbitalElements,
    StateVector,
    parse_horizons_elements,
    parse_horizons_vector,
)
from src.models import SCHEMA_VERSION, SolarSystem  # noqa: E402

DEFAULT_EPOCH = "2026-06-14 00:00:00"

SOLAR_SYSTEM_TARGETS = {
    "sun": "10",
    "mercury": "199",
    "venus": "299",
    "earth": "399",
    "mars": "499",
    "jupiter": "599",
    "saturn": "699",
    "uranus": "799",
    "neptune": "899",
    "pluto": "999",
    "ceres": "1;",
    "moon": "301",
    "halley": "90000030",
    "bennu": "101955;",
}

SOLAR_SYSTEM_ORBIT_CENTERS = {
    "moon": "500@399",
}

DWARF_PLANET_TARGETS = {
    "sun": "10",
    "neptune": "899",
    "pluto": "999",
    "eris": "136199;",
    "haumea": "136108;",
    "makemake": "136472;",
    "gonggong": "225088;",
    "quaoar": "50000;",
    "orcus": "90482;",
}

PRESET_CONFIGS = {
    "solar-system": {
        "path": REPO_ROOT / "src" / "presets" / "solar_system.json",
        "targets": SOLAR_SYSTEM_TARGETS,
        "orbit_centers": SOLAR_SYSTEM_ORBIT_CENTERS,
        "description": (
            "Built-in Solar System preset using SI units and JPL Horizons "
            "solar-system barycentric state vectors, parent-centered osculating "
            "orbital metadata, and preserved local physical metadata."
        ),
    },
    "dwarf-planets": {
        "path": REPO_ROOT / "src" / "presets" / "dwarf_planets.json",
        "targets": DWARF_PLANET_TARGETS,
        "orbit_centers": {},
        "description": (
            "Built-in Dwarf Planets preset using SI units, JPL Horizons "
            "solar-system barycentric state vectors, heliocentric osculating "
            "orbital metadata, and curated physical metadata."
        ),
    },
}

# Backwards-compatible alias for tests and direct imports.
TARGETS = SOLAR_SYSTEM_TARGETS


def build_horizons_url(target_id: str, epoch: str) -> str:
    params = {
        "format": "json",
        "COMMAND": f"'{target_id}'",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@0'",
        "TLIST": f"'{epoch}'",
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": "TDB",
        "OUT_UNITS": "KM-S",
        "CSV_FORMAT": "YES",
    }
    return f"{HORIZONS_URL}?{urlencode(params)}"


def build_horizons_elements_url(
    target_id: str,
    epoch: str,
    center_id: str = "500@10",
) -> str:
    params = {
        "format": "json",
        "COMMAND": f"'{target_id}'",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "ELEMENTS",
        "CENTER": f"'{center_id}'",
        "TLIST": f"'{epoch}'",
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": "TDB",
        "OUT_UNITS": "AU-D",
        "CSV_FORMAT": "YES",
        "ELM_LABELS": "NO",
    }
    return f"{HORIZONS_URL}?{urlencode(params)}"


def fetch_horizons_vector(target_id: str, epoch: str) -> StateVector:
    url = build_horizons_url(target_id, epoch)
    with urlopen(url, timeout=30) as response:
        payload = json.load(response)

    if "result" not in payload:
        message = payload.get("message") or payload
        raise RuntimeError(f"Horizons returned no result for {target_id}: {message}")
    if payload.get("error"):
        raise RuntimeError(f"Horizons error for {target_id}: {payload['error']}")

    return parse_horizons_vector(str(payload["result"]))


def fetch_horizons_elements(
    target_id: str,
    epoch: str,
    center_id: str = "500@10",
) -> OrbitalElements:
    url = build_horizons_elements_url(target_id, epoch, center_id)
    with urlopen(url, timeout=30) as response:
        payload = json.load(response)

    if "result" not in payload:
        message = payload.get("message") or payload
        raise RuntimeError(f"Horizons returned no result for {target_id}: {message}")
    if payload.get("error"):
        raise RuntimeError(f"Horizons error for {target_id}: {payload['error']}")

    return parse_horizons_elements(str(payload["result"]))


def load_preset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as preset_file:
        return json.load(preset_file)


def apply_vectors(
    preset: dict[str, Any],
    vectors: dict[str, StateVector],
    epoch: str,
    targets: dict[str, str] | None = None,
    description: str | None = None,
    elements: dict[str, OrbitalElements] | None = None,
    retrieved_at: str | None = None,
    orbit_centers: dict[str, str] | None = None,
) -> dict[str, Any]:
    targets = targets or TARGETS
    orbit_centers = orbit_centers or {}
    body_ids = {body["id"] for body in preset.get("bodies", [])}
    missing_bodies = sorted(targets.keys() - body_ids)
    if missing_bodies:
        raise ValueError(f"Preset is missing expected bodies: {', '.join(missing_bodies)}")

    missing_vectors = sorted(targets.keys() - vectors.keys())
    if missing_vectors:
        raise ValueError(f"Missing fetched vectors: {', '.join(missing_vectors)}")

    if elements is not None:
        element_targets = set(targets) - {"sun"}
        missing_elements = sorted(element_targets - elements.keys())
        if missing_elements:
            raise ValueError(f"Missing fetched orbital elements: {', '.join(missing_elements)}")

    updated = json.loads(json.dumps(preset))
    updated["schema_version"] = SCHEMA_VERSION
    updated["epoch"] = f"{epoch} TDB, JPL Horizons, solar-system barycentric"
    updated["reference_frame"] = {
        "epoch": epoch,
        "time_scale": "TDB",
        "center_id": "500@0",
        "reference_plane": "ECLIPTIC",
        "reference_system": "ICRF",
        "source": "horizons",
    }
    if description is not None:
        updated["description"] = description

    retrieved_at = retrieved_at or date.today().isoformat()
    for body in updated["bodies"]:
        body_id = body["id"]
        if body_id not in targets:
            continue
        vector = vectors[body_id]
        body["position_m"] = vector.position_m
        body["velocity_mps"] = vector.velocity_mps
        body["state_origin"] = "horizons"
        if elements is not None and body_id != "sun":
            body["orbit"] = elements[body_id].to_orbit_dict(epoch)
            body["data_source"] = {
                "source_name": "JPL Horizons",
                "source_url": build_horizons_elements_url(
                    targets[body_id],
                    epoch,
                    orbit_centers.get(body_id, "500@10"),
                ),
                "catalog_id": targets[body_id],
                "retrieved_at": retrieved_at,
                "citation": "JPL Horizons osculating elements and state vectors.",
            }

    SolarSystem.from_dict(updated)
    return updated


def fetch_all_elements(
    epoch: str,
    targets: dict[str, str] | None = None,
    orbit_centers: dict[str, str] | None = None,
) -> dict[str, OrbitalElements]:
    targets = targets or TARGETS
    orbit_centers = orbit_centers or {}
    return {
        body_id: fetch_horizons_elements(
            target_id,
            epoch,
            orbit_centers.get(body_id, "500@10"),
        )
        for body_id, target_id in targets.items()
        if body_id != "sun"
    }


def fetch_all_vectors(epoch: str, targets: dict[str, str] | None = None) -> dict[str, StateVector]:
    targets = targets or TARGETS
    return {
        body_id: fetch_horizons_vector(target_id, epoch)
        for body_id, target_id in targets.items()
    }


def write_preset(path: Path, preset: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as preset_file:
        json.dump(preset, preset_file, indent=2)
        preset_file.write("\n")


def print_summary(vectors: dict[str, StateVector], targets: dict[str, str] | None = None) -> None:
    targets = targets or TARGETS
    for body_id in targets:
        vector = vectors[body_id]
        x, y, z = vector.position_m
        vx, vy, vz = vector.velocity_mps
        print(
            f"{body_id:8s} "
            f"pos=({x:.6e}, {y:.6e}, {z:.6e}) m "
            f"vel=({vx:.6e}, {vy:.6e}, {vz:.6e}) m/s"
        )


def print_elements_summary(
    elements: dict[str, OrbitalElements],
    targets: dict[str, str] | None = None,
) -> None:
    targets = targets or TARGETS
    for body_id in targets:
        if body_id == "sun":
            continue
        orbit = elements[body_id]
        print(
            f"{body_id:8s} "
            f"a={orbit.semi_major_axis_m / AU:.9f} AU "
            f"period={orbit.orbital_period_s / DAY:.6f} d "
            f"e={orbit.eccentricity:.9f} "
            f"i={orbit.inclination_deg:.6f} deg"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch JPL Horizons vectors and refresh bundled presets."
    )
    parser.add_argument(
        "--preset-set",
        choices=sorted(PRESET_CONFIGS),
        default="solar-system",
        help="Bundled preset target set to refresh",
    )
    parser.add_argument("--epoch", default=DEFAULT_EPOCH, help="TDB calendar epoch")
    parser.add_argument(
        "--preset",
        type=Path,
        default=None,
        help="Preset JSON path to read and optionally overwrite",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Overwrite the preset file. Without this, only validate and print a summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = PRESET_CONFIGS[args.preset_set]
    preset_path = args.preset or config["path"]
    targets = config["targets"]
    orbit_centers = config["orbit_centers"]
    preset = load_preset(preset_path)
    vectors = fetch_all_vectors(args.epoch, targets)
    elements = fetch_all_elements(args.epoch, targets, orbit_centers)
    updated = apply_vectors(
        preset,
        vectors,
        args.epoch,
        targets,
        str(config["description"]),
        elements,
        orbit_centers=orbit_centers,
    )

    print_summary(vectors, targets)
    print_elements_summary(elements, targets)
    print(f"Validated {len(updated['bodies'])} bodies for epoch {updated['epoch']}.")

    if args.write:
        write_preset(preset_path, updated)
        print(f"Wrote {preset_path}.")
    else:
        print("Dry run only. Pass --write to update the preset file.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
