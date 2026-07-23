# models.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Schema-versioned domain models for solar systems."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .constants import DAY

SCHEMA_VERSION = 14

ACCURACY_PROFILES = {"high", "balanced", "fast"}
PHYSICS_MODES = {"newtonian", "post_newtonian"}
INTEGRATORS = {"velocity_verlet", "rk4"}
RECLASSIFIABLE_BODY_KINDS = ("planet", "dwarf planet", "comet", "asteroid")
BODY_KINDS = frozenset({"star", "moon", *RECLASSIFIABLE_BODY_KINDS})
STATE_ORIGINS = frozenset({"cartesian", "orbital", "horizons", "flyby"})
REFERENCE_FRAME_SOURCES = frozenset({"app_local", "horizons", "standard"})
REFERENCE_ORIGIN_KINDS = frozenset(
    {"custom", "jpl", "body", "group_barycenter", "system_barycenter"}
)
REFERENCE_TIME_SCALES = frozenset({"UTC", "UT1", "TAI", "TT", "TDB", "TCB", "TCG"})
REFERENCE_AXES = {
    "custom": ("app-local", "app-local XY"),
    "icrf": ("ICRF", "FRAME"),
    "fk5_j2000": ("FK5", "mean equator/equinox J2000"),
    "mean_equator_of_date": ("IAU 2006", "mean equator/equinox of date"),
    "true_equator_of_date": ("IAU 2006/2000A", "true equator/equinox of date"),
    "jpl_ecliptic_j2000": ("ICRF", "ECLIPTIC"),
    "mean_ecliptic_of_date": ("IAU 2006", "mean ecliptic/equinox of date"),
    "true_ecliptic_of_date": ("IAU 2006/2000A", "true ecliptic/equinox of date"),
    "galactic_iau1958": ("IAU Galactic", "Galactic plane"),
}
DISTANCE_UNITS = {"km", "AU", "kAU", "ly"}
VIEW_MODES = {"fit_system", "follow_selected", "fixed_scale", "log_overview"}
TRAIL_FRAMES = {"focused_parent", "system_inertial"}
ORBIT_VISIBILITY_MODES = {"off", "selected", "all"}
TRAIL_VISIBILITY_MODES = {"off", "selected", "all"}
PATH_STYLES = {"subtle", "standard", "bold"}
SIMULATION_SCOPES = {
    "auto",
    "full_nbody",
    "stellar_overview",
    "focused_subsystem",
    "system_overview",
    "hybrid_focused_context",
}


class ModelError(ValueError):
    """Raised when serialized model data is invalid."""


def _optional_positive_float(data: dict[str, Any], field_name: str) -> float | None:
    if data.get(field_name) is None:
        return None
    value = float(data[field_name])
    if value <= 0.0 or not math.isfinite(value):
        raise ModelError(f"{field_name} must be positive")
    return value


def _optional_nonzero_float(data: dict[str, Any], field_name: str) -> float | None:
    if data.get(field_name) is None:
        return None
    value = float(data[field_name])
    if value == 0.0 or not math.isfinite(value):
        raise ModelError(f"{field_name} must be finite and nonzero")
    return value


def _optional_float(data: dict[str, Any], field_name: str) -> float | None:
    if data.get(field_name) is None:
        return None
    value = float(data[field_name])
    if not math.isfinite(value):
        raise ModelError(f"{field_name} must be finite")
    return value


