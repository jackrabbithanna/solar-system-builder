# horizons.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free JPL Horizons lookup and import services."""

from __future__ import annotations

import csv
import json
import math
import re
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from .constants import AU, DAY, G
from .models import Body, DataSource, ModelError, OrbitData, SolarSystem, SystemReferenceFrame
from .system_editing import BodyStateInput, add_body_from_state, default_body_state

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
HORIZONS_LOOKUP_URL = "https://ssd.jpl.nasa.gov/api/horizons_lookup.api"
SBDB_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"
KM_TO_M = 1000.0
KM_CUBED_TO_M_CUBED = 1.0e9
_FLOAT_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"


class HorizonsError(RuntimeError):
    """Raised when lookup, transport, or ephemeris data cannot be used."""


@dataclass(frozen=True)
class StateVector:
    position_m: list[float]
    velocity_mps: list[float]


@dataclass(frozen=True)
class PhysicalProperties:
    mass_kg: float | None = None
    radius_m: float | None = None
    mass_note: str = ""
    radius_note: str = ""

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(note for note in (self.mass_note, self.radius_note) if note)

    def with_fallback(self, fallback: PhysicalProperties) -> PhysicalProperties:
        return PhysicalProperties(
            mass_kg=self.mass_kg if self.mass_kg is not None else fallback.mass_kg,
            radius_m=self.radius_m if self.radius_m is not None else fallback.radius_m,
            mass_note=self.mass_note if self.mass_kg is not None else fallback.mass_note,
            radius_note=self.radius_note if self.radius_m is not None else fallback.radius_note,
        )


@dataclass(frozen=True)
class OrbitalElements:
    semi_major_axis_m: float
    orbital_period_s: float
    eccentricity: float
    inclination_deg: float
    longitude_of_ascending_node_deg: float
    argument_of_periapsis_deg: float
    mean_anomaly_deg: float
    reference_plane: str = "J2000 ecliptic"

    def to_orbit_data(self, epoch: str) -> OrbitData:
        return OrbitData(
            semi_major_axis_m=self.semi_major_axis_m,
            orbital_period_s=self.orbital_period_s,
            eccentricity=self.eccentricity,
            inclination_deg=self.inclination_deg,
            longitude_of_ascending_node_deg=self.longitude_of_ascending_node_deg,
            argument_of_periapsis_deg=self.argument_of_periapsis_deg,
            mean_anomaly_deg=self.mean_anomaly_deg,
            epoch=f"{epoch} TDB",
            reference_plane=self.reference_plane,
            approximation_notes=(
                "JPL Horizons parent-centered osculating elements at the system epoch; "
                "the imported Cartesian state is canonical."
            ),
        )

    def to_orbit_dict(self, epoch: str) -> dict[str, Any]:
        return self.to_orbit_data(epoch).to_dict()


@dataclass(frozen=True)
class HorizonsSearchResult:
    name: str
    object_type: str
    primary_designation: str
    spkid: str
    aliases: tuple[str, ...] = ()

    @property
    def suggested_kind(self) -> str | None:
        object_type = self.object_type.casefold()
        if "spacecraft" in object_type:
            return None
        # Small-body trajectories are labeled "integrated barycenter" even
        # though they represent the physical comet or asteroid being imported.
        if "comet" in object_type:
            return "comet"
        if "asteroid (integrated barycenter)" in object_type:
            return "asteroid"
        if "barycenter" in object_type or "asteroidal system" in object_type:
            return None
        if "satellite" in object_type:
            return "moon"
        if "asteroid" in object_type:
            return "asteroid"
        if "dwarf" in object_type:
            return "dwarf planet"
        if "planet" in object_type:
            return "planet"
        if "star" in object_type or self.name.casefold() in {"sun", "sol"}:
            return "star"
        return None

    @property
    def supported(self) -> bool:
        return self.suggested_kind is not None


