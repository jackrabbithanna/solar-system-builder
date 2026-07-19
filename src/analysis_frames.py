# analysis_frames.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Derived translating and rotating analysis frames.

Canonical simulation state remains inertial.  This module evaluates a frame at
one instant and exposes every non-inertial acceleration term explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np

from .models import ModelError, REFERENCE_AXES, SolarSystem
from .orbits import body_indices_for_group
from .physics import SimulationState, acceleration
from .standard_frames import axes_matrix, shift_epoch

OriginKind = Literal["fixed", "body", "group_barycenter", "system_barycenter"]
RotationMode = Literal["fixed", "of_date", "prescribed", "target_pair"]


@dataclass(frozen=True)
class AnalysisFrameSpec:
    origin_kind: OriginKind = "fixed"
    origin_id: str | None = None
    axes_id: str = "current"
    rotation_mode: RotationMode = "fixed"
    rotation_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    angular_rate_rad_s: float = 0.0
    angular_acceleration_rad_s2: float = 0.0
    reference_elapsed_s: float = 0.0
    secondary_kind: OriginKind | None = None
    secondary_id: str | None = None
    label: str = "System / Inertial"

    def validated(self, system: SolarSystem | None = None) -> "AnalysisFrameSpec":
        if self.origin_kind not in {"fixed", "body", "group_barycenter", "system_barycenter"}:
            raise ModelError(f"unsupported analysis origin {self.origin_kind}")
        if self.rotation_mode not in {"fixed", "of_date", "prescribed", "target_pair"}:
            raise ModelError(f"unsupported analysis rotation {self.rotation_mode}")
        if self.axes_id != "current" and (
            self.axes_id not in REFERENCE_AXES or self.axes_id == "custom"
        ):
            raise ModelError(f"unsupported analysis axes {self.axes_id}")
        if self.rotation_mode == "of_date" and self.axes_id == "current":
            raise ModelError("axes-of-date rotation requires registered standard axes")
        if self.rotation_mode == "target_pair" and self.axes_id != "current":
            raise ModelError("target-pair rotation defines its own axes")
        if not all(
            math.isfinite(value)
            for value in (
                *self.rotation_axis,
                self.angular_rate_rad_s,
                self.angular_acceleration_rad_s2,
                self.reference_elapsed_s,
            )
        ):
            raise ModelError("analysis-frame values must be finite")
        if self.origin_kind in {"body", "group_barycenter"} and not self.origin_id:
            raise ModelError("analysis frame origin requires a target")
        axis = np.asarray(self.rotation_axis, dtype=float)
        if axis.shape != (3,):
            raise ModelError("analysis rotation axis must contain three values")
        magnitude = float(np.linalg.norm(axis))
        if self.rotation_mode == "prescribed" and magnitude <= 0.0:
            raise ModelError("prescribed rotation requires a nonzero axis")
        if self.rotation_mode == "target_pair":
            if self.secondary_kind not in {"body", "group_barycenter", "system_barycenter"}:
                raise ModelError("target-pair rotation requires a secondary target")
            if self.secondary_kind in {"body", "group_barycenter"} and not self.secondary_id:
                raise ModelError("target-pair rotation requires a secondary target id")
        if system is not None:
            _target_indices(system, self.origin_kind, self.origin_id)
            if self.rotation_mode == "target_pair" and self.secondary_kind is not None:
                _target_indices(system, self.secondary_kind, self.secondary_id)
        return self

    @property
    def active(self) -> bool:
        return not (
            self.origin_kind == "fixed"
            and self.axes_id == "current"
            and self.rotation_mode == "fixed"
        )


@dataclass(frozen=True)
class FrameKinematics:
    origin_position_m: tuple[float, float, float]
    origin_velocity_mps: tuple[float, float, float]
    origin_acceleration_mps2: tuple[float, float, float]
    rotation_matrix: tuple[tuple[float, ...], ...]
    angular_velocity_rad_s: tuple[float, float, float]
    angular_acceleration_rad_s2: tuple[float, float, float]


