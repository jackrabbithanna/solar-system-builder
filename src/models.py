# models.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Schema-versioned domain models for solar systems."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 2


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
class SolarSystem:
    name: str
    epoch: str
    bodies: list[Body]
    description: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SolarSystem":
        version = int(data.get("schema_version", 0))
        if version not in (1, SCHEMA_VERSION):
            raise ModelError(f"unsupported schema version {version}")
        bodies = [Body.from_dict(item) for item in data.get("bodies", [])]
        if version == 1:
            cls._migrate_v1_parent_ids(bodies)
        system = cls(
            id=str(data.get("id") or uuid4()),
            schema_version=SCHEMA_VERSION,
            name=str(data["name"]),
            epoch=str(data.get("epoch", "")),
            description=str(data.get("description", "")),
            bodies=bodies,
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

    def validate(self) -> None:
        if not self.id:
            raise ModelError("system id is required")
        if not self.name.strip():
            raise ModelError("system name is required")
        if not self.bodies:
            raise ModelError("system must contain at least one body")
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

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "epoch": self.epoch,
            "description": self.description,
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
        return SolarSystem.from_dict(data)