@dataclass(frozen=True)
class HorizonsImportDraft:
    name: str
    kind: str
    catalog_id: str
    position_m: list[float]
    velocity_mps: list[float]
    orbit: OrbitData | None
    data_source: DataSource
    vector_center_catalog_id: str | None = None
    mass_kg: float | None = None
    radius_m: float | None = None
    physical_data_notes: tuple[str, ...] = ()
    warning: str = ""

    @property
    def missing_physical_fields(self) -> tuple[str, ...]:
        missing = []
        if self.mass_kg is None:
            missing.append("mass_kg")
        if self.radius_m is None:
            missing.append("radius_m")
        return tuple(missing)


@dataclass(frozen=True)
class HorizonsBodyRefresh:
    """One canonical body-state replacement returned by a system refresh."""

    body_id: str
    command: str
    position_m: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]
    orbit: OrbitData | None
    data_source: DataSource


@dataclass(frozen=True)
class HorizonsSystemRefresh:
    """A complete, single-epoch Horizons refresh that is safe to apply atomically."""

    system_id: str
    utc_epoch: str
    tdb_epoch: str
    bodies: tuple[HorizonsBodyRefresh, ...]


def parse_required_physical_value(value: str, field_name: str) -> float:
    cleaned = value.strip()
    if not cleaned:
        raise ModelError(f"{field_name} is required because Horizons does not provide it")
    try:
        number = float(cleaned)
    except ValueError as error:
        raise ModelError(f"{field_name} must be a number") from error
    if number <= 0.0 or not math.isfinite(number):
        raise ModelError(f"{field_name} must be a finite positive number")
    return number


def parse_horizons_vector(result_text: str) -> StateVector:
    row = _first_csv_ephemeris_row(result_text)
    if len(row) < 8:
        raise HorizonsError("Horizons vector row has too few columns")
    try:
        values = [float(item) * KM_TO_M for item in row[2:8]]
    except ValueError as error:
        raise HorizonsError("Horizons vector row contains invalid numbers") from error
    return StateVector(position_m=values[:3], velocity_mps=values[3:])


def parse_horizons_vector_with_delta_t(result_text: str) -> tuple[StateVector, float]:
    """Parse a vector row emitted with ``VEC_DELTA_T=YES``."""

    row = _first_csv_ephemeris_row(result_text)
    if len(row) < 9:
        raise HorizonsError("Horizons vector row is missing TDB-UT")
    try:
        delta_t_s = float(row[2])
        values = [float(item) * KM_TO_M for item in row[3:9]]
    except ValueError as error:
        raise HorizonsError("Horizons vector row contains invalid numbers") from error
    if not math.isfinite(delta_t_s):
        raise HorizonsError("Horizons vector row contains invalid TDB-UT")
    return StateVector(position_m=values[:3], velocity_mps=values[3:]), delta_t_s


def parse_horizons_elements(result_text: str) -> OrbitalElements:
    row = _first_csv_ephemeris_row(result_text)
    if len(row) < 14:
        raise HorizonsError("Horizons element row has too few columns")
    try:
        return OrbitalElements(
            eccentricity=float(row[2]),
            inclination_deg=float(row[4]),
            longitude_of_ascending_node_deg=float(row[5]),
            argument_of_periapsis_deg=float(row[6]),
            mean_anomaly_deg=float(row[9]),
            semi_major_axis_m=float(row[11]) * AU,
            orbital_period_s=float(row[13]) * DAY,
        )
    except ValueError as error:
        raise HorizonsError("Horizons element row contains invalid numbers") from error


