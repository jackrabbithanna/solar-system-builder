# reference_frames.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free reference-frame transformations for canonical body state."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .models import FlybyData, ModelError, OrbitData, SolarSystem, SystemReferenceFrame
from .orbits import barycenter_for_indices, group_barycenter

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