@dataclass(frozen=True)
class RelativeDiagnosticRow:
    body_id: str
    name: str
    mass_kg: float
    position_m: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]
    gravitational_acceleration_mps2: tuple[float, float, float]
    translational_acceleration_mps2: tuple[float, float, float]
    coriolis_acceleration_mps2: tuple[float, float, float]
    centrifugal_acceleration_mps2: tuple[float, float, float]
    euler_acceleration_mps2: tuple[float, float, float]
    total_apparent_acceleration_mps2: tuple[float, float, float]


def frame_kinematics(
    system: SolarSystem,
    state: SimulationState,
    spec: AnalysisFrameSpec,
    *,
    physics_mode: str | None = None,
    include_acceleration: bool = True,
) -> FrameKinematics:
    spec.validated(system)
    mode = physics_mode or system.settings.physics_mode
    accelerations = (
        acceleration(
            state.masses_kg,
            state.positions_m,
            state.velocities_mps,
            mode,
        )
        if include_acceleration
        else np.zeros_like(state.positions_m)
    )
    origin_position, origin_velocity, origin_acceleration = _target_state(
        system,
        state,
        accelerations,
        spec.origin_kind,
        spec.origin_id,
    )
    base = _base_rotation(system, spec, state.elapsed_s)
    omega = np.zeros(3)
    alpha = np.zeros(3)
    rotation = base

    if spec.rotation_mode == "prescribed":
        axis = np.asarray(spec.rotation_axis, dtype=float)
        axis /= np.linalg.norm(axis)
        elapsed = state.elapsed_s - spec.reference_elapsed_s
        rate = spec.angular_rate_rad_s + spec.angular_acceleration_rad_s2 * elapsed
        angle = (
            spec.angular_rate_rad_s * elapsed
            + 0.5 * spec.angular_acceleration_rad_s2 * elapsed * elapsed
        )
        rotation = _axis_angle(axis, -angle) @ base
        omega = axis * rate
        alpha = axis * spec.angular_acceleration_rad_s2
    elif spec.rotation_mode == "of_date":
        rotation, omega, alpha = _numerical_axes_kinematics(system, spec, state.elapsed_s)
    elif spec.rotation_mode == "target_pair":
        rotation, omega, alpha = _pair_kinematics(
            system,
            state,
            accelerations,
            spec,
        )

    return FrameKinematics(
        tuple(float(value) for value in origin_position),
        tuple(float(value) for value in origin_velocity),
        tuple(float(value) for value in origin_acceleration),
        tuple(tuple(float(value) for value in row) for row in rotation),
        tuple(float(value) for value in omega),
        tuple(float(value) for value in alpha),
    )


def transform_state(
    state: SimulationState,
    kinematics: FrameKinematics,
) -> SimulationState:
    rotation = np.asarray(kinematics.rotation_matrix)
    origin_position = np.asarray(kinematics.origin_position_m)
    origin_velocity = np.asarray(kinematics.origin_velocity_mps)
    omega = np.asarray(kinematics.angular_velocity_rad_s)
    positions = (rotation @ (state.positions_m - origin_position).T).T
    inertial_relative_velocity = (rotation @ (state.velocities_mps - origin_velocity).T).T
    velocities = inertial_relative_velocity - np.cross(omega, positions)
    return SimulationState(state.masses_kg.copy(), positions, velocities, state.elapsed_s)


def transform_points(
    points: Sequence[Sequence[float]],
    kinematics: FrameKinematics,
) -> list[tuple[float, float, float]]:
    if not points:
        return []
    rotation = np.asarray(kinematics.rotation_matrix)
    origin = np.asarray(kinematics.origin_position_m)
    transformed = (rotation @ (np.asarray(points, dtype=float) - origin).T).T
    return [tuple(float(value) for value in row) for row in transformed]