def _vector3(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ModelError(f"{field_name} must be a 3-value vector")
    vector = [float(component) for component in value]
    if not all(math.isfinite(component) for component in vector):
        raise ModelError(f"{field_name} must contain finite values")
    return vector


@dataclass
class OrbitData:
    semi_major_axis_m: float | None = None
    orbital_period_s: float | None = None
    eccentricity: float | None = None
    inclination_deg: float | None = None
    longitude_of_ascending_node_deg: float | None = None
    argument_of_periapsis_deg: float | None = None
    mean_anomaly_deg: float | None = None
    epoch: str = ""
    reference_plane: str = "app-local XY"
    approximation_notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "OrbitData | None":
        if data is None:
            return None
        orbit = cls(
            semi_major_axis_m=_optional_nonzero_float(data, "semi_major_axis_m"),
            orbital_period_s=_optional_positive_float(data, "orbital_period_s"),
            eccentricity=_optional_float(data, "eccentricity"),
            inclination_deg=_optional_float(data, "inclination_deg"),
            longitude_of_ascending_node_deg=_optional_float(
                data,
                "longitude_of_ascending_node_deg",
            ),
            argument_of_periapsis_deg=_optional_float(data, "argument_of_periapsis_deg"),
            mean_anomaly_deg=_optional_float(data, "mean_anomaly_deg"),
            epoch=str(data.get("epoch", "")),
            reference_plane=str(data.get("reference_plane", "app-local XY")),
            approximation_notes=str(data.get("approximation_notes", "")),
        )
        orbit.validate()
        return orbit

    def validate(self) -> None:
        if self.semi_major_axis_m is None and self.orbital_period_s is None:
            raise ModelError("orbit requires semi_major_axis_m or orbital_period_s")
        if self.semi_major_axis_m is not None and not math.isfinite(self.semi_major_axis_m):
            raise ModelError("semi_major_axis_m must be finite")
        if self.orbital_period_s is not None and (
            self.orbital_period_s <= 0.0 or not math.isfinite(self.orbital_period_s)
        ):
            raise ModelError("orbital_period_s must be finite and positive")
        eccentricity = self.eccentricity if self.eccentricity is not None else 0.0
        if not math.isfinite(eccentricity) or eccentricity < 0.0 or eccentricity == 1.0:
            raise ModelError("eccentricity must be nonnegative and cannot equal 1")
        if eccentricity < 1.0:
            if self.semi_major_axis_m is not None and self.semi_major_axis_m <= 0.0:
                raise ModelError("elliptic semi_major_axis_m must be positive")
        else:
            if self.semi_major_axis_m is None or self.semi_major_axis_m >= 0.0:
                raise ModelError("hyperbolic semi_major_axis_m must be negative")
            if self.orbital_period_s is not None:
                raise ModelError("hyperbolic orbit cannot have orbital_period_s")
        for field_name in (
            "inclination_deg",
            "longitude_of_ascending_node_deg",
            "argument_of_periapsis_deg",
            "mean_anomaly_deg",
        ):
            value = getattr(self, field_name)
            if value is not None and not math.isfinite(value):
                raise ModelError(f"{field_name} must be finite")
        if not self.reference_plane.strip():
            raise ModelError("reference_plane is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data: dict[str, Any] = {
            "reference_plane": self.reference_plane,
        }
        for field_name in (
            "semi_major_axis_m",
            "orbital_period_s",
            "eccentricity",
            "inclination_deg",
            "longitude_of_ascending_node_deg",
            "argument_of_periapsis_deg",
            "mean_anomaly_deg",
        ):
            value = getattr(self, field_name)
            if value is not None:
                data[field_name] = value
        if self.epoch:
            data["epoch"] = self.epoch
        if self.approximation_notes:
            data["approximation_notes"] = self.approximation_notes
        return data


@dataclass
class FlybyData:
    """User-facing parameters for a persistent unbound trajectory."""

    anchor_body_id: str
    periapsis_distance_m: float
    velocity_at_infinity_mps: float
    start_distance_m: float
    inclination_deg: float = 0.0
    longitude_of_ascending_node_deg: float = 0.0
    argument_of_periapsis_deg: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FlybyData | None":
        if data is None:
            return None
        flyby = cls(
            anchor_body_id=str(data.get("anchor_body_id", "")),
            periapsis_distance_m=float(data["periapsis_distance_m"]),
            velocity_at_infinity_mps=float(data["velocity_at_infinity_mps"]),
            start_distance_m=float(data["start_distance_m"]),
            inclination_deg=float(data.get("inclination_deg", 0.0)),
            longitude_of_ascending_node_deg=float(
                data.get("longitude_of_ascending_node_deg", 0.0)
            ),
            argument_of_periapsis_deg=float(data.get("argument_of_periapsis_deg", 0.0)),
        )
        flyby.validate()
        return flyby

    def validate(self) -> None:
        if not self.anchor_body_id:
            raise ModelError("flyby anchor_body_id is required")
        for field_name in (
            "periapsis_distance_m",
            "velocity_at_infinity_mps",
            "start_distance_m",
        ):
            value = getattr(self, field_name)
            if value <= 0.0 or not math.isfinite(value):
                raise ModelError(f"flyby {field_name} must be finite and positive")
        if self.start_distance_m <= self.periapsis_distance_m:
            raise ModelError("flyby start_distance_m must be greater than periapsis_distance_m")
        for field_name in (
            "inclination_deg",
            "longitude_of_ascending_node_deg",
            "argument_of_periapsis_deg",
        ):
            if not math.isfinite(getattr(self, field_name)):
                raise ModelError(f"flyby {field_name} must be finite")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "anchor_body_id": self.anchor_body_id,
            "periapsis_distance_m": self.periapsis_distance_m,
            "velocity_at_infinity_mps": self.velocity_at_infinity_mps,
            "start_distance_m": self.start_distance_m,
            "inclination_deg": self.inclination_deg,
            "longitude_of_ascending_node_deg": self.longitude_of_ascending_node_deg,
            "argument_of_periapsis_deg": self.argument_of_periapsis_deg,
        }


