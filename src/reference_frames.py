# reference_frames.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free reference-frame transformations for canonical body state."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from .models import FlybyData, ModelError, OrbitData, SolarSystem, SystemReferenceFrame
from .orbits import (
    OrbitAnchor,
    barycenter_for_indices,
    body_indices_for_group,
    group_barycenter,
    orbit_from_state_vectors,
    target_anchor,
)
from .physics import SimulationState, step
from .scales import derived_max_step_s
from .standard_frames import epoch_delta_seconds, rotation_between_frames, shift_epoch

_MATRIX_TOLERANCE = 1.0e-9
_ANGLE_SINGULARITY_TOLERANCE = 1.0e-12


def _vector3(value: Sequence[float], field_name: str) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ModelError(f"{field_name} must contain three values")
    vector = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in vector):
        raise ModelError(f"{field_name} must contain finite values")
    return vector


def validate_rotation_matrix(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    """Validate and normalize a proper three-dimensional rotation matrix."""

    try:
        array = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError) as error:
        raise ModelError("rotation matrix must contain numeric values") from error
    if array.shape != (3, 3):
        raise ModelError("rotation matrix must be 3 by 3")
    if not np.all(np.isfinite(array)):
        raise ModelError("rotation matrix must contain finite values")
    if not np.allclose(array @ array.T, np.identity(3), atol=_MATRIX_TOLERANCE, rtol=0.0):
        raise ModelError("rotation matrix must be orthonormal")
    determinant = float(np.linalg.det(array))
    if not math.isclose(determinant, 1.0, abs_tol=_MATRIX_TOLERANCE):
        raise ModelError("rotation matrix must have determinant +1")
    return tuple(tuple(float(value) for value in row) for row in array)


@dataclass(frozen=True)
class ReferenceFrameTransform:
    """A rigid transform from old coordinates into new coordinates."""

    origin_position_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    origin_velocity_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_matrix: tuple[tuple[float, ...], ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )

    def validated(self) -> "ReferenceFrameTransform":
        return ReferenceFrameTransform(
            origin_position_m=_vector3(self.origin_position_m, "origin position"),
            origin_velocity_mps=_vector3(self.origin_velocity_mps, "origin velocity"),
            rotation_matrix=validate_rotation_matrix(self.rotation_matrix),
        )


def rotation_matrix_from_xyz_degrees(
    x_degrees: float,
    y_degrees: float,
    z_degrees: float,
) -> tuple[tuple[float, ...], ...]:
    """Return Rz(z) @ Ry(y) @ Rx(x), applying fixed X, Y, then Z rotations."""

    angles = (float(x_degrees), float(y_degrees), float(z_degrees))
    if not all(math.isfinite(angle) for angle in angles):
        raise ModelError("rotation angles must be finite")
    x, y, z = (math.radians(angle) for angle in angles)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rotate_x = np.array(((1.0, 0.0, 0.0), (0.0, cx, -sx), (0.0, sx, cx)))
    rotate_y = np.array(((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy)))
    rotate_z = np.array(((cz, -sz, 0.0), (sz, cz, 0.0), (0.0, 0.0, 1.0)))
    return validate_rotation_matrix(rotate_z @ rotate_y @ rotate_x)


