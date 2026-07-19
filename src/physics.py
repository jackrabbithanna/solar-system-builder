# physics.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""NumPy-backed orbital integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .constants import C, DAY, G
from .models import Body

PhysicsMode = Literal["newtonian", "post_newtonian"]
Integrator = Literal["velocity_verlet", "rk4"]
DEFAULT_MAX_STEP_S = DAY

_PHYSICS_MODES = frozenset({"newtonian", "post_newtonian"})
_INTEGRATORS = frozenset({"velocity_verlet", "rk4"})


@dataclass
class SimulationState:
    masses_kg: np.ndarray
    positions_m: np.ndarray
    velocities_mps: np.ndarray
    elapsed_s: float = 0.0

    @classmethod
    def from_bodies(cls, bodies: list[Body]) -> "SimulationState":
        if not bodies:
            raise ValueError("at least one body is required")
        return cls(
            masses_kg=np.array([body.mass_kg for body in bodies], dtype=float),
            positions_m=np.array([body.position_m for body in bodies], dtype=float),
            velocities_mps=np.array([body.velocity_mps for body in bodies], dtype=float),
        )

    def copy(self) -> "SimulationState":
        return SimulationState(
            self.masses_kg.copy(),
            self.positions_m.copy(),
            self.velocities_mps.copy(),
            self.elapsed_s,
        )

    def apply_to_bodies(self, bodies: list[Body]) -> None:
        if len(bodies) != len(self.masses_kg):
            raise ValueError("body count changed during simulation")
        for index, body in enumerate(bodies):
            body.position_m = self.positions_m[index].tolist()
            body.velocity_mps = self.velocities_mps[index].tolist()


@dataclass(frozen=True)
class ConservationDiagnostics:
    kinetic_energy_j: float
    potential_energy_j: float
    total_energy_j: float
    angular_momentum_kg_m2ps: tuple[float, float, float]
    angular_momentum_magnitude_kg_m2ps: float


@dataclass(frozen=True)
class ConservationDrift:
    energy_delta_j: float
    relative_energy_drift: float | None
    angular_momentum_delta_kg_m2ps: tuple[float, float, float]
    angular_momentum_delta_magnitude_kg_m2ps: float
    relative_angular_momentum_drift: float | None


def acceleration(
    masses_kg: np.ndarray,
    positions_m: np.ndarray,
    velocities_mps: np.ndarray,
    mode: PhysicsMode = "post_newtonian",
) -> np.ndarray:
    """Return acceleration vectors for all bodies.

    The post-Newtonian mode uses a pairwise 1PN correction appropriate for
    solar-system scale simulation. It is intentionally isolated from the UI so
    it can be replaced by a more exact formulation later.
    """

    _validate_arrays(masses_kg, positions_m, velocities_mps)
    _validate_physics_mode(mode)
    count = len(masses_kg)
    result = np.zeros_like(positions_m, dtype=float)

    for i in range(count):
        for j in range(count):
            if i == j:
                continue
            delta = positions_m[j] - positions_m[i]
            distance_sq = float(np.dot(delta, delta))
            if distance_sq <= 0.0:
                continue
            distance = distance_sq ** 0.5
            unit = delta / distance
            base = G * masses_kg[j] / distance_sq
            factor = 1.0

            if mode == "post_newtonian":
                relative_velocity = velocities_mps[i] - velocities_mps[j]
                radial_speed = float(np.dot(delta, relative_velocity)) / distance
                speed_sq = float(np.dot(relative_velocity, relative_velocity))
                schwarzschild = 4.0 * G * (masses_kg[i] + masses_kg[j]) / (distance * C * C)
                velocity_term = -speed_sq / (C * C)
                radial_term = 4.0 * radial_speed * radial_speed / (C * C)
                factor += schwarzschild + velocity_term + radial_term
            result[i] += base * factor * unit

    return result


def step(
    state: SimulationState,
    dt_s: float,
    mode: PhysicsMode = "post_newtonian",
    integrator: Integrator = "velocity_verlet",
) -> SimulationState:
    """Advance the simulation with one low-level integrator step."""

    _validate_physics_mode(mode)
    _validate_integrator(integrator)
    if not np.isfinite(dt_s):
        raise ValueError("dt_s must be finite")
    if dt_s == 0.0:
        return state.copy()
    if integrator == "rk4":
        return _rk4_step(state, dt_s, mode)
    return _velocity_verlet_step(state, dt_s, mode)