@dataclass
class DataSource:
    source_name: str = ""
    source_url: str = ""
    catalog_id: str = ""
    retrieved_at: str = ""
    citation: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DataSource | None":
        if data is None:
            return None
        source = cls(
            source_name=str(data.get("source_name", "")),
            source_url=str(data.get("source_url", "")),
            catalog_id=str(data.get("catalog_id", "")),
            retrieved_at=str(data.get("retrieved_at", "")),
            citation=str(data.get("citation", "")),
        )
        return source if source.to_dict() else None

    def to_dict(self) -> dict[str, Any]:
        data = {}
        for field_name in ("source_name", "source_url", "catalog_id", "retrieved_at", "citation"):
            value = getattr(self, field_name).strip()
            if value:
                data[field_name] = value
        return data


@dataclass
class ReferenceOrigin:
    """Origin identifier for the canonical state at its reference epoch."""

    kind: str = "custom"
    id: str | None = "app-local"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ReferenceOrigin":
        if data is None:
            return cls()
        origin = cls(
            kind=str(data.get("kind", "custom")),
            id=str(data["id"]) if data.get("id") is not None else None,
        )
        origin.validate()
        return origin

    def validate(self) -> None:
        if self.kind not in REFERENCE_ORIGIN_KINDS:
            raise ModelError(f"unsupported reference origin kind {self.kind}")
        if self.kind == "system_barycenter":
            if self.id not in {None, ""}:
                raise ModelError("system-barycenter origin must not have an id")
            return
        if self.id is None or not self.id.strip():
            raise ModelError(f"reference origin {self.kind} requires an id")
        if self.kind == "jpl" and not re.fullmatch(
            r"500@[-+A-Za-z0-9]+",
            self.id.strip(),
        ):
            raise ModelError("JPL reference origin id must use the 500@<id> form")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data: dict[str, Any] = {"kind": self.kind}
        if self.id:
            data["id"] = self.id
        return data


def _legacy_axes_id(reference_system: str, reference_plane: str, source: str) -> str:
    if source != "horizons":
        return "custom"
    system = reference_system.strip().casefold()
    plane = reference_plane.strip().casefold()
    if system == "icrf" and plane == "frame":
        return "icrf"
    if system == "icrf" and plane in {"ecliptic", "j2000 ecliptic"}:
        return "jpl_ecliptic_j2000"
    return "custom"


def _legacy_origin(source: str, center_id: str) -> ReferenceOrigin:
    cleaned = center_id.strip() or "app-local"
    if source == "horizons" or cleaned.startswith("500@"):
        return ReferenceOrigin("jpl", cleaned)
    if cleaned == "system-barycenter":
        return ReferenceOrigin("system_barycenter", None)
    if cleaned.startswith("body:"):
        return ReferenceOrigin("body", cleaned.removeprefix("body:"))
    if cleaned.startswith("group:"):
        return ReferenceOrigin("group_barycenter", cleaned.removeprefix("group:"))
    return ReferenceOrigin("custom", cleaned)


@dataclass
class SystemReferenceFrame:
    """Coordinate-frame metadata shared by every canonical body state."""

    epoch: str = ""
    time_scale: str = ""
    center_id: str = "app-local"
    reference_plane: str = "app-local XY"
    reference_system: str = "app-local"
    source: str = "app_local"
    axes_id: str = ""
    origin: ReferenceOrigin | None = None

    def __post_init__(self) -> None:
        if not self.axes_id:
            self.axes_id = _legacy_axes_id(
                self.reference_system,
                self.reference_plane,
                self.source,
            )
        if self.origin is None:
            self.origin = _legacy_origin(self.source, self.center_id)
        self._sync_legacy_metadata()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SystemReferenceFrame | None":
        if data is None:
            return None
        axes_id = str(data.get("axes_id", ""))
        origin_data = data.get("origin")
        reference_system = str(data.get("reference_system", "app-local"))
        reference_plane = str(data.get("reference_plane", "app-local XY"))
        source = str(data.get("source", "app_local"))
        center_id = str(data.get("center_id", "app-local"))
        frame = cls(
            epoch=str(data.get("epoch", "")),
            time_scale=str(data.get("time_scale", "")),
            center_id=center_id,
            reference_plane=str(data.get("custom_reference_plane", reference_plane)),
            reference_system=str(data.get("custom_reference_system", reference_system)),
            source=source,
            axes_id=axes_id,
            origin=(
                ReferenceOrigin.from_dict(origin_data)
                if isinstance(origin_data, dict)
                else _legacy_origin(source, center_id)
            ),
        )
        frame.validate()
        return frame

    def _sync_legacy_metadata(self) -> None:
        if self.axes_id not in REFERENCE_AXES:
            return
        if self.axes_id != "custom":
            self.reference_system, self.reference_plane = REFERENCE_AXES[self.axes_id]
        if self.origin is None:
            return
        if self.origin.kind == "jpl":
            self.center_id = self.origin.id or ""
            self.source = "horizons"
        elif self.origin.kind == "body":
            self.center_id = f"body:{self.origin.id}"
            self.source = "standard" if self.axes_id != "custom" else "app_local"
        elif self.origin.kind == "group_barycenter":
            self.center_id = f"group:{self.origin.id}"
            self.source = "standard" if self.axes_id != "custom" else "app_local"
        elif self.origin.kind == "system_barycenter":
            self.center_id = "system-barycenter"
            self.source = "standard" if self.axes_id != "custom" else "app_local"
        else:
            self.center_id = self.origin.id or "app-local"
            self.source = "standard" if self.axes_id != "custom" else "app_local"

    def validate(self) -> None:
        self._sync_legacy_metadata()
        if self.axes_id not in REFERENCE_AXES:
            raise ModelError(f"unsupported reference axes {self.axes_id}")
        if self.origin is None:
            raise ModelError("reference frame origin is required")
        self.origin.validate()
        if self.source not in REFERENCE_FRAME_SOURCES:
            raise ModelError(f"unsupported reference frame source {self.source}")
        for field_name in ("center_id", "reference_plane", "reference_system"):
            if not getattr(self, field_name).strip():
                raise ModelError(f"reference frame {field_name} is required")
        if self.axes_id != "custom":
            if not self.epoch.strip():
                raise ModelError("standard reference frame epoch is required")
            if self.time_scale not in REFERENCE_TIME_SCALES:
                raise ModelError(f"unsupported reference frame time scale {self.time_scale}")
        if self.origin.kind == "jpl" and self.time_scale not in REFERENCE_TIME_SCALES:
            raise ModelError("JPL reference frame requires a supported time scale")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = {
            "epoch": self.epoch,
            "time_scale": self.time_scale,
            "axes_id": self.axes_id,
            "origin": self.origin.to_dict(),
        }
        if self.axes_id == "custom":
            data["custom_reference_plane"] = self.reference_plane
            data["custom_reference_system"] = self.reference_system
        return data

    @property
    def horizons_compatible(self) -> bool:
        return bool(
            self.origin is not None
            and self.origin.kind == "jpl"
            and self.origin.id
            and self.axes_id
            in {"icrf", "fk5_j2000", "jpl_ecliptic_j2000", "galactic_iau1958"}
            and self.time_scale in REFERENCE_TIME_SCALES
        )


