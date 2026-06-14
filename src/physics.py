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
DEFAULT_MAX_STEP_S = DAY


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
            elif mode != "newtonian":
                raise ValueError(f"unknown physics mode {mode}")

            result[i] += base * factor * unit

    return result


def step(
    state: SimulationState,
    dt_s: float,
    mode: PhysicsMode = "post_newtonian",
) -> SimulationState:
    """Advance the simulation with a velocity-Verlet style step."""

    if dt_s == 0.0:
        return state.copy()
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


def advance(
    state: SimulationState,
    total_dt_s: float,
    mode: PhysicsMode = "post_newtonian",
    max_step_s: float = DEFAULT_MAX_STEP_S,
) -> SimulationState:
    """Advance by a user-visible interval using bounded internal steps."""

    if total_dt_s == 0.0:
        return state.copy()
    if max_step_s <= 0.0:
        raise ValueError("max_step_s must be positive")

    next_state = state.copy()
    remaining = float(total_dt_s)
    direction = 1.0 if remaining > 0.0 else -1.0
    bounded_step = abs(float(max_step_s))

    while abs(remaining) > 0.0:
        dt_s = direction * min(abs(remaining), bounded_step)
        next_state = step(next_state, dt_s, mode)
        remaining -= dt_s

    return next_state


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
