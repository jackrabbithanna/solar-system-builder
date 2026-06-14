# models.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Schema-versioned domain models for solar systems."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .constants import DAY

SCHEMA_VERSION = 5

ACCURACY_PROFILES = {"high", "balanced", "fast"}
DISTANCE_UNITS = {"km", "AU", "kAU", "ly"}
VIEW_MODES = {"fit_system", "follow_selected", "log_overview"}
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


def _vector3(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ModelError(f"{field_name} must be a 3-value vector")
    vector = [float(component) for component in value]
    if not all(component == component for component in vector):
        raise ModelError(f"{field_name} cannot contain NaN")
    return vector


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Body":
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
        )
        body.validate()
        return body

    def validate(self) -> None:
        if not self.id:
            raise ModelError("body id is required")
        if not self.name.strip():
            raise ModelError("body name is required")
        if self.mass_kg <= 0.0:
            raise ModelError(f"{self.name} mass must be positive")
        if self.radius_m <= 0.0:
            raise ModelError(f"{self.name} radius must be positive")
        self.position_m = _vector3(self.position_m, "position_m")
        self.velocity_mps = _vector3(self.velocity_mps, "velocity_mps")

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
        }
        if self.parent_id is not None:
            data["parent_id"] = self.parent_id
        return data


@dataclass
class SystemGroup:
    name: str
    kind: str
    body_ids: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid4()))
    parent_group_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SystemGroup":
        group = cls(
            id=str(data.get("id") or uuid4()),
            name=str(data["name"]),
            kind=str(data.get("kind", "system")),
            body_ids=[str(body_id) for body_id in data.get("body_ids", [])],
            parent_group_id=str(data["parent_group_id"]) if data.get("parent_group_id") is not None else None,
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
        return data


@dataclass
class SystemSettings:
    visible_step_s: float = DAY
    accuracy_profile: str = "balanced"
    distance_unit: str = "AU"
    view_mode: str = "fit_system"
    simulation_scope: str = "auto"
    trail_sample_interval_s: float = DAY

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
            distance_unit=str(data.get("distance_unit", defaults.distance_unit)),
            view_mode=str(data.get("view_mode", defaults.view_mode)),
            simulation_scope=str(data.get("simulation_scope", defaults.simulation_scope)),
            trail_sample_interval_s=float(
                data.get("trail_sample_interval_s", defaults.trail_sample_interval_s)
            ),
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
        if self.distance_unit not in DISTANCE_UNITS:
            raise ModelError(f"unsupported distance_unit {self.distance_unit}")
        if self.view_mode not in VIEW_MODES:
            raise ModelError(f"unsupported view_mode {self.view_mode}")
        if self.simulation_scope not in SIMULATION_SCOPES:
            raise ModelError(f"unsupported simulation_scope {self.simulation_scope}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "visible_step_s": self.visible_step_s,
            "accuracy_profile": self.accuracy_profile,
            "distance_unit": self.distance_unit,
            "view_mode": self.view_mode,
            "simulation_scope": self.simulation_scope,
            "trail_sample_interval_s": self.trail_sample_interval_s,
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SolarSystem":
        version = int(data.get("schema_version", 0))
        if version not in (1, 2, 3, 4, SCHEMA_VERSION):
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
        system = cls(
            id=system_id,
            schema_version=SCHEMA_VERSION,
            name=str(data["name"]),
            epoch=str(data.get("epoch", "")),
            description=str(data.get("description", "")),
            bodies=bodies,
            settings=SystemSettings.from_dict(data.get("settings"), system_id),
            groups=groups,
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

    def validate(self) -> None:
        if not self.id:
            raise ModelError("system id is required")
        if not self.name.strip():
            raise ModelError("system name is required")
        if not self.bodies:
            raise ModelError("system must contain at least one body")
        self.settings.validate()
        seen_ids: set[str] = set()
        for body in self.bodies:
            body.validate()
            if body.id in seen_ids:
                raise ModelError(f"duplicate body id {body.id}")
            seen_ids.add(body.id)
        for body in self.bodies:
            if body.parent_id is None:
                continue
            if body.parent_id == body.id:
                raise ModelError(f"{body.name} cannot parent itself")
            if body.parent_id not in seen_ids:
                raise ModelError(f"{body.name} parent_id {body.parent_id} does not exist")
        self._validate_parent_cycles()
        self._validate_groups(seen_ids)

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

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "epoch": self.epoch,
            "description": self.description,
            "settings": self.settings.to_dict(),
            "groups": [group.to_dict() for group in self.groups],
            "bodies": [body.to_dict() for body in self.bodies],
        }

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
        group_id_map = {
            group["id"]: str(uuid4())
            for group in data.get("groups", [])
        }
        for group in data.get("groups", []):
            old_id = group["id"]
            group["id"] = group_id_map[old_id]
            if group.get("parent_group_id") is not None:
                group["parent_group_id"] = group_id_map[group["parent_group_id"]]
            group["body_ids"] = [
                body_id_map[body_id]
                for body_id in group.get("body_ids", [])
            ]
        return SolarSystem.from_dict(data)