@dataclass
class Body:
    name: str
    kind: str
    mass_kg: float
    radius_m: float
    position_m: list[float]
    velocity_mps: list[float]
    color: str
    id: str = field(default_factory=lambda: str(uuid4()))
    parent_id: str | None = None
    visible: bool = True
    trail_enabled: bool = True
    orbit: OrbitData | None = None
    data_source: DataSource | None = None
    state_origin: str = "cartesian"
    flyby: FlybyData | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Body":
        orbit = OrbitData.from_dict(data.get("orbit"))
        data_source = DataSource.from_dict(data.get("data_source"))
        flyby = FlybyData.from_dict(data.get("flyby"))
        state_origin = data.get("state_origin")
        if state_origin is None:
            if flyby is not None:
                state_origin = "flyby"
            elif data_source is not None and data_source.source_name.casefold() == "jpl horizons":
                state_origin = "horizons"
            elif orbit is not None:
                state_origin = "orbital"
            else:
                state_origin = "cartesian"
        body = cls(
            id=str(data.get("id") or uuid4()),
            name=str(data["name"]),
            kind=str(data.get("kind", "body")),
            mass_kg=float(data["mass_kg"]),
            radius_m=float(data.get("radius_m", 1.0)),
            position_m=_vector3(data["position_m"], "position_m"),
            velocity_mps=_vector3(data["velocity_mps"], "velocity_mps"),
            color=str(data.get("color", "#ffffff")),
            parent_id=str(data["parent_id"]) if data.get("parent_id") is not None else None,
            visible=bool(data.get("visible", True)),
            trail_enabled=bool(data.get("trail_enabled", True)),
            orbit=orbit,
            data_source=data_source,
            state_origin=str(state_origin),
            flyby=flyby,
        )
        body.validate()
        return body

    def validate(self) -> None:
        if not self.id:
            raise ModelError("body id is required")
        if not self.name.strip():
            raise ModelError("body name is required")
        if self.kind not in BODY_KINDS:
            raise ModelError(f"{self.name} unsupported body kind {self.kind}")
        if self.mass_kg <= 0.0 or not math.isfinite(self.mass_kg):
            raise ModelError(f"{self.name} mass must be positive")
        if self.radius_m <= 0.0 or not math.isfinite(self.radius_m):
            raise ModelError(f"{self.name} radius must be positive")
        if self.state_origin not in STATE_ORIGINS:
            raise ModelError(f"{self.name} unsupported state_origin {self.state_origin}")
        if self.flyby is not None:
            self.flyby.validate()
            if self.state_origin != "flyby":
                raise ModelError(f"{self.name} flyby metadata requires flyby state_origin")
            if self.orbit is None or (self.orbit.eccentricity or 0.0) <= 1.0:
                raise ModelError(f"{self.name} flyby requires hyperbolic orbit metadata")
        elif self.state_origin == "flyby":
            raise ModelError(f"{self.name} flyby state_origin requires flyby metadata")
        self.position_m = _vector3(self.position_m, "position_m")
        self.velocity_mps = _vector3(self.velocity_mps, "velocity_mps")
        if self.orbit is not None:
            self.orbit.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "mass_kg": self.mass_kg,
            "radius_m": self.radius_m,
            "position_m": self.position_m,
            "velocity_mps": self.velocity_mps,
            "color": self.color,
            "visible": self.visible,
            "trail_enabled": self.trail_enabled,
            "state_origin": self.state_origin,
        }
        if self.parent_id is not None:
            data["parent_id"] = self.parent_id
        if self.orbit is not None:
            data["orbit"] = self.orbit.to_dict()
        if self.data_source is not None:
            source_data = self.data_source.to_dict()
            if source_data:
                data["data_source"] = source_data
        if self.flyby is not None:
            data["flyby"] = self.flyby.to_dict()
        return data