def origin_for_body(system: SolarSystem, body_id: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    body = next((item for item in system.bodies if item.id == body_id), None)
    if body is None:
        raise ModelError(f"reference-frame origin body {body_id} does not exist")
    return tuple(body.position_m), tuple(body.velocity_mps)


def origin_for_group(system: SolarSystem, group_id: str) -> tuple[tuple[float, ...], tuple[float, ...]]:
    anchor = group_barycenter(system.bodies, system.groups, group_id)
    return tuple(anchor.position_m), tuple(anchor.velocity_mps)


def origin_for_system(system: SolarSystem) -> tuple[tuple[float, ...], tuple[float, ...]]:
    anchor = barycenter_for_indices(system.bodies, list(range(len(system.bodies))))
    return tuple(anchor.position_m), tuple(anchor.velocity_mps)


def transform_system_reference_frame(
    system: SolarSystem,
    target_frame: SystemReferenceFrame,
    transform: ReferenceFrameTransform,
) -> SolarSystem:
    """Return an atomically validated copy expressed in ``target_frame``."""

    candidate = SolarSystem.from_dict(system.to_dict())
    target = SystemReferenceFrame.from_dict(target_frame.to_dict())
    if target is None:  # pragma: no cover - target_frame.to_dict() cannot return None
        raise ModelError("target reference frame is required")
    operation = transform.validated()
    rotation = np.asarray(operation.rotation_matrix, dtype=float)
    origin_position = np.asarray(operation.origin_position_m, dtype=float)
    origin_velocity = np.asarray(operation.origin_velocity_mps, dtype=float)

    for body in candidate.bodies:
        body.position_m = list(rotation @ (np.asarray(body.position_m) - origin_position))
        body.velocity_mps = list(rotation @ (np.asarray(body.velocity_mps) - origin_velocity))
        if body.orbit is not None:
            _transform_orbit(body.orbit, rotation, target.reference_plane)
        if body.flyby is not None:
            _transform_flyby(body.flyby, rotation)
    for group in candidate.groups:
        if group.orbit is not None:
            _transform_orbit(group.orbit, rotation, target.reference_plane)

    candidate.reference_frame = target
    candidate.validate()
    return candidate


def transform_system_to_standard_frame(
    system: SolarSystem,
    target_frame: SystemReferenceFrame,
    *,
    elapsed_s: float = 0.0,
    external_origin: tuple[Sequence[float], Sequence[float]] | None = None,
    cancel_event: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> SolarSystem:
    """Propagate and atomically transform a complete system into a standard frame.

    ``system`` must already contain the materialized current simulation state.
    ``elapsed_s`` locates that state relative to the stored frame epoch.
    """

    source_frame = system.reference_frame
    if source_frame is None:
        raise ModelError("source reference frame is required")
    source_frame.validate()
    target_frame.validate()
    if source_frame.axes_id == "custom" or target_frame.axes_id == "custom":
        raise ModelError("standard-frame transforms require verified source and target axes")

    candidate = SolarSystem.from_dict(system.to_dict())
    current_epoch = shift_epoch(source_frame.epoch, source_frame.time_scale, elapsed_s)
    propagation_s = epoch_delta_seconds(
        current_epoch,
        source_frame.time_scale,
        target_frame.epoch,
        target_frame.time_scale,
    )
    if propagation_s:
        state = SimulationState.from_bodies(candidate.bodies)
        max_step_s = derived_max_step_s(
            candidate.bodies,
            candidate.settings.accuracy_profile,
        )
        total_steps = max(1, math.ceil(abs(propagation_s) / max_step_s))
        dt_s = propagation_s / total_steps
        for completed in range(total_steps):
            if cancel_event is not None and cancel_event.is_set():
                raise ModelError("reference-frame propagation cancelled")
            state = step(
                state,
                dt_s,
                candidate.settings.physics_mode,
                candidate.settings.integrator,
            )
            if progress is not None and (
                completed + 1 == total_steps
                or completed == 0
                or (completed + 1) % max(1, total_steps // 100) == 0
            ):
                progress(completed + 1, total_steps)
        state.apply_to_bodies(candidate.bodies)

    if external_origin is not None:
        origin_position, origin_velocity = external_origin
    elif target_frame.origin is None:
        raise ModelError("target reference origin is required")
    elif target_frame.origin.kind == "body":
        origin_position, origin_velocity = origin_for_body(
            candidate, target_frame.origin.id or ""
        )
    elif target_frame.origin.kind == "group_barycenter":
        origin_position, origin_velocity = origin_for_group(
            candidate, target_frame.origin.id or ""
        )
    elif target_frame.origin.kind == "system_barycenter":
        origin_position, origin_velocity = origin_for_system(candidate)
    elif (
        target_frame.origin.kind == "jpl"
        and source_frame.origin is not None
        and source_frame.origin.kind == "jpl"
        and target_frame.origin.id == source_frame.origin.id
    ) or (
        target_frame.origin.kind == "custom"
        and source_frame.origin is not None
        and target_frame.origin == source_frame.origin
    ):
        origin_position = origin_velocity = (0.0, 0.0, 0.0)
    else:
        raise ModelError("target origin requires an authoritative translation")

    rotation = rotation_between_frames(source_frame, target_frame)
    transformed = transform_system_reference_frame(
        candidate,
        target_frame,
        ReferenceFrameTransform(
            tuple(float(value) for value in origin_position),
            tuple(float(value) for value in origin_velocity),
            rotation,
        ),
    )
    recompute_osculating_metadata(transformed)
    transformed.epoch = (
        f"{target_frame.epoch} {target_frame.time_scale}, "
        f"{target_frame.reference_system}/{target_frame.reference_plane}, "
        f"{target_frame.center_id}"
    )
    transformed.validate()
    return transformed


def recompute_osculating_metadata(system: SolarSystem) -> None:
    """Refresh optional Newtonian osculating metadata from canonical state."""

    bodies_by_id = {body.id: body for body in system.bodies}
    frame = system.reference_frame
    if frame is None:
        return
    orbit_epoch = f"{frame.epoch} {frame.time_scale}".strip()
    for body in system.bodies:
        if body.orbit is None:
            continue
        anchor_id = body.parent_id
        if anchor_id is None and body.flyby is not None:
            anchor_id = body.flyby.anchor_body_id
        anchor = bodies_by_id.get(anchor_id) if anchor_id is not None else None
        if anchor is None:
            continue
        try:
            body.orbit = orbit_from_state_vectors(
                anchor,
                body,
                epoch=orbit_epoch,
                reference_plane=frame.reference_plane,
            )
        except ModelError:
            if body.flyby is None:
                body.orbit = None
            else:
                body.orbit.epoch = orbit_epoch
                body.orbit.reference_plane = frame.reference_plane
                body.orbit.approximation_notes = (
                    "The propagated flyby state is canonical; osculating elements could not "
                    "be recomputed at this epoch."
                )
    for group in system.groups:
        if group.orbit is None:
            continue
        group_center = group_barycenter(system.bodies, system.groups, group.id)
        if group.orbit_target_type is not None and group.orbit_target_id is not None:
            anchor = target_anchor(
                system.bodies,
                system.groups,
                group.orbit_target_type,
                group.orbit_target_id,
            )
            try:
                group.orbit = orbit_from_state_vectors(
                    anchor,
                    group_center,
                    epoch=orbit_epoch,
                    reference_plane=frame.reference_plane,
                )
            except ModelError:
                group.orbit = None
            continue
        indices = body_indices_for_group(system.bodies, system.groups, group.id)
        direct = [
            bodies_by_id[body_id]
            for body_id in group.body_ids
            if body_id in bodies_by_id
        ]
        if len(direct) != 2 or len(indices) < 2:
            continue
        try:
            group.orbit = orbit_from_state_vectors(
                OrbitAnchor(
                    direct[0].mass_kg,
                    direct[0].position_m,
                    direct[0].velocity_mps,
                ),
                OrbitAnchor(
                    direct[1].mass_kg,
                    direct[1].position_m,
                    direct[1].velocity_mps,
                ),
                epoch=orbit_epoch,
                reference_plane=frame.reference_plane,
            )
        except ModelError:
            group.orbit = None


def _transform_orbit(orbit: OrbitData, rotation: np.ndarray, reference_plane: str) -> None:
    orbit.reference_plane = reference_plane
    if np.allclose(rotation, np.identity(3), atol=_MATRIX_TOLERANCE, rtol=0.0):
        return
    inclination, node, periapsis = _rotated_orientation(
        orbit.inclination_deg or 0.0,
        orbit.longitude_of_ascending_node_deg or 0.0,
        orbit.argument_of_periapsis_deg or 0.0,
        rotation,
    )
    orbit.inclination_deg = inclination
    orbit.longitude_of_ascending_node_deg = node
    orbit.argument_of_periapsis_deg = periapsis


def rotate_orbit_metadata(
    orbit: OrbitData,
    rotation_matrix: Sequence[Sequence[float]],
    reference_plane: str,
) -> OrbitData:
    """Rotate one copied orbit record into a new verified reference plane."""

    copy = OrbitData.from_dict(orbit.to_dict())
    if copy is None:  # pragma: no cover - a serialized OrbitData is never empty
        raise ModelError("orbit metadata is required")
    rotation = np.asarray(validate_rotation_matrix(rotation_matrix), dtype=float)
    _transform_orbit(copy, rotation, reference_plane)
    copy.validate()
    return copy


def _transform_flyby(flyby: FlybyData, rotation: np.ndarray) -> None:
    inclination, node, periapsis = _rotated_orientation(
        flyby.inclination_deg,
        flyby.longitude_of_ascending_node_deg,
        flyby.argument_of_periapsis_deg,
        rotation,
    )
    flyby.inclination_deg = inclination
    flyby.longitude_of_ascending_node_deg = node
    flyby.argument_of_periapsis_deg = periapsis


def _rotated_orientation(
    inclination_deg: float,
    node_deg: float,
    periapsis_deg: float,
    rotation: np.ndarray,
) -> tuple[float, float, float]:
    orientation = (
        _axis_rotation_z(math.radians(node_deg))
        @ _axis_rotation_x(math.radians(inclination_deg))
        @ _axis_rotation_z(math.radians(periapsis_deg))
    )
    rotated = rotation @ orientation
    normal = rotated[:, 2]
    periapsis = rotated[:, 0]
    cos_inclination = max(-1.0, min(1.0, float(normal[2])))
    inclination = math.acos(cos_inclination)
    sin_inclination = math.sin(inclination)

    if abs(sin_inclination) > _ANGLE_SINGULARITY_TOLERANCE:
        node = math.atan2(float(normal[0]), float(-normal[1]))
        argument = math.atan2(float(periapsis[2]), float(rotated[2, 1]))
    elif cos_inclination >= 0.0:
        node = 0.0
        argument = math.atan2(float(periapsis[1]), float(periapsis[0]))
    else:
        node = 0.0
        argument = -math.atan2(float(periapsis[1]), float(periapsis[0]))

    return (
        math.degrees(inclination),
        _normalized_degrees(node),
        _normalized_degrees(argument),
    )


def _axis_rotation_x(angle: float) -> np.ndarray:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array(((1.0, 0.0, 0.0), (0.0, cosine, -sine), (0.0, sine, cosine)))


def _axis_rotation_z(angle: float) -> np.ndarray:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array(((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0)))


def _normalized_degrees(angle: float) -> float:
    value = math.degrees(angle) % 360.0
    return 0.0 if math.isclose(value, 360.0, abs_tol=1.0e-10) else value
