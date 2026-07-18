# system_editing.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free structural editing helpers for solar systems."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from .constants import AU, G, SOLAR_MASS
from .models import (
    BODY_KINDS,
    Body,
    DataSource,
    ModelError,
    SolarSystem,
    SystemGroup,
    SystemReferenceFrame,
    SystemSettings,
)

EARTH_MASS_KG = 5.9722e24
EARTH_RADIUS_M = 6_371_000.0
MOON_MASS_KG = 7.342e22
MOON_RADIUS_M = 1_737_400.0
SOLAR_RADIUS_M = 695_700_000.0

DEFAULT_ORBIT_RADIUS_M = {
    "planet": AU,
    "dwarf planet": 39.0 * AU,
    "moon": 384_400_000.0,
    "comet": 5.0 * AU,
    "asteroid": 2.7 * AU,
}

BODY_DEFAULTS = {
    "star": (SOLAR_MASS, SOLAR_RADIUS_M, "#ffd166"),
    "planet": (EARTH_MASS_KG, EARTH_RADIUS_M, "#4dabf7"),
    "dwarf planet": (0.0022 * EARTH_MASS_KG, 1_188_300.0, "#cdb4db"),
    "moon": (MOON_MASS_KG, MOON_RADIUS_M, "#d8dee9"),
    "comet": (1.0e14, 5_000.0, "#80ffdb"),
    "asteroid": (1.0e16, 10_000.0, "#adb5bd"),
}


@dataclass(frozen=True)
class BodyStateInput:
    name: str
    kind: str
    mass_kg: float
    radius_m: float
    position_m: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]
    color: str
    parent_id: str | None = None
    group_id: str | None = None
    visible: bool = True
    trail_enabled: bool = True


def default_body_state(name: str, kind: str) -> BodyStateInput:
    if kind not in BODY_DEFAULTS:
        raise ModelError(f"unsupported body kind {kind}")
    mass_kg, radius_m, color = BODY_DEFAULTS[kind]
    return BodyStateInput(
        name=name,
        kind=kind,
        mass_kg=mass_kg,
        radius_m=radius_m,
        position_m=(0.0, 0.0, 0.0),
        velocity_mps=(0.0, 0.0, 0.0),
        color=color,
    )


def create_system(
    name: str,
    starter: str = "single_star",
    *,
    description: str | None = None,
    epoch: str | None = None,
    primary_state: BodyStateInput | None = None,
    secondary_state: BodyStateInput | None = None,
) -> SolarSystem:
    system_name = _required_name(name, "system name")
    system_id = str(uuid4())
    epoch = (epoch or "Custom seed").strip()
    root_group = SystemGroup(
        id=str(uuid4()),
        name=system_name,
        kind="stellar_system",
    )
    if starter == "single_star":
        star = _body_from_state(primary_state or default_body_state("Primary Star", "star"))
        if star.kind != "star" or star.parent_id is not None:
            raise ModelError("single-star starter requires a root star")
        root_group.body_ids = [star.id]
        bodies = [star]
    elif starter == "binary_star":
        if primary_state is None and secondary_state is None:
            primary, secondary = _binary_stars()
        elif primary_state is not None and secondary_state is not None:
            primary = _body_from_state(primary_state)
            secondary = _body_from_state(secondary_state)
            if any(body.kind != "star" or body.parent_id is not None for body in (primary, secondary)):
                raise ModelError("binary-star starter requires two root stars")
        else:
            raise ModelError("binary-star starter requires both star states")
        root_group.body_ids = [primary.id, secondary.id]
        bodies = [primary, secondary]
    elif starter == "hierarchical":
        star = _body_from_state(primary_state or default_body_state("Primary Star", "star"))
        if star.kind != "star" or star.parent_id is not None:
            raise ModelError("hierarchical starter requires a root star")
        root_group.kind = "hierarchical_system"
        root_group.body_ids = [star.id]
        bodies = [star]
    elif starter == "sol":
        sun_state = primary_state or BodyStateInput(
            name="Sun",
            kind="star",
            mass_kg=SOLAR_MASS,
            radius_m=SOLAR_RADIUS_M,
            position_m=(0.0, 0.0, 0.0),
            velocity_mps=(0.0, 0.0, 0.0),
            color="#ffd166",
        )
        sun = _body_from_state(sun_state)
        if sun.kind != "star" or sun.parent_id is not None:
            raise ModelError("Sol starter requires a root star")
        sun.data_source = DataSource(
            source_name="JPL Horizons",
            source_url="https://ssd.jpl.nasa.gov/horizons/",
            catalog_id="10",
            citation="JPL Horizons target identifier; local standard physical constants.",
        )
        root_group.kind = "planetary_system"
        root_group.body_ids = [sun.id]
        bodies = [sun]
        if epoch == "Custom seed":
            epoch = f"{date.today().isoformat()} 00:00:00"
    else:
        raise ModelError(f"unsupported system starter {starter}")
    reference_frame = (
        SystemReferenceFrame(
            epoch=epoch,
            time_scale="TDB",
            center_id="500@10",
            reference_plane="ECLIPTIC",
            reference_system="ICRF",
            source="horizons",
        )
        if starter == "sol"
        else SystemReferenceFrame(epoch=epoch)
    )
    system = SolarSystem(
        id=system_id,
        name=system_name,
        epoch=(f"{epoch} TDB, heliocentric JPL-compatible frame" if starter == "sol" else epoch),
        description=description.strip() if description is not None else "User-created system.",
        bodies=bodies,
        groups=[root_group],
        settings=SystemSettings(),
        reference_frame=reference_frame,
    )
    system.validate()
    return system


