# flybys.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Flyby trajectory helpers independent of GTK."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .constants import G
from .models import Body, FlybyData, ModelError, OrbitData
from .orbits import state_vectors_from_orbit


@dataclass(frozen=True)
class FlybySolution:
    orbit: OrbitData
    position_m: list[float]
    velocity_mps: list[float]


def solve_flyby(
    anchor: Body,
    body: Body,
    flyby: FlybyData,
    *,
    epoch: str = "",
    reference_plane: str = "app-local XY",
) -> FlybySolution:
    """Derive an inbound hyperbolic state from encounter-oriented inputs."""

    flyby.validate()
    if anchor.id != flyby.anchor_body_id:
        raise ModelError("flyby anchor does not match the requested anchor body")
    if body.id == anchor.id:
        raise ModelError("flyby body cannot use itself as its anchor")
    if body.kind == "moon":
        raise ModelError("moon cannot be a flyby body")

    mu = G * (anchor.mass_kg + body.mass_kg)
    if mu <= 0.0 or not math.isfinite(mu):
        raise ModelError("flyby gravitational parameter must be finite and positive")
    axis_magnitude = mu / flyby.velocity_at_infinity_mps**2
    eccentricity = 1.0 + flyby.periapsis_distance_m / axis_magnitude
    cosh_anomaly = (
        flyby.start_distance_m / axis_magnitude + 1.0
    ) / eccentricity
    if cosh_anomaly < 1.0:
        raise ModelError("flyby starting distance does not lie on the requested trajectory")
    hyperbolic_anomaly = -math.acosh(cosh_anomaly)
    mean_anomaly = (
        eccentricity * math.sinh(hyperbolic_anomaly) - hyperbolic_anomaly
    )
    orbit = OrbitData(
        semi_major_axis_m=-axis_magnitude,
        eccentricity=eccentricity,
        inclination_deg=flyby.inclination_deg,
        longitude_of_ascending_node_deg=flyby.longitude_of_ascending_node_deg,
        argument_of_periapsis_deg=flyby.argument_of_periapsis_deg,
        mean_anomaly_deg=math.degrees(mean_anomaly),
        epoch=epoch,
        reference_plane=reference_plane or "app-local XY",
        approximation_notes=(
            "Inbound hyperbolic flyby generated from periapsis distance, "
            "velocity at infinity, and starting distance."
        ),
    )
    position_m, velocity_mps = state_vectors_from_orbit(anchor, body, orbit)
    return FlybySolution(orbit, position_m, velocity_mps)


def radial_velocity_mps(anchor: Body, body: Body) -> float:
    """Return the body's signed radial speed relative to the anchor."""

    relative_position = [
        body.position_m[axis] - anchor.position_m[axis]
        for axis in range(3)
    ]
    distance = math.sqrt(sum(component * component for component in relative_position))
    if distance == 0.0:
        return 0.0
    relative_velocity = [
        body.velocity_mps[axis] - anchor.velocity_mps[axis]
        for axis in range(3)
    ]
    return sum(
        position * velocity
        for position, velocity in zip(relative_position, relative_velocity)
    ) / distance
