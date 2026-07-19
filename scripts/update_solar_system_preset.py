#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Refresh bundled preset vectors from JPL Horizons."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    PhysicalProperties,
    StateVector,
    build_sbdb_url,
    parse_horizons_elements,
    parse_horizons_physical_properties,
    parse_horizons_vector,
    parse_horizons_vector_with_delta_t,
    parse_sbdb_physical_properties,
)
from src.models import SCHEMA_VERSION, SolarSystem  # noqa: E402

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

SOLAR_SYSTEM_SBDB_TARGETS = {
    "ceres": "1",
    "halley": "1P",
    "bennu": "101955",
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

DWARF_PLANET_SBDB_TARGETS = {
    "eris": "136199",
    "haumea": "136108",
    "makemake": "136472",
    "gonggong": "225088",
    "quaoar": "50000",
    "orcus": "90482",
}

PRESET_CONFIGS = {
    "solar-system": {
        "path": REPO_ROOT / "src" / "presets" / "solar_system.json",
        "targets": SOLAR_SYSTEM_TARGETS,
        "orbit_centers": SOLAR_SYSTEM_ORBIT_CENTERS,
        "sbdb_targets": SOLAR_SYSTEM_SBDB_TARGETS,
        "description": (
            "Built-in Solar System preset using SI units and JPL Horizons "
            "solar-system barycentric state vectors, parent-centered osculating "
            "orbital metadata, and available JPL physical data with curated "
            "fallbacks."
        ),
    },
    "dwarf-planets": {
        "path": REPO_ROOT / "src" / "presets" / "dwarf_planets.json",
        "targets": DWARF_PLANET_TARGETS,
        "orbit_centers": {},
        "sbdb_targets": DWARF_PLANET_SBDB_TARGETS,
        "description": (
            "Built-in Dwarf Planets preset using SI units, JPL Horizons "
            "solar-system barycentric state vectors, heliocentric osculating "
            "orbital metadata, and available JPL physical data with curated "
            "fallbacks."
        ),
    },
}

# Backwards-compatible alias for tests and direct imports.
TARGETS = SOLAR_SYSTEM_TARGETS


@dataclass(frozen=True)
class FetchedVector:
    vector: StateVector
    physical: PhysicalProperties
    source_url: str
    delta_t_s: float | None = None


@dataclass(frozen=True)
class FetchedPresetData:
    tdb_epoch: str
    retrieved_at: str
    utc_epoch: str | None
    vectors: dict[str, FetchedVector]
    elements: dict[tuple[str, str], OrbitalElements]
    physical: dict[str, PhysicalProperties]


def build_horizons_url(
    target_id: str,
    epoch: str,
    *,
    time_type: str = "TDB",
    include_delta_t: bool = False,
) -> str:
    params = {
        "format": "json",
        "COMMAND": f"'{target_id}'",
        "OBJ_DATA": "YES",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@0'",
        "TLIST": f"'{epoch}'",
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": time_type,
        "TIME_DIGITS": "FRACSEC",
        "REF_PLANE": "ECLIPTIC",
        "REF_SYSTEM": "ICRF",
        "OUT_UNITS": "KM-S",
        "VEC_TABLE": "2",
        "VEC_LABELS": "NO",
        "VEC_CORR": "NONE",
        "CSV_FORMAT": "YES",
    }
    if include_delta_t:
        params["VEC_DELTA_T"] = "YES"
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
        "TIME_DIGITS": "FRACSEC",
        "REF_PLANE": "ECLIPTIC",
        "REF_SYSTEM": "ICRF",
        "OUT_UNITS": "AU-D",
        "CSV_FORMAT": "YES",
        "ELM_LABELS": "NO",
    }
    return f"{HORIZONS_URL}?{urlencode(params)}"