def parse_horizons_physical_properties(result_text: str) -> PhysicalProperties:
    """Extract documented physical values from a Horizons object-data block."""
    mass_kg = None
    mass_note = ""
    gm_match = re.search(
        rf"^\s*GM(?!\s*1-sigma)[^\n=]*=\s*({_FLOAT_PATTERN})",
        result_text,
        re.IGNORECASE | re.MULTILINE,
    )
    if gm_match:
        gm_km3_s2 = _positive_float(gm_match.group(1))
        if gm_km3_s2 is not None:
            mass_kg = gm_km3_s2 * KM_CUBED_TO_M_CUBED / G
            mass_note = "Mass derived from JPL GM"
    if mass_kg is None:
        mass_match = re.search(
            rf"\bMass\s*,?\s*(?:x\s*)?10\^([+-]?\d+)\s*"
            rf"(?:\(\s*kg\s*\)|kg)\s*=\s*~?\s*({_FLOAT_PATTERN})",
            result_text,
            re.IGNORECASE,
        )
        if mass_match:
            coefficient = _positive_float(mass_match.group(2))
            if coefficient is not None:
                candidate = coefficient * (10.0 ** int(mass_match.group(1)))
                if math.isfinite(candidate) and candidate > 0.0:
                    mass_kg = candidate
                    mass_note = "Mass supplied by JPL Horizons"

    radius_m = None
    radius_note = ""
    mean_radius_match = re.search(
        rf"\bVol\.\s*Mean Radius\s*(?:\(\s*km\s*\)|,\s*km)\s*"
        rf"=\s*~?\s*({_FLOAT_PATTERN})",
        result_text,
        re.IGNORECASE,
    )
    if mean_radius_match:
        radius_km = _positive_float(mean_radius_match.group(1))
        if radius_km is not None:
            radius_m = radius_km * KM_TO_M
            radius_note = "Mean radius supplied by JPL Horizons"
    if radius_m is None:
        small_body_radius_match = re.search(
            rf"\bRAD\s*=\s*({_FLOAT_PATTERN})",
            result_text,
            re.IGNORECASE,
        )
        if small_body_radius_match:
            radius_km = _positive_float(small_body_radius_match.group(1))
            if radius_km is not None:
                radius_m = radius_km * KM_TO_M
                radius_note = "Radius supplied by JPL Horizons"

    return PhysicalProperties(mass_kg, radius_m, mass_note, radius_note)


def parse_sbdb_physical_properties(payload: dict[str, Any]) -> PhysicalProperties:
    """Parse structured small-body GM and diameter values from JPL SBDB."""
    parameters = payload.get("phys_par")
    if not isinstance(parameters, list):
        return PhysicalProperties()
    values = {
        str(item.get("name", "")).casefold(): item
        for item in parameters
        if isinstance(item, dict)
    }

    mass_kg = None
    mass_note = ""
    gm = values.get("gm")
    if gm is not None and _normalized_unit(gm.get("units")) == "km^3/s^2":
        gm_km3_s2 = _positive_float(gm.get("value"))
        if gm_km3_s2 is not None:
            mass_kg = gm_km3_s2 * KM_CUBED_TO_M_CUBED / G
            mass_note = "Mass derived from JPL SBDB GM"

    radius_m = None
    radius_note = ""
    diameter = values.get("diameter")
    if diameter is not None and _normalized_unit(diameter.get("units")) == "km":
        diameter_km = _positive_float(diameter.get("value"))
        if diameter_km is not None:
            radius_m = diameter_km * KM_TO_M / 2.0
            radius_note = "Radius derived from JPL SBDB diameter"

    return PhysicalProperties(mass_kg, radius_m, mass_note, radius_note)


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0.0 and math.isfinite(number) else None


def _normalized_unit(value: Any) -> str:
    return str(value or "").replace(" ", "").casefold()


def _first_csv_ephemeris_row(result_text: str) -> list[str]:
    in_table = False
    for line in result_text.splitlines():
        stripped = line.strip()
        if stripped == "$$SOE":
            in_table = True
            continue
        if stripped == "$$EOE":
            break
        if in_table and stripped:
            return next(csv.reader([stripped], skipinitialspace=True))
    raise HorizonsError("Horizons response did not contain ephemeris data")


def build_lookup_url(query: str, group: str | None = None) -> str:
    cleaned = query.strip()
    if not cleaned:
        raise ModelError("enter a Horizons search term")
    params = {"sstr": cleaned}
    if group:
        params["group"] = group
    return f"{HORIZONS_LOOKUP_URL}?{urlencode(params)}"


def build_sbdb_url(identifier: str) -> str:
    cleaned = identifier.strip()
    if not cleaned:
        raise ModelError("a JPL small-body identifier is required")
    params = {"sstr": cleaned, "phys-par": "true", "full-prec": "true"}
    return f"{SBDB_URL}?{urlencode(params)}"