@dataclass
class SystemGroup:
    name: str
    kind: str
    body_ids: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))
    parent_group_id: str | None = None
    orbit: OrbitData | None = None
    data_source: DataSource | None = None
    orbit_target_type: str | None = None
    orbit_target_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SystemGroup":
        group = cls(
            id=str(data.get("id") or uuid4()),
            name=str(data["name"]),
            kind=str(data.get("kind", "system")),
            body_ids=[str(body_id) for body_id in data.get("body_ids", [])],
            parent_group_id=str(data["parent_group_id"]) if data.get("parent_group_id") is not None else None,
            orbit=OrbitData.from_dict(data.get("orbit")),
            data_source=DataSource.from_dict(data.get("data_source")),
            orbit_target_type=str(data["orbit_target_type"]) if data.get("orbit_target_type") is not None else None,
            orbit_target_id=str(data["orbit_target_id"]) if data.get("orbit_target_id") is not None else None,
        )
        group.validate()
        return group

    def validate(self) -> None:
        if not self.id:
            raise ModelError("group id is required")
        if not self.name.strip():
            raise ModelError("group name is required")
        if not self.kind.strip():
            raise ModelError(f"{self.name} group kind is required")
        if self.orbit is not None:
            self.orbit.validate()
        if self.orbit_target_type is not None and self.orbit_target_type not in {"body", "group"}:
            raise ModelError(f"{self.name} unsupported orbit_target_type {self.orbit_target_type}")
        if (self.orbit_target_type is None) != (self.orbit_target_id is None):
            raise ModelError(f"{self.name} group orbit target requires type and id")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "body_ids": self.body_ids,
        }
        if self.parent_group_id is not None:
            data["parent_group_id"] = self.parent_group_id
        if self.orbit is not None:
            data["orbit"] = self.orbit.to_dict()
        if self.data_source is not None:
            source_data = self.data_source.to_dict()
            if source_data:
                data["data_source"] = source_data
        if self.orbit_target_type is not None and self.orbit_target_id is not None:
            data["orbit_target_type"] = self.orbit_target_type
            data["orbit_target_id"] = self.orbit_target_id
        return data