def add_body_from_state(system: SolarSystem, state: BodyStateInput) -> Body:
    body = _body_from_state(state)
    if body.kind == "star":
        if body.parent_id is not None:
            raise ModelError("star must be a root body")
        _place_root_star_if_unset(system, body)
    else:
        parent = _body_by_id(system, body.parent_id, "parent body")
        _validate_parent_kind(body.kind, parent)
    group = None
    if state.group_id is not None:
        group = _group_by_id(system, state.group_id)
        if group is None:
            raise ModelError("group does not exist")
        if body.kind != "star":
            raise ModelError("non-root bodies inherit group membership from their parent")
    system.bodies.append(body)
    if group is not None:
        group.body_ids.append(body.id)
    try:
        system.validate()
    except Exception:
        system.bodies.remove(body)
        if group is not None and body.id in group.body_ids:
            group.body_ids.remove(body.id)
        raise
    return body


def update_body_from_state(system: SolarSystem, body_id: str, state: BodyStateInput) -> Body:
    body = _body_by_id(system, body_id, "body")
    replacement = _body_from_state(state, body_id=body.id)
    replacement.orbit = body.orbit
    replacement.data_source = body.data_source
    replacement.state_origin = body.state_origin
    if replacement.kind == "star" and replacement.parent_id is not None:
        raise ModelError("star must be a root body")
    if replacement.kind != "star":
        parent = _body_by_id(system, replacement.parent_id, "parent body")
        _validate_parent_kind(replacement.kind, parent)
    candidate = SolarSystem.from_dict(system.to_dict())
    candidate_index = next(index for index, item in enumerate(candidate.bodies) if item.id == body_id)
    candidate.bodies[candidate_index] = replacement
    existing_group_id = next(
        (group.id for group in system.groups if body_id in group.body_ids),
        None,
    )
    target_group_id = state.group_id if state.group_id is not None else existing_group_id
    for group in candidate.groups:
        group.body_ids = [owned_id for owned_id in group.body_ids if owned_id != body_id]
    if replacement.kind == "star" and target_group_id is not None:
        target_group = _group_by_id(candidate, target_group_id)
        if target_group is None:
            raise ModelError("group does not exist")
        target_group.body_ids.append(body_id)
    candidate.validate()
    index = system.bodies.index(body)
    system.bodies[index] = replacement
    for group in system.groups:
        group.body_ids = [owned_id for owned_id in group.body_ids if owned_id != body_id]
    if replacement.kind == "star" and target_group_id is not None:
        target_group = _group_by_id(system, target_group_id)
        if target_group is not None:
            target_group.body_ids.append(body_id)
    system.validate()
    return replacement


def add_star_system(
    system: SolarSystem,
    name: str,
    parent_group_id: str | None = None,
) -> tuple[SystemGroup, Body]:
    group_name = _required_name(name, "star system name")
    if parent_group_id is not None and not _group_by_id(system, parent_group_id):
        raise ModelError("parent group does not exist")
    star = _new_body(f"{group_name} Star", "star")
    group = SystemGroup(
        id=str(uuid4()),
        name=group_name,
        kind="stellar_system",
        body_ids=[star.id],
        parent_group_id=parent_group_id,
    )
    system.groups.append(group)
    system.bodies.append(star)
    system.validate()
    return group, star