def horizons_command_for_result(
    result: HorizonsSearchResult,
    frame: SystemReferenceFrame,
) -> str:
    """Translate lookup API identifiers into unambiguous Horizons commands."""
    designation = result.primary_designation.strip().replace(";", "")
    if not designation:
        return result.spkid
    kind = result.suggested_kind
    if kind == "comet":
        year = frame.epoch.split("-", 1)[0]
        return f"DES={designation}; CAP < {year};"
    if kind == "asteroid":
        return f"DES={designation};"
    return result.spkid


def horizons_command_for_body(body: Body) -> str:
    """Recover the exact Horizons command saved for an existing body."""

    source = body.data_source
    if source is not None and source.source_name.casefold() == "jpl horizons":
        parsed = urlparse(source.source_url.strip())
        if (
            parsed.scheme == "https"
            and parsed.hostname in {"ssd.jpl.nasa.gov", "ssd-api.jpl.nasa.gov"}
            and parsed.path.rstrip("/") == "/api/horizons.api"
        ):
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items():
                if key.casefold() != "command" or not values:
                    continue
                command = values[0].strip()
                if len(command) >= 2 and command[0] == command[-1] and command[0] in "'\"":
                    command = command[1:-1].strip()
                if command:
                    return command
        catalog_id = source.catalog_id.strip()
        if catalog_id:
            return catalog_id
    if body.kind == "star" and body.name.casefold() in {"sun", "sol"}:
        return "10"
    raise ModelError(f"{body.name} does not have a reusable JPL Horizons command")


def build_vector_url(
    target_id: str,
    frame: SystemReferenceFrame,
    *,
    center_catalog_id: str | None = None,
    time_type: str | None = None,
    include_delta_t: bool = False,
    include_object_data: bool = True,
) -> str:
    _validate_horizons_frame(frame)
    selected_time_type = time_type or frame.time_scale
    if selected_time_type not in {"UT", "TT", "TDB"}:
        raise ModelError("Horizons vector time type must be UT, TT, or TDB")
    center_id = frame.center_id
    if center_catalog_id is not None:
        cleaned_center = center_catalog_id.strip()
        if not cleaned_center:
            raise ModelError("Horizons vector center catalog id is required")
        center_id = f"500@{cleaned_center}"
    params = {
        "format": "json",
        "COMMAND": f"'{target_id}'",
        "OBJ_DATA": "YES" if include_object_data else "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": f"'{center_id}'",
        "TLIST": f"'{frame.epoch}'",
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": selected_time_type,
        "TIME_DIGITS": "FRACSEC",
        "REF_PLANE": frame.reference_plane,
        "REF_SYSTEM": frame.reference_system,
        "OUT_UNITS": "KM-S",
        "VEC_TABLE": "2",
        "VEC_LABELS": "NO",
        "VEC_CORR": "NONE",
        "CSV_FORMAT": "YES",
    }
    if include_delta_t:
        params["VEC_DELTA_T"] = "YES"
    return f"{HORIZONS_URL}?{urlencode(params)}"


def shift_horizons_frame_epoch(
    frame: SystemReferenceFrame,
    elapsed_s: float,
) -> SystemReferenceFrame:
    """Return a compatible frame whose epoch includes simulation elapsed time."""

    _validate_horizons_frame(frame)
    if not math.isfinite(elapsed_s):
        raise ModelError("simulation elapsed time must be finite")
    if elapsed_s == 0.0:
        return replace(frame)
    try:
        epoch = datetime.fromisoformat(frame.epoch.strip())
    except ValueError as error:
        raise ModelError(
            "Horizons reference-frame epoch must be an ISO date and time "
            "before playback can be offset"
        ) from error
    shifted = epoch + timedelta(seconds=elapsed_s)
    timespec = "microseconds" if shifted.microsecond else "seconds"
    return replace(frame, epoch=shifted.isoformat(sep=" ", timespec=timespec))