def relative_diagnostics(
    system: SolarSystem,
    state: SimulationState,
    spec: AnalysisFrameSpec,
) -> tuple[FrameKinematics, list[RelativeDiagnosticRow]]:
    kinematics = frame_kinematics(system, state, spec)
    transformed = transform_state(state, kinematics)
    rotation = np.asarray(kinematics.rotation_matrix)
    origin_acceleration = np.asarray(kinematics.origin_acceleration_mps2)
    omega = np.asarray(kinematics.angular_velocity_rad_s)
    alpha = np.asarray(kinematics.angular_acceleration_rad_s2)
    physical = acceleration(
        state.masses_kg,
        state.positions_m,
        state.velocities_mps,
        system.settings.physics_mode,
    )
    rows = []
    for index, body in enumerate(system.bodies):
        position = transformed.positions_m[index]
        velocity = transformed.velocities_mps[index]
        gravitational = rotation @ physical[index]
        translational = -(rotation @ origin_acceleration)
        coriolis = -2.0 * np.cross(omega, velocity)
        centrifugal = -np.cross(omega, np.cross(omega, position))
        euler = -np.cross(alpha, position)
        total = gravitational + translational + coriolis + centrifugal + euler
        rows.append(
            RelativeDiagnosticRow(
                body.id,
                body.name,
                body.mass_kg,
                _tuple3(position),
                _tuple3(velocity),
                _tuple3(gravitational),
                _tuple3(translational),
                _tuple3(coriolis),
                _tuple3(centrifugal),
                _tuple3(euler),
                _tuple3(total),
            )
        )
    return kinematics, rows


def _base_rotation(system: SolarSystem, spec: AnalysisFrameSpec, elapsed_s: float) -> np.ndarray:
    if spec.axes_id == "current":
        return np.identity(3)
    frame = system.reference_frame
    if frame is None or frame.axes_id == "custom":
        raise ModelError("standard analysis axes require a verified canonical frame")
    source = np.asarray(axes_matrix(frame.axes_id, frame.epoch, frame.time_scale))
    target = np.asarray(axes_matrix(spec.axes_id, frame.epoch, frame.time_scale))
    return target @ source.T