def _load_jpl_payload(url: str, api_name: str) -> dict[str, Any]:
    with urlopen(url, timeout=30) as response:
        payload = json.load(response)

    if not isinstance(payload, dict):
        raise RuntimeError(f"{api_name} returned an invalid response")
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise RuntimeError(f"{api_name} response is missing an API signature")
    source = str(signature.get("source", ""))
    version = str(signature.get("version", ""))
    if "NASA/JPL" not in source or not version:
        raise RuntimeError(f"{api_name} response has an invalid API signature")
    if version.split(".", 1)[0] != "1":
        raise RuntimeError(f"unsupported {api_name} API version {version}")
    if payload.get("error"):
        raise RuntimeError(f"{api_name} error: {payload['error']}")
    return payload


def _horizons_result(payload: dict[str, Any], target_id: str) -> str:
    if "result" not in payload:
        message = payload.get("message") or payload
        raise RuntimeError(f"Horizons returned no result for {target_id}: {message}")
    return str(payload["result"])


def fetch_horizons_vector_data(
    target_id: str,
    epoch: str,
    *,
    time_type: str = "TDB",
    include_delta_t: bool = False,
) -> FetchedVector:
    url = build_horizons_url(
        target_id,
        epoch,
        time_type=time_type,
        include_delta_t=include_delta_t,
    )
    payload = _load_jpl_payload(url, "Horizons")
    result = _horizons_result(payload, target_id)
    delta_t_s = None
    if include_delta_t:
        vector, delta_t_s = parse_horizons_vector_with_delta_t(result)
    else:
        vector = parse_horizons_vector(result)
    return FetchedVector(
        vector=vector,
        physical=parse_horizons_physical_properties(result),
        source_url=url,
        delta_t_s=delta_t_s,
    )


def fetch_horizons_vector(target_id: str, epoch: str) -> StateVector:
    return fetch_horizons_vector_data(target_id, epoch).vector


def fetch_sbdb_physical(identifier: str) -> PhysicalProperties:
    payload = _load_jpl_payload(build_sbdb_url(identifier), "JPL SBDB")
    return parse_sbdb_physical_properties(payload)


def fetch_horizons_elements(
    target_id: str,
    epoch: str,
    center_id: str = "500@10",
) -> OrbitalElements:
    url = build_horizons_elements_url(target_id, epoch, center_id)
    payload = _load_jpl_payload(url, "Horizons")
    return parse_horizons_elements(_horizons_result(payload, target_id))


def load_preset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as preset_file:
        return json.load(preset_file)


def _physical_citation(
    physical: PhysicalProperties | None,
) -> str:
    if physical is None:
        return ""
    details = list(physical.notes)
    retained = []
    if physical.mass_kg is None:
        retained.append("mass")
    if physical.radius_m is None:
        retained.append("radius")
    if retained:
        details.append(f"Curated preset {' and '.join(retained)} retained")
    return "; ".join(details)