def build_elements_url(
    target_id: str,
    frame: SystemReferenceFrame,
    parent_catalog_id: str,
) -> str:
    _validate_horizons_frame(frame)
    params = {
        "format": "json",
        "COMMAND": f"'{target_id}'",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "ELEMENTS",
        "CENTER": f"'500@{parent_catalog_id}'",
        "TLIST": f"'{frame.epoch}'",
        "TLIST_TYPE": "CAL",
        "TIME_TYPE": frame.time_scale,
        "TIME_DIGITS": "FRACSEC",
        "REF_PLANE": frame.reference_plane,
        "REF_SYSTEM": frame.reference_system,
        "OUT_UNITS": "AU-D",
        "CSV_FORMAT": "YES",
        "ELM_LABELS": "NO",
    }
    return f"{HORIZONS_URL}?{urlencode(params)}"


class HorizonsClient:
    """A serialized client; JPL asks API consumers not to issue concurrent calls."""

    def __init__(
        self,
        *,
        timeout_s: float = 30.0,
        opener: Callable[..., Any] = urlopen,
    ):
        self.timeout_s = timeout_s
        self._opener = opener
        self._request_lock = threading.Lock()

    def search(self, query: str, group: str | None = None) -> list[HorizonsSearchResult]:
        payload = self._get_json(build_lookup_url(query, group), api_name="Horizons Lookup")
        try:
            count = int(payload.get("count", 0))
        except (TypeError, ValueError) as error:
            raise HorizonsError("Horizons Lookup returned an invalid result count") from error
        if count == 0:
            return []
        results = payload.get("result")
        if not isinstance(results, list):
            raise HorizonsError("Horizons Lookup returned invalid results")
        return [
            HorizonsSearchResult(
                name=str(item.get("name", "")),
                object_type=str(item.get("type", "")),
                primary_designation=str(item.get("pdes") or ""),
                spkid=str(item.get("spkid", "")),
                aliases=tuple(str(alias) for alias in item.get("alias", [])),
            )
            for item in results
            if isinstance(item, dict) and item.get("spkid")
        ]

    def fetch_import(
        self,
        result: HorizonsSearchResult,
        frame: SystemReferenceFrame,
        *,
        parent_catalog_id: str | None = None,
    ) -> HorizonsImportDraft:
        kind = result.suggested_kind
        if kind is None:
            raise HorizonsError(f"{result.name} is not a supported physical body")
        parent_catalog_id = parent_catalog_id.strip() if parent_catalog_id else None
        target_command = horizons_command_for_result(result, frame)
        vector_url = build_vector_url(
            target_command,
            frame,
            center_catalog_id=parent_catalog_id,
        )
        vector_payload = self._get_json(vector_url, api_name="Horizons")
        vector_text = _result_text(vector_payload, result.spkid)
        vector = parse_horizons_vector(vector_text)
        physical = parse_horizons_physical_properties(vector_text)
        orbit = None
        warning = ""
        source_url = vector_url
        if kind in {"asteroid", "comet"} and (
            physical.mass_kg is None or physical.radius_m is None
        ):
            try:
                sbdb_url = build_sbdb_url(result.spkid)
                sbdb_payload = self._get_json(sbdb_url, api_name="JPL SBDB")
                physical = physical.with_fallback(
                    parse_sbdb_physical_properties(sbdb_payload)
                )
            except HorizonsError as error:
                warning = f"JPL physical data unavailable: {error}"
        if parent_catalog_id:
            try:
                elements_url = build_elements_url(
                    target_command,
                    frame,
                    parent_catalog_id,
                )
                elements_payload = self._get_json(elements_url, api_name="Horizons")
                elements = parse_horizons_elements(_result_text(elements_payload, result.spkid))
                orbit = elements.to_orbit_data(frame.epoch)
                source_url = elements_url
            except HorizonsError as error:
                warning = _append_warning(
                    warning,
                    f"State vector imported; orbital elements unavailable: {error}",
                )
        citation = "JPL Horizons state vector"
        if orbit is not None:
            citation += " and osculating elements"
        if physical.mass_kg is not None or physical.radius_m is not None:
            citation += "; JPL physical parameters"
        return HorizonsImportDraft(
            name=result.name,
            kind=kind,
            catalog_id=result.spkid,
            position_m=vector.position_m,
            velocity_mps=vector.velocity_mps,
            orbit=orbit,
            data_source=DataSource(
                source_name="JPL Horizons",
                source_url=source_url,
                catalog_id=result.spkid,
                retrieved_at=date.today().isoformat(),
                citation=f"{citation}.",
            ),
            vector_center_catalog_id=parent_catalog_id,
            mass_kg=physical.mass_kg,
            radius_m=physical.radius_m,
            physical_data_notes=physical.notes,
            warning=warning,
        )

    def fetch_system_refresh(
        self,
        system: SolarSystem,
        utc_now: datetime,
        *,
        cancel_event: threading.Event | None = None,
        progress: Callable[[int, int, str, str], None] | None = None,
    ) -> HorizonsSystemRefresh:
        """Fetch every Horizons-backed body without mutating ``system``."""

        frame = system.reference_frame
        if frame is None or not frame.horizons_compatible:
            raise ModelError("system reference frame is not compatible with Horizons refresh")
        bodies = horizons_refreshable_bodies(system)
        if not bodies:
            raise ModelError("system does not contain any JPL Horizons bodies")
        if utc_now.tzinfo is None:
            raise ModelError("current refresh time must include a UTC offset")
        captured_utc = utc_now.astimezone(timezone.utc).replace(microsecond=0)
        utc_calendar = captured_utc.replace(tzinfo=None).isoformat(
            sep=" ", timespec="seconds"
        )
        utc_frame = replace(frame, epoch=utc_calendar)

        commands: dict[str, str] = {}
        parent_catalog_ids: dict[str, str] = {}
        bodies_by_id = {body.id: body for body in system.bodies}
        for body in bodies:
            commands[body.id] = horizons_command_for_body(body)
            if body.parent_id is None:
                continue
            parent = bodies_by_id.get(body.parent_id)
            if parent is None:
                raise ModelError(f"{body.name} parent body does not exist")
            parent_catalog_id = horizons_catalog_id(parent)
            if parent_catalog_id is None:
                raise ModelError(
                    f"{body.name} parent {parent.name} does not have a JPL Horizons catalog id"
                )
            parent_catalog_ids[body.id] = parent_catalog_id

        total = len(bodies) + len(parent_catalog_ids)
        completed = 0
        vectors: dict[str, tuple[StateVector, str]] = {}
        delta_t_s: float | None = None
        for body in bodies:
            _raise_if_refresh_cancelled(cancel_event)
            if progress is not None:
                progress(completed, total, body.name, "Fetching current state")
            vector_url = build_vector_url(
                commands[body.id],
                utc_frame,
                time_type="UT",
                include_delta_t=True,
                include_object_data=False,
            )
            try:
                payload = self._get_json(vector_url, api_name="Horizons")
                vector, body_delta_t_s = parse_horizons_vector_with_delta_t(
                    _result_text(payload, commands[body.id])
                )
            except HorizonsError as error:
                raise HorizonsError(f"{body.name}: {error}") from error
            if delta_t_s is None:
                delta_t_s = body_delta_t_s
            elif abs(body_delta_t_s - delta_t_s) > 1.0e-3:
                raise HorizonsError("Horizons returned inconsistent TDB-UT offsets")
            vectors[body.id] = (vector, vector_url)
            completed += 1

        if delta_t_s is None:
            raise HorizonsError("Horizons refresh did not return a TDB-UT offset")
        tdb_datetime = captured_utc.replace(tzinfo=None) + timedelta(seconds=delta_t_s)
        tdb_epoch = _format_horizons_epoch(tdb_datetime)
        tdb_frame = replace(frame, epoch=tdb_epoch)

        updates: list[HorizonsBodyRefresh] = []
        for body in bodies:
            orbit = None
            parent_catalog_id = parent_catalog_ids.get(body.id)
            if parent_catalog_id is not None:
                _raise_if_refresh_cancelled(cancel_event)
                if progress is not None:
                    progress(completed, total, body.name, "Fetching orbital elements")
                try:
                    elements_url = build_elements_url(
                        commands[body.id],
                        tdb_frame,
                        parent_catalog_id,
                    )
                    elements_payload = self._get_json(elements_url, api_name="Horizons")
                    elements = parse_horizons_elements(
                        _result_text(elements_payload, commands[body.id])
                    )
                except HorizonsError as error:
                    raise HorizonsError(f"{body.name}: {error}") from error
                orbit = elements.to_orbit_data(tdb_epoch)
                completed += 1
            vector, vector_url = vectors[body.id]
            catalog_id = horizons_catalog_id(body) or commands[body.id]
            citation = "JPL Horizons state vector"
            if orbit is not None:
                citation += " and osculating elements"
            updates.append(
                HorizonsBodyRefresh(
                    body_id=body.id,
                    command=commands[body.id],
                    position_m=tuple(vector.position_m),
                    velocity_mps=tuple(vector.velocity_mps),
                    orbit=orbit,
                    data_source=DataSource(
                        source_name="JPL Horizons",
                        source_url=vector_url,
                        catalog_id=catalog_id,
                        retrieved_at=captured_utc.date().isoformat(),
                        citation=f"{citation}.",
                    ),
                )
            )

        _raise_if_refresh_cancelled(cancel_event)
        if progress is not None:
            progress(total, total, "JPL Horizons", "Finishing refresh")
        return HorizonsSystemRefresh(
            system_id=system.id,
            utc_epoch=captured_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            tdb_epoch=tdb_epoch,
            bodies=tuple(updates),
        )

    def _get_json(self, url: str, *, api_name: str) -> dict[str, Any]:
        try:
            with self._request_lock:
                with self._opener(url, timeout=self.timeout_s) as response:
                    payload = json.load(response)
        except HorizonsError:
            raise
        except Exception as error:
            raise HorizonsError(f"{api_name} request failed: {error}") from error
        if not isinstance(payload, dict):
            raise HorizonsError(f"{api_name} returned an invalid response")
        _validate_signature(payload, api_name)
        if payload.get("error"):
            raise HorizonsError(f"{api_name} error: {payload['error']}")
        return payload


