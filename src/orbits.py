# orbits.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Orbit conversion helpers independent of GTK."""

from __future__ import annotations

import math

from .constants import G
from dataclasses import dataclass

from .models import Body, ModelError, OrbitData, SystemGroup


@dataclass(frozen=True)
class OrbitAnchor:
    mass_kg: float
    position_m: list[float]
    velocity_mps: list[float]


def semi_major_axis_from_period(period_s: float, parent_mass_kg: float, body_mass_kg: float = 0.0) -> float:
    if period_s <= 0.0:
        raise ModelError("orbital_period_s must be positive")
    mu = _gravitational_parameter(parent_mass_kg, body_mass_kg)
    return (mu * (period_s / math.tau) ** 2) ** (1.0 / 3.0)


def state_vectors_from_orbit(parent: Body, body: Body, orbit: OrbitData) -> tuple[list[float], list[float]]:
    """Return absolute SI position and velocity vectors for body around parent."""

    orbit.validate()
    semi_major_axis_m = orbit.semi_major_axis_m
    if semi_major_axis_m is None:
        if orbit.orbital_period_s is None:
            raise ModelError("orbit requires semi_major_axis_m or orbital_period_s")
        semi_major_axis_m = semi_major_axis_from_period(
            orbit.orbital_period_s,
            parent.mass_kg,
            body.mass_kg,
        )
    relative_position, relative_velocity = _relative_state_from_orbit(
        semi_major_axis_m,
        parent.mass_kg + body.mass_kg,
        orbit,
    )
    return (
        [parent.position_m[index] + relative_position[index] for index in range(3)],
        [parent.velocity_mps[index] + relative_velocity[index] for index in range(3)],
    )


def group_barycenter(bodies: list[Body], groups: list[SystemGroup], group_id: str) -> OrbitAnchor:
    indices = body_indices_for_group(bodies, groups, group_id)
    if not indices:
        raise ModelError("group has no bodies")
    return barycenter_for_indices(bodies, indices)


def barycenter_for_indices(bodies: list[Body], indices: list[int]) -> OrbitAnchor:
    total_mass = sum(bodies[index].mass_kg for index in indices)
    if total_mass <= 0.0:
        raise ModelError("barycenter mass must be positive")
    position = [
        sum(bodies[index].mass_kg * bodies[index].position_m[axis] for index in indices) / total_mass
        for axis in range(3)
    ]
    velocity = [
        sum(bodies[index].mass_kg * bodies[index].velocity_mps[axis] for index in indices) / total_mass
        for axis in range(3)
    ]
    return OrbitAnchor(total_mass, position, velocity)


def target_anchor(
    bodies: list[Body],
    groups: list[SystemGroup],
    target_type: str,
    target_id: str,
) -> OrbitAnchor:
    if target_type == "body":
        body = next((item for item in bodies if item.id == target_id), None)
        if body is None:
            raise ModelError(f"orbit target body {target_id} does not exist")
        return OrbitAnchor(body.mass_kg, body.position_m[:], body.velocity_mps[:])
    if target_type == "group":
        return group_barycenter(bodies, groups, target_id)
    raise ModelError(f"unsupported orbit target type {target_type}")


def desired_barycenter_from_orbit(target: OrbitAnchor, group_mass_kg: float, orbit: OrbitData) -> OrbitAnchor:
    proxy_parent = Body(
        "Target",
        "target",
        target.mass_kg,
        1.0,
        target.position_m[:],
        target.velocity_mps[:],
        "#fff",
    )
    proxy_body = Body(
        "Group",
        "group",
        group_mass_kg,
        1.0,
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        "#fff",
    )
    position, velocity = state_vectors_from_orbit(proxy_parent, proxy_body, orbit)
    return OrbitAnchor(group_mass_kg, position, velocity)