def _velocity_verlet_step(
    state: SimulationState,
    dt_s: float,
    mode: PhysicsMode,
) -> SimulationState:
    current_accel = acceleration(
        state.masses_kg,
        state.positions_m,
        state.velocities_mps,
        mode,
    )
    next_positions = state.positions_m + state.velocities_mps * dt_s + 0.5 * current_accel * dt_s * dt_s
    estimate_velocities = state.velocities_mps + current_accel * dt_s
    next_accel = acceleration(state.masses_kg, next_positions, estimate_velocities, mode)
    next_velocities = state.velocities_mps + 0.5 * (current_accel + next_accel) * dt_s
    return SimulationState(
        state.masses_kg.copy(),
        next_positions,
        next_velocities,
        state.elapsed_s + dt_s,
    )


def _rk4_step(
    state: SimulationState,
    dt_s: float,
    mode: PhysicsMode,
) -> SimulationState:
    masses = state.masses_kg
    positions = state.positions_m
    velocities = state.velocities_mps

    k1_position = velocities
    k1_velocity = acceleration(masses, positions, velocities, mode)

    k2_position_state = positions + 0.5 * dt_s * k1_position
    k2_velocity_state = velocities + 0.5 * dt_s * k1_velocity
    k2_position = k2_velocity_state
    k2_velocity = acceleration(masses, k2_position_state, k2_velocity_state, mode)

    k3_position_state = positions + 0.5 * dt_s * k2_position
    k3_velocity_state = velocities + 0.5 * dt_s * k2_velocity
    k3_position = k3_velocity_state
    k3_velocity = acceleration(masses, k3_position_state, k3_velocity_state, mode)

    k4_position_state = positions + dt_s * k3_position
    k4_velocity_state = velocities + dt_s * k3_velocity
    k4_position = k4_velocity_state
    k4_velocity = acceleration(masses, k4_position_state, k4_velocity_state, mode)

    next_positions = positions + (dt_s / 6.0) * (
        k1_position + 2.0 * k2_position + 2.0 * k3_position + k4_position
    )
    next_velocities = velocities + (dt_s / 6.0) * (
        k1_velocity + 2.0 * k2_velocity + 2.0 * k3_velocity + k4_velocity
    )
    return SimulationState(
        masses.copy(),
        next_positions,
        next_velocities,
        state.elapsed_s + dt_s,
    )


def advance(
    state: SimulationState,
    total_dt_s: float,
    mode: PhysicsMode = "post_newtonian",
    max_step_s: float = DEFAULT_MAX_STEP_S,
    integrator: Integrator = "velocity_verlet",
) -> SimulationState:
    """Advance by a user-visible interval using bounded internal steps."""

    next_state, _samples = _advance(
        state,
        total_dt_s,
        mode,
        max_step_s,
        integrator,
        collect_samples=False,
    )
    return next_state


def advance_with_samples(
    state: SimulationState,
    total_dt_s: float,
    mode: PhysicsMode = "post_newtonian",
    max_step_s: float = DEFAULT_MAX_STEP_S,
    integrator: Integrator = "velocity_verlet",
) -> tuple[SimulationState, list[np.ndarray]]:
    """Advance using bounded internal steps and return sampled positions."""

    return _advance(
        state,
        total_dt_s,
        mode,
        max_step_s,
        integrator,
        collect_samples=True,
    )


def _advance(
    state: SimulationState,
    total_dt_s: float,
    mode: PhysicsMode,
    max_step_s: float,
    integrator: Integrator,
    collect_samples: bool,
) -> tuple[SimulationState, list[np.ndarray]]:
    _validate_physics_mode(mode)
    _validate_integrator(integrator)
    if not np.isfinite(total_dt_s):
        raise ValueError("total_dt_s must be finite")
    if total_dt_s == 0.0:
        return state.copy(), []
    if max_step_s <= 0.0 or not np.isfinite(max_step_s):
        raise ValueError("max_step_s must be finite and positive")

    next_state = state.copy()
    samples: list[np.ndarray] = []
    remaining = float(total_dt_s)
    direction = 1.0 if remaining > 0.0 else -1.0
    bounded_step = abs(float(max_step_s))

    while abs(remaining) > 0.0:
        dt_s = direction * min(abs(remaining), bounded_step)
        next_state = step(next_state, dt_s, mode, integrator)
        if collect_samples:
            samples.append(next_state.positions_m.copy())
        remaining -= dt_s

    return next_state, samples