def horizons_import_available(system: SolarSystem) -> bool:
    frame = system.reference_frame
    if frame is None or not frame.horizons_compatible:
        return False
    return any(
        body.kind == "star"
        and body.parent_id is None
        and (
            body.name.casefold() in {"sun", "sol"}
            or (body.data_source is not None and body.data_source.catalog_id == "10")
        )
        for body in system.bodies
    )


def horizons_refreshable_bodies(system: SolarSystem) -> list[Body]:
    """Return bodies whose canonical state can be refreshed from Horizons."""

    frame = system.reference_frame
    if frame is None or not frame.horizons_compatible:
        return []
    return [
        body
        for body in system.bodies
        if (
            body.data_source is not None
            and body.data_source.source_name.casefold() == "jpl horizons"
        )
        or (body.kind == "star" and body.name.casefold() in {"sun", "sol"})
    ]


def horizons_refresh_available(system: SolarSystem) -> bool:
    return bool(horizons_refreshable_bodies(system))


def horizons_catalog_id(body: Body) -> str | None:
    if (
        body.data_source is not None
        and body.data_source.source_name.casefold() == "jpl horizons"
        and body.data_source.catalog_id.strip()
    ):
        return body.data_source.catalog_id.strip()
    if body.kind == "star" and body.name.casefold() in {"sun", "sol"}:
        return "10"
    return None