def shift_group_to_barycenter(
    bodies: list[Body],
    groups: list[SystemGroup],
    group_id: str,
    desired: OrbitAnchor,
) -> list[int]:
    indices = body_indices_for_group(bodies, groups, group_id)
    current = barycenter_for_indices(bodies, indices)
    position_delta = [desired.position_m[index] - current.position_m[index] for index in range(3)]
    velocity_delta = [desired.velocity_mps[index] - current.velocity_mps[index] for index in range(3)]
    for body_index in indices:
        body = bodies[body_index]
        body.position_m = [body.position_m[index] + position_delta[index] for index in range(3)]
        body.velocity_mps = [body.velocity_mps[index] + velocity_delta[index] for index in range(3)]
    return indices


def binary_pair_state_vectors(
    first: Body,
    second: Body,
    orbit: OrbitData,
    barycenter_position_m: list[float] | None = None,
    barycenter_velocity_mps: list[float] | None = None,
) -> tuple[tuple[list[float], list[float]], tuple[list[float], list[float]]]:
    orbit.validate()
    semi_major_axis_m = orbit.semi_major_axis_m
    if semi_major_axis_m is None:
        if orbit.orbital_period_s is None:
            raise ModelError("orbit requires semi_major_axis_m or orbital_period_s")
        semi_major_axis_m = semi_major_axis_from_period(
            orbit.orbital_period_s,
            first.mass_kg,
            second.mass_kg,
        )
    relative_position, relative_velocity = _relative_state_from_orbit(
        semi_major_axis_m,
        first.mass_kg + second.mass_kg,
        orbit,
    )
    total_mass = first.mass_kg + second.mass_kg
    if total_mass <= 0.0:
        raise ModelError("binary masses must be positive")
    center_position = barycenter_position_m or barycenter_for_indices([first, second], [0, 1]).position_m
    center_velocity = barycenter_velocity_mps or barycenter_for_indices([first, second], [0, 1]).velocity_mps
    first_fraction = second.mass_kg / total_mass
    second_fraction = first.mass_kg / total_mass
    first_position = [center_position[index] - first_fraction * relative_position[index] for index in range(3)]
    second_position = [center_position[index] + second_fraction * relative_position[index] for index in range(3)]
    first_velocity = [center_velocity[index] - first_fraction * relative_velocity[index] for index in range(3)]
    second_velocity = [center_velocity[index] + second_fraction * relative_velocity[index] for index in range(3)]
    return (first_position, first_velocity), (second_position, second_velocity)


def body_indices_for_group(bodies: list[Body], groups: list[SystemGroup], group_id: str) -> list[int]:
    group_ids = _descendant_group_ids(groups, group_id)
    if group_id not in group_ids:
        return []
    body_ids: set[str] = set()
    for group in groups:
        if group.id in group_ids:
            body_ids.update(group.body_ids)
    changed = True
    while changed:
        changed = False
        for body in bodies:
            if body.parent_id in body_ids and body.id not in body_ids:
                body_ids.add(body.id)
                changed = True
    body_indices_by_id = {body.id: index for index, body in enumerate(bodies)}
    return [body_indices_by_id[body_id] for body_id in body_ids if body_id in body_indices_by_id]


def _gravitational_parameter(parent_mass_kg: float, body_mass_kg: float) -> float:
    total_mass_kg = parent_mass_kg + body_mass_kg
    if total_mass_kg <= 0.0:
        raise ModelError("orbit masses must be positive")
    return G * total_mass_kg


