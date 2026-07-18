# orbit_editing.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free model mutations for generating orbital state."""

from __future__ import annotations

from .models import Body, DataSource, ModelError, OrbitData, SolarSystem, SystemGroup
from .orbits import (
    binary_pair_state_vectors,
    body_indices_for_group,
    desired_barycenter_from_orbit,
    group_barycenter,
    state_vectors_from_orbit,
    target_anchor,
)
from .playback import SimulationSession


def generate_body_orbit(
    system: SolarSystem,
    simulation: SimulationSession,
    body_id: str,
    orbit: OrbitData,
    data_source: DataSource | None,
) -> Body:
    """Generate and apply a body's state vector around its parent."""

    body = _body_by_id(system, body_id)
    if body.parent_id is None:
        raise ModelError(f"{body.name} does not have a parent body")
    parent = _body_by_id(system, body.parent_id, role="parent body")
    position_m, velocity_mps = state_vectors_from_orbit(parent, body, orbit)

    body.orbit = orbit
    body.data_source = data_source
    body.state_origin = "orbital"
    body.position_m = position_m
    body.velocity_mps = velocity_mps
    simulation.replace_bodies(system.bodies)
    return body


def generate_group_barycenter_orbit(
    system: SolarSystem,
    simulation: SimulationSession,
    group_id: str,
    target_type: str,
    target_id: str,
    orbit: OrbitData,
    data_source: DataSource | None,
) -> SystemGroup:
    """Move a group so its barycenter follows an orbit around a target."""

    group = _group_by_id(system, group_id)
    indices = body_indices_for_group(system.bodies, system.groups, group.id)
    if not indices:
        raise ModelError(f"{group.name} group has no bodies")
    _validate_group_target(system, group, indices, target_type, target_id)

    current = group_barycenter(system.bodies, system.groups, group.id)
    target = target_anchor(system.bodies, system.groups, target_type, target_id)
    desired = desired_barycenter_from_orbit(target, current.mass_kg, orbit)
    position_delta = [desired.position_m[axis] - current.position_m[axis] for axis in range(3)]
    velocity_delta = [desired.velocity_mps[axis] - current.velocity_mps[axis] for axis in range(3)]
    states = [
        (
            [system.bodies[index].position_m[axis] + position_delta[axis] for axis in range(3)],
            [system.bodies[index].velocity_mps[axis] + velocity_delta[axis] for axis in range(3)],
        )
        for index in indices
    ]

    for index, (position_m, velocity_mps) in zip(indices, states):
        system.bodies[index].position_m = position_m
        system.bodies[index].velocity_mps = velocity_mps
    group.orbit = orbit
    group.orbit_target_type = target_type
    group.orbit_target_id = target_id
    group.data_source = data_source
    simulation.replace_bodies(system.bodies)
    return group


def generate_binary_pair_orbit(
    system: SolarSystem,
    simulation: SimulationSession,
    group_id: str,
    orbit: OrbitData,
    data_source: DataSource | None,
) -> SystemGroup:
    """Generate two direct group members around their existing barycenter."""

    group = _group_by_id(system, group_id)
    indices = _direct_binary_indices(system, group)
    first = system.bodies[indices[0]]
    second = system.bodies[indices[1]]
    center = group_barycenter(system.bodies, system.groups, group.id)
    first_state, second_state = binary_pair_state_vectors(
        first,
        second,
        orbit,
        center.position_m,
        center.velocity_mps,
    )

    first.position_m, first.velocity_mps = first_state
    second.position_m, second.velocity_mps = second_state
    group.orbit = orbit
    group.data_source = data_source
    simulation.replace_bodies(system.bodies)
    return group


def _body_by_id(system: SolarSystem, body_id: str, *, role: str = "body") -> Body:
    body = next((item for item in system.bodies if item.id == body_id), None)
    if body is None:
        raise ModelError(f"{role} {body_id} does not exist")
    return body


def _group_by_id(system: SolarSystem, group_id: str) -> SystemGroup:
    group = next((item for item in system.groups if item.id == group_id), None)
    if group is None:
        raise ModelError(f"group {group_id} does not exist")
    return group


def _direct_binary_indices(system: SolarSystem, group: SystemGroup) -> tuple[int, int]:
    if len(group.body_ids) != 2:
        raise ModelError("Binary generation requires exactly two direct bodies.")
    indices_by_id = {body.id: index for index, body in enumerate(system.bodies)}
    indices = [indices_by_id.get(body_id) for body_id in group.body_ids]
    if any(index is None for index in indices):
        raise ModelError("Binary generation requires exactly two valid direct bodies.")
    return int(indices[0]), int(indices[1])


def _validate_group_target(
    system: SolarSystem,
    group: SystemGroup,
    group_indices: list[int],
    target_type: str,
    target_id: str,
) -> None:
    group_body_ids = {system.bodies[index].id for index in group_indices}
    if target_type == "body":
        _body_by_id(system, target_id, role="orbit target body")
        if target_id in group_body_ids:
            raise ModelError(f"{group.name} cannot orbit a body inside itself")
        return
    if target_type == "group":
        target_group = _group_by_id(system, target_id)
        target_indices = body_indices_for_group(system.bodies, system.groups, target_group.id)
        target_body_ids = {system.bodies[index].id for index in target_indices}
        if target_group.id == group.id:
            raise ModelError(f"{group.name} cannot orbit itself")
        if group_body_ids & target_body_ids:
            raise ModelError(f"{group.name} cannot orbit an overlapping group")
        return
    raise ModelError(f"unsupported orbit target type {target_type}")