def add_imported_body(
    system: SolarSystem,
    draft: HorizonsImportDraft,
    *,
    mass_kg: float,
    radius_m: float,
    parent_id: str,
    kind: str | None = None,
    color: str | None = None,
) -> str:
    if not horizons_import_available(system):
        raise ModelError("Horizons import requires a Sol system with a compatible reference frame")
    if any(
        body.data_source is not None and body.data_source.catalog_id == draft.catalog_id
        for body in system.bodies
    ):
        raise ModelError(f"{draft.name} is already present in this system")
    selected_kind = kind or draft.kind
    parent = next((body for body in system.bodies if body.id == parent_id), None)
    if parent is None:
        raise ModelError("parent body does not exist")
    position_m = list(draft.position_m)
    velocity_mps = list(draft.velocity_mps)
    if draft.vector_center_catalog_id is not None:
        parent_catalog_id = horizons_catalog_id(parent)
        if parent_catalog_id is None:
            raise ModelError(
                f"{parent.name} does not have a Horizons catalog id for relative-state import"
            )
        if parent_catalog_id != draft.vector_center_catalog_id:
            raise ModelError(
                "imported vector center does not match the selected parent body"
            )
        position_m = [
            parent.position_m[axis] + draft.position_m[axis]
            for axis in range(3)
        ]
        velocity_mps = [
            parent.velocity_mps[axis] + draft.velocity_mps[axis]
            for axis in range(3)
        ]
    elif selected_kind == "moon":
        raise ModelError("Horizons moon import requires a parent with a Horizons catalog id")
    defaults = default_body_state(draft.name, selected_kind)
    body = add_body_from_state(
        system,
        BodyStateInput(
            name=draft.name,
            kind=selected_kind,
            mass_kg=mass_kg,
            radius_m=radius_m,
            position_m=tuple(position_m),
            velocity_mps=tuple(velocity_mps),
            color=color or defaults.color,
            parent_id=parent_id,
        ),
    )
    try:
        body.orbit = draft.orbit
        body.data_source = draft.data_source
        body.state_origin = "horizons"
        system.validate()
    except Exception:
        system.bodies = [candidate for candidate in system.bodies if candidate.id != body.id]
        for group in system.groups:
            group.body_ids = [body_id for body_id in group.body_ids if body_id != body.id]
        raise
    return body.id