@dataclass
class SystemSettings:
    visible_step_s: float = DAY
    accuracy_profile: str = "balanced"
    physics_mode: str = "post_newtonian"
    integrator: str = "velocity_verlet"
    distance_unit: str = "AU"
    view_mode: str = "fit_system"
    simulation_scope: str = "auto"
    trail_sample_interval_s: float = DAY
    trail_frame: str = "focused_parent"
    orbit_visibility: str = "all"
    trail_visibility: str = "all"
    path_style: str = "subtle"

    @classmethod
    def default_for_system(cls, system_id: str) -> "SystemSettings":
        if system_id == "builtin-dwarf-planets":
            return cls(visible_step_s=90.0 * DAY, trail_sample_interval_s=90.0 * DAY)
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, system_id: str) -> "SystemSettings":
        defaults = cls.default_for_system(system_id)
        if data is None:
            return defaults
        settings = cls(
            visible_step_s=float(data.get("visible_step_s", defaults.visible_step_s)),
            accuracy_profile=str(data.get("accuracy_profile", defaults.accuracy_profile)),
            physics_mode=str(data.get("physics_mode", defaults.physics_mode)),
            integrator=str(data.get("integrator", defaults.integrator)),
            distance_unit=str(data.get("distance_unit", defaults.distance_unit)),
            view_mode=str(data.get("view_mode", defaults.view_mode)),
            simulation_scope=str(data.get("simulation_scope", defaults.simulation_scope)),
            trail_sample_interval_s=float(
                data.get("trail_sample_interval_s", defaults.trail_sample_interval_s)
            ),
            trail_frame=str(data.get("trail_frame", defaults.trail_frame)),
            orbit_visibility=str(
                data.get("orbit_visibility", defaults.orbit_visibility)
            ),
            trail_visibility=str(
                data.get("trail_visibility", defaults.trail_visibility)
            ),
            path_style=str(data.get("path_style", defaults.path_style)),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.visible_step_s <= 0.0:
            raise ModelError("visible_step_s must be positive")
        if self.trail_sample_interval_s <= 0.0:
            raise ModelError("trail_sample_interval_s must be positive")
        if self.accuracy_profile not in ACCURACY_PROFILES:
            raise ModelError(f"unsupported accuracy_profile {self.accuracy_profile}")
        if self.physics_mode not in PHYSICS_MODES:
            raise ModelError(f"unsupported physics_mode {self.physics_mode}")
        if self.integrator not in INTEGRATORS:
            raise ModelError(f"unsupported integrator {self.integrator}")
        if self.distance_unit not in DISTANCE_UNITS:
            raise ModelError(f"unsupported distance_unit {self.distance_unit}")
        if self.view_mode not in VIEW_MODES:
            raise ModelError(f"unsupported view_mode {self.view_mode}")
        if self.simulation_scope not in SIMULATION_SCOPES:
            raise ModelError(f"unsupported simulation_scope {self.simulation_scope}")
        if self.trail_frame not in TRAIL_FRAMES:
            raise ModelError(f"unsupported trail_frame {self.trail_frame}")
        if self.orbit_visibility not in ORBIT_VISIBILITY_MODES:
            raise ModelError(f"unsupported orbit_visibility {self.orbit_visibility}")
        if self.trail_visibility not in TRAIL_VISIBILITY_MODES:
            raise ModelError(f"unsupported trail_visibility {self.trail_visibility}")
        if self.path_style not in PATH_STYLES:
            raise ModelError(f"unsupported path_style {self.path_style}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "visible_step_s": self.visible_step_s,
            "accuracy_profile": self.accuracy_profile,
            "physics_mode": self.physics_mode,
            "integrator": self.integrator,
            "distance_unit": self.distance_unit,
            "view_mode": self.view_mode,
            "simulation_scope": self.simulation_scope,
            "trail_sample_interval_s": self.trail_sample_interval_s,
            "trail_frame": self.trail_frame,
            "orbit_visibility": self.orbit_visibility,
            "trail_visibility": self.trail_visibility,
            "path_style": self.path_style,
        }


@dataclass
class SolarSystem:
    name: str
    epoch: str
    bodies: list[Body]
    description: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = SCHEMA_VERSION
    settings: SystemSettings = field(default_factory=SystemSettings)
    groups: list[SystemGroup] = field(default_factory=list)
    reference_frame: SystemReferenceFrame | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SolarSystem":
        version = int(data.get("schema_version", 0))
        if version not in range(1, SCHEMA_VERSION + 1):
            raise ModelError(f"unsupported schema version {version}")
        system_id = str(data.get("id") or uuid4())
        bodies = [Body.from_dict(item) for item in data.get("bodies", [])]
        if version == 1:
            cls._migrate_v1_parent_ids(bodies)
        groups = (
            [SystemGroup.from_dict(item) for item in data["groups"]]
            if version >= 5 and "groups" in data
            else cls._default_groups(system_id, str(data["name"]), bodies)
        )
        reference_frame = SystemReferenceFrame.from_dict(data.get("reference_frame"))
        if reference_frame is None:
            reference_frame = cls._legacy_reference_frame(
                system_id,
                str(data.get("epoch", "")),
            )
        if reference_frame.source == "horizons":
            for item, body in zip(data.get("bodies", []), bodies):
                if "state_origin" not in item and body.flyby is None:
                    body.state_origin = "horizons"
        system = cls(
            id=system_id,
            schema_version=SCHEMA_VERSION,
            name=str(data["name"]),
            epoch=str(data.get("epoch", "")),
            description=str(data.get("description", "")),
            bodies=bodies,
            settings=SystemSettings.from_dict(data.get("settings"), system_id),
            groups=groups,
            reference_frame=reference_frame,
        )
        system.validate()
        return system

    @staticmethod
    def _migrate_v1_parent_ids(bodies: list[Body]) -> None:
        first_star = next((body for body in bodies if body.kind == "star"), None)
        if first_star is None:
            return
        for body in bodies:
            if body.kind != "star" and body.parent_id is None:
                body.parent_id = first_star.id

    @staticmethod
    def _default_groups(system_id: str, name: str, bodies: list[Body]) -> list[SystemGroup]:
        root_stars = [body for body in bodies if body.kind == "star" and body.parent_id is None]
        if root_stars:
            kind = "planetary_system" if len(root_stars) == 1 else "stellar_system"
            return [
                SystemGroup(
                    id=f"{system_id}-root-group",
                    name=name,
                    kind=kind,
                    body_ids=[body.id for body in root_stars],
                )
            ]
        root_bodies = [body for body in bodies if body.parent_id is None]
        return [
            SystemGroup(
                id=f"{system_id}-root-group",
                name=name,
                kind="system",
                body_ids=[body.id for body in root_bodies],
            )
        ]

    @staticmethod
    def _legacy_reference_frame(system_id: str, epoch_label: str) -> SystemReferenceFrame:
        if system_id in {"builtin-solar-system", "builtin-dwarf-planets"}:
            epoch = epoch_label.split(" TDB", 1)[0].strip()
            return SystemReferenceFrame(
                epoch=epoch,
                time_scale="TDB",
                center_id="500@0",
                reference_plane="ECLIPTIC",
                reference_system="ICRF",
                source="horizons",
            )
        return SystemReferenceFrame(epoch=epoch_label)

    def validate(self) -> None:
        if not self.id:
            raise ModelError("system id is required")
        if not self.name.strip():
            raise ModelError("system name is required")
        if not self.bodies:
            raise ModelError("system must contain at least one body")
        self.settings.validate()
        if self.reference_frame is not None:
            self.reference_frame.validate()
        seen_ids: set[str] = set()
        for body in self.bodies:
            body.validate()
            if body.id in seen_ids:
                raise ModelError(f"duplicate body id {body.id}")
            seen_ids.add(body.id)
        for body in self.bodies:
            if body.parent_id is None:
                if body.kind == "moon":
                    raise ModelError(f"{body.name} moon requires a parent planet or dwarf planet")
                continue
            if body.parent_id == body.id:
                raise ModelError(f"{body.name} cannot parent itself")
            if body.parent_id not in seen_ids:
                raise ModelError(f"{body.name} parent_id {body.parent_id} does not exist")
        self._validate_parent_cycles()
        self._validate_body_parent_kinds()
        self._validate_flybys(seen_ids)
        self._validate_groups(seen_ids)
        self._validate_group_orbit_targets(seen_ids)
        self._validate_reference_origin(seen_ids)

    def _validate_reference_origin(self, body_ids: set[str]) -> None:
        frame = self.reference_frame
        if frame is None or frame.origin is None:
            return
        if frame.origin.kind == "body" and frame.origin.id not in body_ids:
            raise ModelError(f"reference-frame body origin {frame.origin.id} does not exist")
        if frame.origin.kind == "group_barycenter":
            group_ids = {group.id for group in self.groups}
            if frame.origin.id not in group_ids:
                raise ModelError(f"reference-frame group origin {frame.origin.id} does not exist")

    def _validate_body_parent_kinds(self) -> None:
        bodies_by_id = {body.id: body for body in self.bodies}
        for body in self.bodies:
            if body.parent_id is None:
                continue
            parent = bodies_by_id[body.parent_id]
            if body.kind == "star":
                raise ModelError(f"{body.name} star must be a root body")
            if body.kind == "moon":
                if parent.kind not in {"planet", "dwarf planet"}:
                    raise ModelError(f"{body.name} moon parent must be a planet or dwarf planet")
                continue
            if parent.kind != "star":
                raise ModelError(f"{body.name} {body.kind} parent must be a star")

    def _validate_flybys(self, body_ids: set[str]) -> None:
        for body in self.bodies:
            if body.flyby is None:
                continue
            if body.parent_id is not None:
                raise ModelError(f"{body.name} flyby must be an unparented body")
            if body.kind == "moon":
                raise ModelError(f"{body.name} moon cannot be a flyby body")
            anchor_id = body.flyby.anchor_body_id
            if anchor_id == body.id:
                raise ModelError(f"{body.name} cannot use itself as its flyby anchor")
            if anchor_id not in body_ids:
                raise ModelError(f"{body.name} flyby anchor {anchor_id} does not exist")

    def _validate_parent_cycles(self) -> None:
        bodies_by_id = {body.id: body for body in self.bodies}
        for body in self.bodies:
            visited: set[str] = set()
            parent_id = body.parent_id
            while parent_id is not None:
                if parent_id in visited:
                    raise ModelError(f"parent cycle involving {body.name}")
                visited.add(parent_id)
                parent = bodies_by_id[parent_id]
                parent_id = parent.parent_id

    def _validate_groups(self, body_ids: set[str]) -> None:
        seen_group_ids: set[str] = set()
        direct_body_owners: dict[str, str] = {}
        for group in self.groups:
            group.validate()
            if group.id in seen_group_ids:
                raise ModelError(f"duplicate group id {group.id}")
            seen_group_ids.add(group.id)
            seen_body_ids: set[str] = set()
            for body_id in group.body_ids:
                if body_id not in body_ids:
                    raise ModelError(f"{group.name} group body_id {body_id} does not exist")
                if body_id in seen_body_ids:
                    raise ModelError(f"{group.name} group contains duplicate body_id {body_id}")
                seen_body_ids.add(body_id)
                existing_owner = direct_body_owners.get(body_id)
                if existing_owner is not None:
                    raise ModelError(
                        f"body_id {body_id} belongs directly to both {existing_owner} and {group.name}"
                    )
                direct_body_owners[body_id] = group.name
        for group in self.groups:
            if group.parent_group_id is None:
                continue
            if group.parent_group_id == group.id:
                raise ModelError(f"{group.name} group cannot parent itself")
            if group.parent_group_id not in seen_group_ids:
                raise ModelError(f"{group.name} parent_group_id {group.parent_group_id} does not exist")
        self._validate_group_cycles()

    def _validate_group_cycles(self) -> None:
        groups_by_id = {group.id: group for group in self.groups}
        for group in self.groups:
            visited: set[str] = set()
            parent_group_id = group.parent_group_id
            while parent_group_id is not None:
                if parent_group_id in visited:
                    raise ModelError(f"group cycle involving {group.name}")
                visited.add(parent_group_id)
                parent = groups_by_id[parent_group_id]
                parent_group_id = parent.parent_group_id

    def _validate_group_orbit_targets(self, body_ids: set[str]) -> None:
        group_ids = {group.id for group in self.groups}
        descendant_group_ids_by_id = {
            group.id: self._descendant_group_ids(group.id)
            for group in self.groups
        }
        for group in self.groups:
            if group.orbit_target_type is None and group.orbit_target_id is None:
                continue
            if group.orbit_target_type == "body":
                if group.orbit_target_id not in body_ids:
                    raise ModelError(f"{group.name} orbit_target_id {group.orbit_target_id} does not exist")
                if group.orbit_target_id in self._body_ids_for_group(group.id):
                    raise ModelError(f"{group.name} cannot orbit a body inside itself")
            elif group.orbit_target_type == "group":
                if group.orbit_target_id not in group_ids:
                    raise ModelError(f"{group.name} orbit_target_id {group.orbit_target_id} does not exist")
                if group.orbit_target_id == group.id:
                    raise ModelError(f"{group.name} cannot orbit itself")
                if group.orbit_target_id in descendant_group_ids_by_id[group.id]:
                    raise ModelError(f"{group.name} cannot orbit a descendant group")
                if self._body_ids_for_group(group.id) & self._body_ids_for_group(group.orbit_target_id):
                    raise ModelError(f"{group.name} cannot orbit an overlapping group")

    def _descendant_group_ids(self, group_id: str) -> set[str]:
        descendants: set[str] = set()
        changed = True
        while changed:
            changed = False
            for group in self.groups:
                if group.parent_group_id in descendants | {group_id} and group.id not in descendants:
                    descendants.add(group.id)
                    changed = True
        return descendants

    def _body_ids_for_group(self, group_id: str) -> set[str]:
        group_ids = self._descendant_group_ids(group_id) | {group_id}
        body_ids = {
            body_id
            for group in self.groups
            if group.id in group_ids
            for body_id in group.body_ids
        }
        changed = True
        while changed:
            changed = False
            for body in self.bodies:
                if body.parent_id in body_ids and body.id not in body_ids:
                    body_ids.add(body.id)
                    changed = True
        return body_ids

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "epoch": self.epoch,
            "description": self.description,
            "settings": self.settings.to_dict(),
            "groups": [group.to_dict() for group in self.groups],
            "bodies": [body.to_dict() for body in self.bodies],
        }
        if self.reference_frame is not None:
            data["reference_frame"] = self.reference_frame.to_dict()
        return data

    def duplicate(self, name: str | None = None) -> "SolarSystem":
        data = self.to_dict()
        data["id"] = str(uuid4())
        data["name"] = name or f"{self.name} Copy"
        body_id_map = {
            body["id"]: str(uuid4())
            for body in data["bodies"]
        }
        for body in data["bodies"]:
            old_id = body["id"]
            body["id"] = body_id_map[old_id]
            if body.get("parent_id") is not None:
                body["parent_id"] = body_id_map[body["parent_id"]]
            if body.get("flyby") is not None:
                body["flyby"]["anchor_body_id"] = body_id_map[
                    body["flyby"]["anchor_body_id"]
                ]
        group_id_map = {
            group["id"]: str(uuid4())
            for group in data.get("groups", [])
        }
        for group in data.get("groups", []):
            old_id = group["id"]
            group["id"] = group_id_map[old_id]
            if group.get("parent_group_id") is not None:
                group["parent_group_id"] = group_id_map[group["parent_group_id"]]
            if group.get("orbit_target_type") == "body" and group.get("orbit_target_id") is not None:
                group["orbit_target_id"] = body_id_map[group["orbit_target_id"]]
            if group.get("orbit_target_type") == "group" and group.get("orbit_target_id") is not None:
                group["orbit_target_id"] = group_id_map[group["orbit_target_id"]]
            group["body_ids"] = [
                body_id_map[body_id]
                for body_id in group.get("body_ids", [])
            ]
        origin = data.get("reference_frame", {}).get("origin")
        if origin is not None and origin.get("kind") == "body":
            origin["id"] = body_id_map[origin["id"]]
        if origin is not None and origin.get("kind") == "group_barycenter":
            origin["id"] = group_id_map[origin["id"]]
        return SolarSystem.from_dict(data)