def add_body(
    system: SolarSystem,
    kind: str,
    name: str,
    parent_id: str | None = None,
    group_id: str | None = None,
    orbit_radius_m: float | None = None,
) -> Body:
    body_name = _required_name(name, "body name")
    body = _new_body(body_name, kind)
    if kind == "star":
        body.parent_id = None
        _place_root_star(system, body)
    else:
        parent = _body_by_id(system, parent_id, "parent body")
        _validate_parent_kind(kind, parent)
        radius_m = orbit_radius_m if orbit_radius_m is not None else DEFAULT_ORBIT_RADIUS_M[kind]
        _set_circular_orbit(body, parent, radius_m)
        body.parent_id = parent.id
    if group_id is not None:
        group = _group_by_id(system, group_id)
        if group is None:
            raise ModelError("group does not exist")
        if kind == "star":
            group.body_ids.append(body.id)
    system.bodies.append(body)
    system.validate()
    return body


def delete_body_cascade(system: SolarSystem, body_id: str) -> list[str]:
    if _body_by_id(system, body_id, "body") is None:
        raise ModelError("body does not exist")
    deleted_ids = _body_descendant_ids(system.bodies, body_id)
    _remove_body_ids(system, deleted_ids)
    return sorted(deleted_ids)


def delete_group_cascade(system: SolarSystem, group_id: str) -> tuple[list[str], list[str]]:
    group = _group_by_id(system, group_id)
    if group is None:
        raise ModelError("group does not exist")
    deleted_group_ids = _group_descendant_ids(system.groups, group_id)
    deleted_body_ids: set[str] = set()
    for candidate in system.groups:
        if candidate.id in deleted_group_ids:
            for body_id in candidate.body_ids:
                deleted_body_ids.update(_body_descendant_ids(system.bodies, body_id))
    if len(deleted_body_ids) >= len(system.bodies):
        raise ModelError("system must contain at least one body")
    system.groups = [group for group in system.groups if group.id not in deleted_group_ids]
    _remove_body_ids(system, deleted_body_ids)
    _clean_group_orbit_targets(system)
    system.validate()
    return sorted(deleted_group_ids), sorted(deleted_body_ids)


def deletion_summary_for_body(system: SolarSystem, body_id: str) -> tuple[list[str], list[str]]:
    body_ids = _body_descendant_ids(system.bodies, body_id)
    return _names_for_body_ids(system, body_ids), []


def deletion_summary_for_group(system: SolarSystem, group_id: str) -> tuple[list[str], list[str]]:
    group_ids = _group_descendant_ids(system.groups, group_id)
    body_ids: set[str] = set()
    for group in system.groups:
        if group.id in group_ids:
            for body_id in group.body_ids:
                body_ids.update(_body_descendant_ids(system.bodies, body_id))
    return _names_for_body_ids(system, body_ids), _names_for_group_ids(system, group_ids)


def _new_body(name: str, kind: str) -> Body:
    if kind not in BODY_DEFAULTS:
        raise ModelError(f"unsupported body kind {kind}")
    mass_kg, radius_m, color = BODY_DEFAULTS[kind]
    return Body(
        name=name,
        kind=kind,
        mass_kg=mass_kg,
        radius_m=radius_m,
        position_m=[0.0, 0.0, 0.0],
        velocity_mps=[0.0, 0.0, 0.0],
        color=color,
    )


def _body_from_state(state: BodyStateInput, body_id: str | None = None) -> Body:
    if state.kind not in BODY_KINDS:
        raise ModelError(f"unsupported body kind {state.kind}")
    body = Body(
        id=body_id or str(uuid4()),
        name=_required_name(state.name, "body name"),
        kind=state.kind,
        mass_kg=float(state.mass_kg),
        radius_m=float(state.radius_m),
        position_m=[float(value) for value in state.position_m],
        velocity_mps=[float(value) for value in state.velocity_mps],
        color=state.color,
        parent_id=state.parent_id,
        visible=state.visible,
        trail_enabled=state.trail_enabled,
        state_origin="cartesian",
    )
    body.validate()
    return body


def _place_root_star_if_unset(system: SolarSystem, body: Body) -> None:
    if any(component != 0.0 for component in (*body.position_m, *body.velocity_mps)):
        return
    _place_root_star(system, body)


def _binary_stars() -> tuple[Body, Body]:
    primary = _new_body("Primary Star", "star")
    secondary = _new_body("Secondary Star", "star")
    secondary.mass_kg = 0.8 * SOLAR_MASS
    secondary.radius_m = 0.85 * SOLAR_RADIUS_M
    secondary.color = "#ffb703"
    separation_m = AU
    total_mass = primary.mass_kg + secondary.mass_kg
    primary_offset = separation_m * secondary.mass_kg / total_mass
    secondary_offset = separation_m * primary.mass_kg / total_mass
    relative_speed = math.sqrt(G * total_mass / separation_m)
    primary.position_m[0] = -primary_offset
    secondary.position_m[0] = secondary_offset
    primary.velocity_mps[1] = -relative_speed * secondary.mass_kg / total_mass
    secondary.velocity_mps[1] = relative_speed * primary.mass_kg / total_mass
    return primary, secondary