def _relative_state_from_orbit(
    semi_major_axis_m: float,
    total_mass_kg: float,
    orbit: OrbitData,
) -> tuple[list[float], list[float]]:
    orbit.validate()
    eccentricity = orbit.eccentricity if orbit.eccentricity is not None else 0.0
    mean_anomaly = math.radians(orbit.mean_anomaly_deg or 0.0)
    inclination = math.radians(orbit.inclination_deg or 0.0)
    ascending_node = math.radians(orbit.longitude_of_ascending_node_deg or 0.0)
    argument = math.radians(orbit.argument_of_periapsis_deg or 0.0)
    mu = G * total_mass_kg
    if eccentricity < 1.0:
        eccentric_anomaly = _solve_eccentric_anomaly(mean_anomaly, eccentricity)
        cos_e = math.cos(eccentric_anomaly)
        sin_e = math.sin(eccentric_anomaly)
        denominator = 1.0 - eccentricity * cos_e
        orbital_x = semi_major_axis_m * (cos_e - eccentricity)
        orbital_y = semi_major_axis_m * math.sqrt(1.0 - eccentricity**2) * sin_e
        mean_motion = math.sqrt(mu / semi_major_axis_m**3)
        orbital_vx = -semi_major_axis_m * mean_motion * sin_e / denominator
        orbital_vy = (
            semi_major_axis_m
            * mean_motion
            * math.sqrt(1.0 - eccentricity**2)
            * cos_e
            / denominator
        )
    else:
        axis_magnitude = abs(semi_major_axis_m)
        hyperbolic_anomaly = _solve_hyperbolic_anomaly(mean_anomaly, eccentricity)
        cosh_h = math.cosh(hyperbolic_anomaly)
        sinh_h = math.sinh(hyperbolic_anomaly)
        denominator = eccentricity * cosh_h - 1.0
        orbital_x = axis_magnitude * (eccentricity - cosh_h)
        orbital_y = axis_magnitude * math.sqrt(eccentricity**2 - 1.0) * sinh_h
        mean_motion = math.sqrt(mu / axis_magnitude**3)
        orbital_vx = -axis_magnitude * mean_motion * sinh_h / denominator
        orbital_vy = (
            axis_magnitude
            * mean_motion
            * math.sqrt(eccentricity**2 - 1.0)
            * cosh_h
            / denominator
        )
    return (
        _rotate_from_orbital_plane(orbital_x, orbital_y, inclination, ascending_node, argument),
        _rotate_from_orbital_plane(orbital_vx, orbital_vy, inclination, ascending_node, argument),
    )


def _descendant_group_ids(groups: list[SystemGroup], group_id: str) -> set[str]:
    group_ids = {group_id}
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group.parent_group_id in group_ids and group.id not in group_ids:
                group_ids.add(group.id)
                changed = True
    return group_ids


def _solve_eccentric_anomaly(mean_anomaly: float, eccentricity: float) -> float:
    anomaly = math.fmod(mean_anomaly, math.tau)
    if anomaly < 0.0:
        anomaly += math.tau
    if eccentricity == 0.0:
        return anomaly
    estimate = anomaly if eccentricity < 0.8 else math.pi
    for _ in range(32):
        delta = (estimate - eccentricity * math.sin(estimate) - anomaly) / (
            1.0 - eccentricity * math.cos(estimate)
        )
        estimate -= delta
        if abs(delta) < 1.0e-12:
            break
    return estimate


def _solve_hyperbolic_anomaly(mean_anomaly: float, eccentricity: float) -> float:
    estimate = math.asinh(mean_anomaly / eccentricity)
    for _ in range(50):
        correction = (
            eccentricity * math.sinh(estimate) - estimate - mean_anomaly
        ) / (eccentricity * math.cosh(estimate) - 1.0)
        estimate -= correction
        if abs(correction) < 1.0e-12:
            return estimate
    raise ModelError("could not solve hyperbolic anomaly")


def _rotate_from_orbital_plane(
    x: float,
    y: float,
    inclination: float,
    ascending_node: float,
    argument: float,
) -> list[float]:
    cos_node = math.cos(ascending_node)
    sin_node = math.sin(ascending_node)
    cos_inc = math.cos(inclination)
    sin_inc = math.sin(inclination)
    cos_arg = math.cos(argument)
    sin_arg = math.sin(argument)

    return [
        (cos_node * cos_arg - sin_node * sin_arg * cos_inc) * x
        + (-cos_node * sin_arg - sin_node * cos_arg * cos_inc) * y,
        (sin_node * cos_arg + cos_node * sin_arg * cos_inc) * x
        + (-sin_node * sin_arg + cos_node * cos_arg * cos_inc) * y,
        (sin_arg * sin_inc) * x + (cos_arg * sin_inc) * y,
    ]