def apply_vectors(
    preset: dict[str, Any],
    vectors: dict[str, StateVector],
    epoch: str,
    targets: dict[str, str] | None = None,
    description: str | None = None,
    elements: dict[str, OrbitalElements] | None = None,
    retrieved_at: str | None = None,
    orbit_centers: dict[str, str] | None = None,
    physical_properties: dict[str, PhysicalProperties] | None = None,
    vector_urls: dict[str, str] | None = None,
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
    if physical_properties is not None:
        missing_physical = sorted(targets.keys() - physical_properties.keys())
        if missing_physical:
            raise ValueError(f"Missing fetched physical data: {', '.join(missing_physical)}")
    if vector_urls is not None:
        missing_urls = sorted(targets.keys() - vector_urls.keys())
        if missing_urls:
            raise ValueError(f"Missing fetched vector URLs: {', '.join(missing_urls)}")

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
        physical = (
            physical_properties[body_id]
            if physical_properties is not None
            else None
        )
        if physical is not None:
            if physical.mass_kg is not None:
                body["mass_kg"] = physical.mass_kg
            if physical.radius_m is not None:
                body["radius_m"] = physical.radius_m
        if elements is not None and body_id != "sun":
            body["orbit"] = elements[body_id].to_orbit_dict(epoch)
        citation = "JPL Horizons state vector"
        if elements is not None and body_id != "sun":
            citation += " and osculating elements"
        physical_note = _physical_citation(physical)
        if physical_note:
            citation += f"; {physical_note}"
        body["data_source"] = {
            "source_name": "JPL Horizons",
            "source_url": (
                vector_urls[body_id]
                if vector_urls is not None
                else build_horizons_url(targets[body_id], epoch)
            ),
            "catalog_id": targets[body_id],
            "retrieved_at": retrieved_at,
            "citation": f"{citation}.",
        }

    return SolarSystem.from_dict(updated).to_dict()


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


def _format_epoch(epoch: datetime) -> str:
    timespec = "microseconds" if epoch.microsecond else "seconds"
    return epoch.isoformat(sep=" ", timespec=timespec)


def fetch_preset_data(
    configs: list[dict[str, Any]],
    *,
    epoch: str | None = None,
    utc_now: datetime | None = None,
) -> FetchedPresetData:
    """Fetch shared preset data at one instant without mutating preset files."""

    target_ids = list(
        dict.fromkeys(
            target_id
            for config in configs
            for target_id in config["targets"].values()
        )
    )
    captured_utc = None
    if epoch is None:
        captured_utc = utc_now or datetime.now(timezone.utc)
        if captured_utc.tzinfo is None:
            raise ValueError("current preset refresh time must include a UTC offset")
        captured_utc = captured_utc.astimezone(timezone.utc).replace(microsecond=0)
        vector_epoch = captured_utc.replace(tzinfo=None).isoformat(
            sep=" ", timespec="seconds"
        )
        time_type = "UT"
        include_delta_t = True
    else:
        vector_epoch = epoch
        time_type = "TDB"
        include_delta_t = False

    vectors = {
        target_id: fetch_horizons_vector_data(
            target_id,
            vector_epoch,
            time_type=time_type,
            include_delta_t=include_delta_t,
        )
        for target_id in target_ids
    }

    if captured_utc is not None:
        delta_t_values = [
            item.delta_t_s
            for item in vectors.values()
            if item.delta_t_s is not None
        ]
        if len(delta_t_values) != len(vectors):
            raise RuntimeError("Horizons current-instant vectors are missing TDB-UT offsets")
        delta_t_s = delta_t_values[0]
        if any(abs(value - delta_t_s) > 1.0e-3 for value in delta_t_values[1:]):
            raise RuntimeError("Horizons returned inconsistent TDB-UT offsets")
        tdb_epoch = _format_epoch(
            captured_utc.replace(tzinfo=None) + timedelta(seconds=delta_t_s)
        )
        retrieved_at = captured_utc.date().isoformat()
        utc_epoch = captured_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
    else:
        tdb_epoch = str(epoch)
        retrieved_at = date.today().isoformat()
        utc_epoch = None

    sbdb_id_by_target: dict[str, str] = {}
    for config in configs:
        for body_id, sbdb_id in config["sbdb_targets"].items():
            sbdb_id_by_target[config["targets"][body_id]] = sbdb_id
    physical: dict[str, PhysicalProperties] = {}
    for target_id, fetched in vectors.items():
        properties = fetched.physical
        sbdb_id = sbdb_id_by_target.get(target_id)
        if sbdb_id is not None and (
            properties.mass_kg is None or properties.radius_m is None
        ):
            properties = properties.with_fallback(fetch_sbdb_physical(sbdb_id))
        physical[target_id] = properties

    element_requests = list(
        dict.fromkeys(
            (
                target_id,
                config["orbit_centers"].get(body_id, "500@10"),
            )
            for config in configs
            for body_id, target_id in config["targets"].items()
            if body_id != "sun"
        )
    )
    elements = {
        request: fetch_horizons_elements(request[0], tdb_epoch, request[1])
        for request in element_requests
    }
    return FetchedPresetData(
        tdb_epoch=tdb_epoch,
        retrieved_at=retrieved_at,
        utc_epoch=utc_epoch,
        vectors=vectors,
        elements=elements,
        physical=physical,
    )


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


def print_physical_summary(
    preset: dict[str, Any],
    physical: dict[str, PhysicalProperties],
    targets: dict[str, str],
) -> None:
    bodies = {body["id"]: body for body in preset["bodies"]}
    for body_id, target_id in targets.items():
        properties = physical[target_id]
        retained = []
        if properties.mass_kg is None:
            retained.append("mass")
        if properties.radius_m is None:
            retained.append("radius")
        details = list(properties.notes)
        if retained:
            details.append(f"curated {' and '.join(retained)} retained")
        body = bodies[body_id]
        print(
            f"{body_id:8s} "
            f"mass={body['mass_kg']:.9e} kg "
            f"radius={body['radius_m']:.9e} m "
            f"({'; '.join(details)})"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch JPL Horizons vectors and refresh bundled presets."
    )
    parser.add_argument(
        "--preset-set",
        choices=["all", *sorted(PRESET_CONFIGS)],
        default="solar-system",
        help="Bundled preset target set to refresh; all shares one captured instant",
    )
    parser.add_argument(
        "--epoch",
        default=None,
        help=(
            "Fixed TDB calendar epoch. If omitted, capture the current UTC instant "
            "and derive its matching TDB epoch from Horizons."
        ),
    )
    parser.add_argument(
        "--preset",
        type=Path,
        default=None,
        help="Preset JSON path for a single preset set",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Overwrite the preset file. Without this, only validate and print a summary.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.preset_set == "all" and args.preset is not None:
        raise SystemExit("--preset cannot be used with --preset-set all")

    preset_names = (
        list(PRESET_CONFIGS)
        if args.preset_set == "all"
        else [args.preset_set]
    )
    configs = [PRESET_CONFIGS[name] for name in preset_names]
    preset_paths = {
        name: args.preset or PRESET_CONFIGS[name]["path"]
        for name in preset_names
    }
    presets = {
        name: load_preset(preset_paths[name])
        for name in preset_names
    }
    fetched = fetch_preset_data(configs, epoch=args.epoch)
    if fetched.utc_epoch is not None:
        print(
            f"Captured {fetched.utc_epoch}; Horizons matching epoch is "
            f"{fetched.tdb_epoch} TDB."
        )

    updated_presets: dict[str, dict[str, Any]] = {}
    for name in preset_names:
        config = PRESET_CONFIGS[name]
        targets = config["targets"]
        orbit_centers = config["orbit_centers"]
        vectors = {
            body_id: fetched.vectors[target_id].vector
            for body_id, target_id in targets.items()
        }
        vector_urls = {
            body_id: fetched.vectors[target_id].source_url
            for body_id, target_id in targets.items()
        }
        physical = {
            body_id: fetched.physical[target_id]
            for body_id, target_id in targets.items()
        }
        elements = {
            body_id: fetched.elements[
                (target_id, orbit_centers.get(body_id, "500@10"))
            ]
            for body_id, target_id in targets.items()
            if body_id != "sun"
        }
        updated = apply_vectors(
            presets[name],
            vectors,
            fetched.tdb_epoch,
            targets,
            str(config["description"]),
            elements,
            fetched.retrieved_at,
            orbit_centers,
            physical,
            vector_urls,
        )
        updated_presets[name] = updated

        print(f"{name}:")
        print_summary(vectors, targets)
        print_elements_summary(elements, targets)
        print_physical_summary(updated, fetched.physical, targets)
        print(f"Validated {len(updated['bodies'])} bodies for epoch {updated['epoch']}.")

    if args.write:
        for name in preset_names:
            write_preset(preset_paths[name], updated_presets[name])
            print(f"Wrote {preset_paths[name]}.")
    else:
        print("Dry run only. Pass --write to update the preset file(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