def _numerical_axes_kinematics(
    system: SolarSystem,
    spec: AnalysisFrameSpec,
    elapsed_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = system.reference_frame
    if frame is None or frame.axes_id == "custom" or spec.axes_id == "current":
        raise ModelError("of-date analysis requires verified source and target axes")
    step_s = 60.0
    source = np.asarray(axes_matrix(frame.axes_id, frame.epoch, frame.time_scale))

    def evaluated(offset: float) -> np.ndarray:
        epoch = shift_epoch(frame.epoch, frame.time_scale, elapsed_s + offset)
        return np.asarray(axes_matrix(spec.axes_id, epoch, frame.time_scale)) @ source.T

    return _matrix_kinematics(evaluated(-step_s), evaluated(0.0), evaluated(step_s), step_s)


def _pair_kinematics(
    system: SolarSystem,
    state: SimulationState,
    accelerations: np.ndarray,
    spec: AnalysisFrameSpec,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    current = _pair_rotation(system, state, spec)
    primary_indices = _target_indices(system, spec.origin_kind, spec.origin_id)
    secondary_indices = _target_indices(system, spec.secondary_kind or "fixed", spec.secondary_id)
    primary_position, primary_velocity = _weighted_state(state, primary_indices)
    secondary_position, secondary_velocity = _weighted_state(state, secondary_indices)
    separation = float(np.linalg.norm(secondary_position - primary_position))
    relative_speed = float(np.linalg.norm(secondary_velocity - primary_velocity))
    timescale = separation / max(relative_speed, 1.0e-12)
    step_s = min(60.0, max(1.0e-3, math.sqrt(np.finfo(float).eps) * timescale))

    def sampled(offset_s: float) -> SimulationState:
        return SimulationState(
            state.masses_kg,
            (
                state.positions_m
                + state.velocities_mps * offset_s
                + 0.5 * accelerations * offset_s * offset_s
            ),
            state.velocities_mps + accelerations * offset_s,
            state.elapsed_s + offset_s,
        )

    return _matrix_kinematics(
        _pair_rotation(system, sampled(-step_s), spec),
        current,
        _pair_rotation(system, sampled(step_s), spec),
        step_s,
    )


def _pair_rotation(system: SolarSystem, state: SimulationState, spec: AnalysisFrameSpec) -> np.ndarray:
    primary_position, primary_velocity = _weighted_state(
        state, _target_indices(system, spec.origin_kind, spec.origin_id)
    )
    secondary_position, secondary_velocity = _weighted_state(
        state, _target_indices(system, spec.secondary_kind or "fixed", spec.secondary_id)
    )
    relative_position = secondary_position - primary_position
    relative_velocity = secondary_velocity - primary_velocity
    separation = float(np.linalg.norm(relative_position))
    if separation <= 0.0:
        raise ModelError("co-rotating targets are coincident")
    angular_momentum = np.cross(relative_position, relative_velocity)
    angular_momentum_magnitude = float(np.linalg.norm(angular_momentum))
    if angular_momentum_magnitude <= 1.0e-15 * separation * max(1.0, np.linalg.norm(relative_velocity)):
        raise ModelError("co-rotating targets have undefined angular momentum")
    x_axis = relative_position / separation
    z_axis = angular_momentum / angular_momentum_magnitude
    y_axis = np.cross(z_axis, x_axis)
    y_axis /= np.linalg.norm(y_axis)
    z_axis = np.cross(x_axis, y_axis)
    return np.vstack((x_axis, y_axis, z_axis))


def _matrix_kinematics(
    backward: np.ndarray,
    current: np.ndarray,
    forward: np.ndarray,
    step_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    first = (forward - backward) / (2.0 * step_s)
    second = (forward - 2.0 * current + backward) / (step_s * step_s)
    omega_cross = _skew(-(first @ current.T))
    alpha_cross = _skew(-(second @ current.T))
    return current, _vector_from_cross(omega_cross), _vector_from_cross(alpha_cross)


def _target_state(system, state, accelerations, kind, target_id):
    indices = _target_indices(system, kind, target_id)
    if not indices:
        return np.zeros(3), np.zeros(3), np.zeros(3)
    masses = state.masses_kg[indices]
    total = float(masses.sum())
    weights = masses[:, np.newaxis]
    return (
        np.sum(state.positions_m[indices] * weights, axis=0) / total,
        np.sum(state.velocities_mps[indices] * weights, axis=0) / total,
        np.sum(accelerations[indices] * weights, axis=0) / total,
    )


def _weighted_state(state: SimulationState, indices: list[int]):
    if not indices:
        return np.zeros(3), np.zeros(3)
    masses = state.masses_kg[indices]
    weights = masses[:, np.newaxis]
    total = float(masses.sum())
    return (
        np.sum(state.positions_m[indices] * weights, axis=0) / total,
        np.sum(state.velocities_mps[indices] * weights, axis=0) / total,
    )


def _target_indices(system: SolarSystem, kind: str, target_id: str | None) -> list[int]:
    if kind == "fixed":
        return []
    if kind == "system_barycenter":
        return list(range(len(system.bodies)))
    if kind == "body":
        for index, body in enumerate(system.bodies):
            if body.id == target_id:
                return [index]
        raise ModelError(f"analysis-frame body {target_id} does not exist")
    if kind == "group_barycenter":
        indices = body_indices_for_group(system.bodies, system.groups, target_id or "")
        if indices:
            return indices
        raise ModelError(f"analysis-frame group {target_id} does not exist")
    raise ModelError(f"unsupported analysis target {kind}")


def _axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    x, y, z = axis
    cross = np.array(((0.0, -z, y), (z, 0.0, -x), (-y, x, 0.0)))
    identity = np.identity(3)
    return (
        identity * math.cos(angle)
        + (1.0 - math.cos(angle)) * np.outer(axis, axis)
        + math.sin(angle) * cross
    )


def _skew(matrix: np.ndarray) -> np.ndarray:
    return 0.5 * (matrix - matrix.T)


def _vector_from_cross(matrix: np.ndarray) -> np.ndarray:
    return np.array((matrix[2, 1], matrix[0, 2], matrix[1, 0]))


def _tuple3(values) -> tuple[float, float, float]:
    return tuple(float(value) for value in values)