def _place_root_star(system: SolarSystem, body: Body) -> None:
    root_stars = [candidate for candidate in system.bodies if candidate.kind == "star" and candidate.parent_id is None]
    if not root_stars:
        return
    spacing_m = 5.0 * AU
    body.position_m[0] = spacing_m * len(root_stars)


def _set_circular_orbit(body: Body, parent: Body, radius_m: float) -> None:
    if radius_m <= 0.0 or not math.isfinite(radius_m):
        raise ModelError("orbit radius must be positive")
    speed = math.sqrt(G * (parent.mass_kg + body.mass_kg) / radius_m)
    body.position_m = [
        parent.position_m[0] + radius_m,
        parent.position_m[1],
        parent.position_m[2],
    ]
    body.velocity_mps = [
        parent.velocity_mps[0],
        parent.velocity_mps[1] + speed,
        parent.velocity_mps[2],
    ]


def _validate_parent_kind(kind: str, parent: Body) -> None:
    if kind == "moon":
        if parent.kind not in {"planet", "dwarf planet"}:
            raise ModelError("moon parent must be a planet or dwarf planet")
        return
    if kind not in {"planet", "dwarf planet", "comet", "asteroid"}:
        raise ModelError(f"unsupported child body kind {kind}")
    if parent.kind != "star":
        raise ModelError(f"{kind} parent must be a star")


def _remove_body_ids(system: SolarSystem, deleted_ids: set[str]) -> None:
    if not deleted_ids:
        return
    if len(deleted_ids) >= len(system.bodies):
        raise ModelError("system must contain at least one body")
    system.bodies = [body for body in system.bodies if body.id not in deleted_ids]
    for group in system.groups:
        group.body_ids = [body_id for body_id in group.body_ids if body_id not in deleted_ids]
        if group.orbit_target_type == "body" and group.orbit_target_id in deleted_ids:
            group.orbit_target_type = None
            group.orbit_target_id = None
            group.orbit = None
            group.data_source = None
    if not any(group.body_ids for group in system.groups):
        root_stars = [body.id for body in system.bodies if body.kind == "star" and body.parent_id is None]
        if root_stars:
            if system.groups:
                system.groups[0].body_ids = [root_stars[0]]
            else:
                system.groups.append(
                    SystemGroup(name=system.name, kind="stellar_system", body_ids=[root_stars[0]])
                )
    _clean_group_orbit_targets(system)
    system.validate()


def _clean_group_orbit_targets(system: SolarSystem) -> None:
    group_ids = {group.id for group in system.groups}
    for group in system.groups:
        if group.orbit_target_type == "group" and group.orbit_target_id not in group_ids:
            group.orbit_target_type = None
            group.orbit_target_id = None
            group.orbit = None
            group.data_source = None


def _body_descendant_ids(bodies: list[Body], body_id: str) -> set[str]:
    if not any(body.id == body_id for body in bodies):
        raise ModelError("body does not exist")
    deleted_ids = {body_id}
    changed = True
    while changed:
        changed = False
        for body in bodies:
            if body.parent_id in deleted_ids and body.id not in deleted_ids:
                deleted_ids.add(body.id)
                changed = True
    return deleted_ids


def _group_descendant_ids(groups: list[SystemGroup], group_id: str) -> set[str]:
    if not any(group.id == group_id for group in groups):
        raise ModelError("group does not exist")
    group_ids = {group_id}
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group.parent_group_id in group_ids and group.id not in group_ids:
                group_ids.add(group.id)
                changed = True
    return group_ids


def _body_by_id(system: SolarSystem, body_id: str | None, role: str) -> Body:
    body = next((candidate for candidate in system.bodies if candidate.id == body_id), None)
    if body is None:
        raise ModelError(f"{role} does not exist")
    return body


def _group_by_id(system: SolarSystem, group_id: str) -> SystemGroup | None:
    return next((group for group in system.groups if group.id == group_id), None)


def _required_name(name: str, field_name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ModelError(f"{field_name} is required")
    return cleaned


def _names_for_body_ids(system: SolarSystem, body_ids: set[str]) -> list[str]:
    return [body.name for body in system.bodies if body.id in body_ids]


def _names_for_group_ids(system: SolarSystem, group_ids: set[str]) -> list[str]:
    return [group.name for group in system.groups if group.id in group_ids]
