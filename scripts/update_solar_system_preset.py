#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Refresh bundled Solar System vectors from JPL Horizons."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import SolarSystem  # noqa: E402

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
DEFAULT_EPOCH = "2026-06-14 00:00:00"
DEFAULT_PRESET = REPO_ROOT / "src" / "presets" / "solar_system.json"
KM_TO_M = 1000.0

TARGETS = {
    "sun": "10",
    "mercury": "199",
    "venus": "299",
    "earth": "399",
    "mars": "499",
    "jupiter": "599",
    "saturn": "699",
    "uranus": "799",
    "neptune": "899",
}


@dataclass(frozen=True)
class StateVector:
    position_m: list[float]
    velocity_mps: list[float]


def parse_horizons_vector(result_text: str) -> StateVector:
    """Parse the first CSV state-vector row between Horizons $$SOE/$$EOE."""
    in_table = False
    for line in result_text.splitlines():
        stripped = line.strip()
        if stripped == "$$SOE":
            in_table = True
            continue
        if stripped == "$$EOE":
            break
        if not in_table or not stripped:
            continue

        row = next(csv.reader([stripped], skipinitialspace=True))
        if len(row) < 8:
            raise ValueError(f"Horizons vector row has too few columns: {stripped}")
        values = [float(item) * KM_TO_M for item in row[2:8]]
        return StateVector(position_m=values[:3], velocity_mps=values[3:])

    raise ValueError("Horizons response did not contain a vector row")


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


def load_preset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as preset_file:
        return json.load(preset_file)


def apply_vectors(
    preset: dict[str, Any],
    vectors: dict[str, StateVector],
    epoch: str,
) -> dict[str, Any]:
    body_ids = {body["id"] for body in preset.get("bodies", [])}
    missing_bodies = sorted(TARGETS.keys() - body_ids)
    if missing_bodies:
        raise ValueError(f"Preset is missing expected bodies: {', '.join(missing_bodies)}")

    missing_vectors = sorted(TARGETS.keys() - vectors.keys())
    if missing_vectors:
        raise ValueError(f"Missing fetched vectors: {', '.join(missing_vectors)}")

    updated = json.loads(json.dumps(preset))
    updated["epoch"] = f"{epoch} TDB, JPL Horizons, solar-system barycentric"
    updated["description"] = (
        "Built-in Solar System preset using SI units and JPL Horizons "
        "solar-system barycentric state vectors; local physical metadata preserved."
    )

    for body in updated["bodies"]:
        body_id = body["id"]
        if body_id not in TARGETS:
            continue
        vector = vectors[body_id]
        body["position_m"] = vector.position_m
        body["velocity_mps"] = vector.velocity_mps

    SolarSystem.from_dict(updated)
    return updated


def fetch_all_vectors(epoch: str) -> dict[str, StateVector]:
    return {
        body_id: fetch_horizons_vector(target_id, epoch)
        for body_id, target_id in TARGETS.items()
    }


def write_preset(path: Path, preset: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as preset_file:
        json.dump(preset, preset_file, indent=2)
        preset_file.write("\n")


def print_summary(vectors: dict[str, StateVector]) -> None:
    for body_id in TARGETS:
        vector = vectors[body_id]
        x, y, z = vector.position_m
        vx, vy, vz = vector.velocity_mps
        print(
            f"{body_id:8s} "
            f"pos=({x:.6e}, {y:.6e}, {z:.6e}) m "
            f"vel=({vx:.6e}, {vy:.6e}, {vz:.6e}) m/s"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch JPL Horizons vectors and refresh the bundled Solar System preset."
    )
    parser.add_argument("--epoch", default=DEFAULT_EPOCH, help="TDB calendar epoch")
    parser.add_argument(
        "--preset",
        type=Path,
        default=DEFAULT_PRESET,
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
    preset = load_preset(args.preset)
    vectors = fetch_all_vectors(args.epoch)
    updated = apply_vectors(preset, vectors, args.epoch)

    print_summary(vectors)
    print(f"Validated {len(updated['bodies'])} bodies for epoch {updated['epoch']}.")

    if args.write:
        write_preset(args.preset, updated)
        print(f"Wrote {args.preset}.")
    else:
        print("Dry run only. Pass --write to update the preset file.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