def compute_conservation_diagnostics(state: SimulationState) -> ConservationDiagnostics:
    """Return Newtonian mechanical invariants in the center-of-mass frame."""

    _validate_arrays(state.masses_kg, state.positions_m, state.velocities_mps)
    total_mass = float(np.sum(state.masses_kg))
    center_position = np.sum(
        state.positions_m * state.masses_kg[:, np.newaxis],
        axis=0,
    ) / total_mass
    center_velocity = np.sum(
        state.velocities_mps * state.masses_kg[:, np.newaxis],
        axis=0,
    ) / total_mass
    relative_positions = state.positions_m - center_position
    relative_velocities = state.velocities_mps - center_velocity

    kinetic_energy = 0.5 * float(
        np.sum(
            state.masses_kg
            * np.einsum("ij,ij->i", relative_velocities, relative_velocities)
        )
    )
    potential_energy = 0.0
    for first in range(len(state.masses_kg)):
        for second in range(first + 1, len(state.masses_kg)):
            separation = state.positions_m[second] - state.positions_m[first]
            distance = float(np.linalg.norm(separation))
            if distance <= 0.0:
                raise ValueError("conservation diagnostics are undefined for coincident bodies")
            potential_energy -= (
                G * state.masses_kg[first] * state.masses_kg[second] / distance
            )

    angular_momentum = np.sum(
        np.cross(
            relative_positions,
            state.masses_kg[:, np.newaxis] * relative_velocities,
        ),
        axis=0,
    )
    angular_tuple = tuple(float(component) for component in angular_momentum)
    return ConservationDiagnostics(
        kinetic_energy_j=kinetic_energy,
        potential_energy_j=potential_energy,
        total_energy_j=kinetic_energy + potential_energy,
        angular_momentum_kg_m2ps=angular_tuple,
        angular_momentum_magnitude_kg_m2ps=float(np.linalg.norm(angular_momentum)),
    )


def compute_conservation_drift(
    current: ConservationDiagnostics,
    baseline: ConservationDiagnostics,
) -> ConservationDrift:
    """Compare current invariants with a previously captured baseline."""

    energy_delta = current.total_energy_j - baseline.total_energy_j
    relative_energy = (
        energy_delta / abs(baseline.total_energy_j)
        if baseline.total_energy_j != 0.0
        else None
    )
    angular_delta = np.array(current.angular_momentum_kg_m2ps) - np.array(
        baseline.angular_momentum_kg_m2ps
    )
    angular_delta_magnitude = float(np.linalg.norm(angular_delta))
    relative_angular = (
        angular_delta_magnitude / baseline.angular_momentum_magnitude_kg_m2ps
        if baseline.angular_momentum_magnitude_kg_m2ps != 0.0
        else None
    )
    return ConservationDrift(
        energy_delta_j=energy_delta,
        relative_energy_drift=relative_energy,
        angular_momentum_delta_kg_m2ps=tuple(
            float(component) for component in angular_delta
        ),
        angular_momentum_delta_magnitude_kg_m2ps=angular_delta_magnitude,
        relative_angular_momentum_drift=relative_angular,
    )


def _validate_physics_mode(mode: str) -> None:
    if mode not in _PHYSICS_MODES:
        raise ValueError(f"unknown physics mode {mode}")


def _validate_integrator(integrator: str) -> None:
    if integrator not in _INTEGRATORS:
        raise ValueError(f"unknown integrator {integrator}")


def _validate_arrays(
    masses_kg: np.ndarray,
    positions_m: np.ndarray,
    velocities_mps: np.ndarray,
) -> None:
    if masses_kg.ndim != 1:
        raise ValueError("masses must be a 1D array")
    if positions_m.shape != velocities_mps.shape:
        raise ValueError("positions and velocities must have the same shape")
    if positions_m.shape != (len(masses_kg), 3):
        raise ValueError("positions and velocities must be Nx3 arrays")
    if np.any(masses_kg <= 0.0):
        raise ValueError("masses must be positive")
    if not np.isfinite(masses_kg).all() or not np.isfinite(positions_m).all() or not np.isfinite(velocities_mps).all():
        raise ValueError("simulation arrays must be finite")