def apply_system_refresh(
    system: SolarSystem,
    refresh: HorizonsSystemRefresh,
) -> SolarSystem:
    """Return a validated clone with an all-or-nothing refresh applied."""

    if refresh.system_id != system.id:
        raise ModelError("Horizons refresh belongs to a different system")
    current_bodies = horizons_refreshable_bodies(system)
    current_ids = {body.id for body in current_bodies}
    update_ids = {update.body_id for update in refresh.bodies}
    if current_ids != update_ids or len(update_ids) != len(refresh.bodies):
        raise ModelError("system bodies changed while Horizons refresh was running")

    candidate = SolarSystem.from_dict(system.to_dict())
    candidate_by_id = {body.id: body for body in candidate.bodies}
    for update in refresh.bodies:
        body = candidate_by_id[update.body_id]
        if horizons_command_for_body(body) != update.command:
            raise ModelError(f"{body.name} Horizons source changed during refresh")
        body.position_m = list(update.position_m)
        body.velocity_mps = list(update.velocity_mps)
        body.orbit = update.orbit
        body.data_source = update.data_source
        body.state_origin = "horizons"

    frame = candidate.reference_frame
    if frame is None:
        raise ModelError("Horizons refresh requires a system reference frame")
    frame.epoch = refresh.tdb_epoch
    center_label = (
        "heliocentric" if frame.center_id == "500@10" else "solar-system barycentric"
    )
    candidate.epoch = f"{refresh.tdb_epoch} TDB, JPL Horizons, {center_label}"
    candidate.validate()
    return candidate


def _validate_horizons_frame(frame: SystemReferenceFrame) -> None:
    frame.validate()
    if not frame.horizons_compatible:
        raise ModelError("system reference frame is not compatible with Horizons import")


def _format_horizons_epoch(epoch: datetime) -> str:
    timespec = "microseconds" if epoch.microsecond else "seconds"
    return epoch.isoformat(sep=" ", timespec=timespec)


def _raise_if_refresh_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise HorizonsError("Horizons refresh cancelled")


def _validate_signature(payload: dict[str, Any], api_name: str) -> None:
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise HorizonsError(f"{api_name} response is missing an API signature")
    source = str(signature.get("source", ""))
    version = str(signature.get("version", ""))
    if "NASA/JPL" not in source or not version:
        raise HorizonsError(f"{api_name} response has an invalid API signature")
    if version.split(".", 1)[0] != "1":
        raise HorizonsError(f"unsupported {api_name} API version {version}")


def _result_text(payload: dict[str, Any], target_id: str) -> str:
    result = payload.get("result")
    if result is None:
        raise HorizonsError(f"Horizons returned no ephemeris for {target_id}")
    return str(result)


def _append_warning(existing: str, warning: str) -> str:
    return f"{existing}\n{warning}" if existing else warning
