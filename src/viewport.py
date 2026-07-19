# viewport.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free canvas projection, scale, and hit-test helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Callable, Sequence

import numpy as np

from .constants import AU
from .models import Body
from .scales import CanvasBounds, OverviewEntity

Position2D = Sequence[float]
Position3D = Sequence[float]
Vector3D = tuple[float, float, float]
CameraBasis = tuple[Vector3D, Vector3D, Vector3D]

FOCUSED_FIT_HEADROOM = 0.15
FOCUSED_FIT_CONTRACTION_THRESHOLD = 0.15
FOCUSED_FIT_CONTRACTION_RATE = 0.05
DEFAULT_CAMERA_AZIMUTH_DEG = 45.0
DEFAULT_CAMERA_ELEVATION_DEG = 30.0
CAMERA_ELEVATION_LIMIT_DEG = 85.0
CAMERA_ROTATION_DEG_PER_PX = 0.35


@dataclass(frozen=True)
class InsetRect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Camera3D:
    azimuth_deg: float = DEFAULT_CAMERA_AZIMUTH_DEG
    elevation_deg: float = DEFAULT_CAMERA_ELEVATION_DEG


@dataclass(frozen=True)
class ProjectedPoint3D:
    x: float
    y: float
    depth: float


@dataclass(frozen=True)
class CanvasBounds3D:
    center: tuple[float, float, float]
    radius_m: float


def overview_inset_rect(width: int, height: int) -> InsetRect:
    inset_width = max(80.0, min(240.0, max(160.0, width * 0.3), width - 24.0))
    inset_height = min(inset_width * 2.0 / 3.0, max(60.0, height - 24.0))
    return InsetRect(12.0, height - inset_height - 12.0, inset_width, inset_height)


def point_in_rect(x: float, y: float, rect: InsetRect) -> bool:
    return rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height


def clamp_zoom_factor(zoom_factor: float, minimum: float = 1.0, maximum: float = 64.0) -> float:
    return max(minimum, min(maximum, zoom_factor))


def pan_center_delta(
    offset_x_px: float,
    offset_y_px: float,
    scale: float,
    view_mode: str,
) -> tuple[float, float]:
    """Return the world-space center change needed to follow a pointer drag."""
    if scale <= 0.0 or not math.isfinite(scale):
        return (0.0, 0.0)

    compressed_x_m = offset_x_px / scale
    compressed_y_m = -offset_y_px / scale
    if view_mode == "log_overview":
        compressed_distance_m = math.hypot(compressed_x_m, compressed_y_m)
        if compressed_distance_m > 0.0:
            distance_m = AU * math.expm1(compressed_distance_m / AU)
            factor = distance_m / compressed_distance_m
            compressed_x_m *= factor
            compressed_y_m *= factor
    return (-compressed_x_m, -compressed_y_m)


def camera_after_drag(
    camera: Camera3D,
    offset_x_px: float,
    offset_y_px: float,
    sensitivity_deg_per_px: float = CAMERA_ROTATION_DEG_PER_PX,
) -> Camera3D:
    """Return an orbit-camera orientation for a cumulative pointer drag."""
    return Camera3D(
        azimuth_deg=(camera.azimuth_deg - offset_x_px * sensitivity_deg_per_px) % 360.0,
        elevation_deg=max(
            -CAMERA_ELEVATION_LIMIT_DEG,
            min(
                CAMERA_ELEVATION_LIMIT_DEG,
                camera.elevation_deg + offset_y_px * sensitivity_deg_per_px,
            ),
        ),
    )


def camera_basis(
    camera: Camera3D,
) -> CameraBasis:
    """Return screen-right, screen-up, and center-to-camera unit vectors."""
    azimuth = math.radians(camera.azimuth_deg)
    elevation = math.radians(camera.elevation_deg)
    sin_azimuth = math.sin(azimuth)
    cos_azimuth = math.cos(azimuth)
    sin_elevation = math.sin(elevation)
    cos_elevation = math.cos(elevation)
    right = (-sin_azimuth, cos_azimuth, 0.0)
    up = (
        -sin_elevation * cos_azimuth,
        -sin_elevation * sin_azimuth,
        cos_elevation,
    )
    toward_camera = (
        cos_elevation * cos_azimuth,
        cos_elevation * sin_azimuth,
        sin_elevation,
    )
    return right, up, toward_camera


def pan_center_delta_3d(
    offset_x_px: float,
    offset_y_px: float,
    scale: float,
    view_mode: str,
    camera: Camera3D,
) -> tuple[float, float, float]:
    """Return a camera-plane world-space center change for a pointer drag."""
    if scale <= 0.0 or not math.isfinite(scale):
        return (0.0, 0.0, 0.0)
    horizontal_m = offset_x_px / scale
    vertical_m = -offset_y_px / scale
    if view_mode == "log_overview":
        compressed_distance_m = math.hypot(horizontal_m, vertical_m)
        if compressed_distance_m > 0.0:
            distance_m = AU * math.expm1(compressed_distance_m / AU)
            factor = distance_m / compressed_distance_m
            horizontal_m *= factor
            vertical_m *= factor
    right, up, _toward_camera = camera_basis(camera)
    return tuple(
        -(horizontal_m * right[axis] + vertical_m * up[axis])
        for axis in range(3)
    )


def focused_fit_bounds(bodies: list[Body], active_indices: list[int]) -> CanvasBounds | None:
    indices = [index for index in active_indices if 0 <= index < len(bodies)]
    if not indices:
        return None

    total_mass = sum(bodies[index].mass_kg for index in indices)
    if total_mass > 0.0:
        center_x_m = sum(bodies[index].mass_kg * bodies[index].position_m[0] for index in indices) / total_mass
        center_y_m = sum(bodies[index].mass_kg * bodies[index].position_m[1] for index in indices) / total_mass
    else:
        center_x_m = sum(bodies[index].position_m[0] for index in indices) / len(indices)
        center_y_m = sum(bodies[index].position_m[1] for index in indices) / len(indices)

    radius_m = max(
        math.hypot(
            bodies[index].position_m[0] - center_x_m,
            bodies[index].position_m[1] - center_y_m,
        )
        for index in indices
    )
    fallback_extent = max(max(bodies[index].radius_m for index in indices), 1.0)
    padded_extent = max(radius_m, fallback_extent) / (1.0 - FOCUSED_FIT_HEADROOM)
    return CanvasBounds(
        center=(center_x_m, center_y_m),
        half_width_m=padded_extent,
        half_height_m=padded_extent,
    )


def focused_fit_bounds_3d(
    bodies: list[Body],
    active_indices: list[int],
) -> CanvasBounds3D | None:
    indices = [index for index in active_indices if 0 <= index < len(bodies)]
    if not indices:
        return None
    total_mass = sum(bodies[index].mass_kg for index in indices)
    if total_mass > 0.0:
        center = tuple(
            sum(
                bodies[index].mass_kg * bodies[index].position_m[axis]
                for index in indices
            ) / total_mass
            for axis in range(3)
        )
    else:
        center = tuple(
            sum(bodies[index].position_m[axis] for index in indices) / len(indices)
            for axis in range(3)
        )
    radius_m = max(
        math.dist(bodies[index].position_m, center)
        for index in indices
    )
    fallback_extent = max(max(bodies[index].radius_m for index in indices), 1.0)
    return CanvasBounds3D(
        center=center,
        radius_m=max(radius_m, fallback_extent) / (1.0 - FOCUSED_FIT_HEADROOM),
    )


def stabilize_focused_extent(previous_extent_m: float | None, required_extent_m: float) -> float:
    if previous_extent_m is None or required_extent_m >= previous_extent_m:
        return required_extent_m
    if required_extent_m >= previous_extent_m * (1.0 - FOCUSED_FIT_CONTRACTION_THRESHOLD):
        return previous_extent_m
    return previous_extent_m + (required_extent_m - previous_extent_m) * FOCUSED_FIT_CONTRACTION_RATE


def project(
    x_m: float,
    y_m: float,
    origin_x: float,
    origin_y: float,
    scale: float,
    center_x_m: float,
    center_y_m: float,
    view_mode: str,
) -> tuple[float, float]:
    x_delta = x_m - center_x_m
    y_delta = y_m - center_y_m
    if view_mode == "log_overview":
        distance = math.hypot(x_delta, y_delta)
        if distance > 0.0:
            compressed = AU * math.log1p(distance / AU)
            factor = compressed / distance
            x_delta *= factor
            y_delta *= factor
    return origin_x + x_delta * scale, origin_y - y_delta * scale


def project_3d(
    position_m: Position3D,
    origin_x: float,
    origin_y: float,
    scale: float,
    center_m: Position3D,
    view_mode: str,
    camera: Camera3D,
    basis: CameraBasis | None = None,
) -> ProjectedPoint3D:
    delta = tuple(float(position_m[axis]) - float(center_m[axis]) for axis in range(3))
    if view_mode == "log_overview":
        distance = math.sqrt(sum(value * value for value in delta))
        if distance > 0.0:
            compressed = AU * math.log1p(distance / AU)
            factor = compressed / distance
            delta = tuple(value * factor for value in delta)
    right, up, toward_camera = basis if basis is not None else camera_basis(camera)
    horizontal = sum(delta[axis] * right[axis] for axis in range(3))
    vertical = sum(delta[axis] * up[axis] for axis in range(3))
    depth = sum(delta[axis] * toward_camera[axis] for axis in range(3))
    return ProjectedPoint3D(
        x=origin_x + horizontal * scale,
        y=origin_y - vertical * scale,
        depth=depth,
    )


def project_points_3d(
    positions_m: Sequence[Position3D] | np.ndarray,
    origin_x: float,
    origin_y: float,
    scale: float,
    center_m: Position3D,
    view_mode: str,
    camera: Camera3D,
    basis: CameraBasis | None = None,
) -> np.ndarray:
    """Vectorized variant of :func:`project_3d` for trails and guide lines."""
    points = np.asarray(positions_m, dtype=float)
    if points.size == 0:
        return np.empty((0, 3), dtype=float)
    points = points.reshape((-1, 3))
    deltas = points - np.asarray(center_m, dtype=float)
    if view_mode == "log_overview":
        distances = np.linalg.norm(deltas, axis=1)
        factors = np.ones_like(distances)
        nonzero = distances > 0.0
        factors[nonzero] = AU * np.log1p(distances[nonzero] / AU) / distances[nonzero]
        deltas = deltas * factors[:, np.newaxis]
    resolved_basis = basis if basis is not None else camera_basis(camera)
    camera_coordinates = deltas @ np.asarray(resolved_basis, dtype=float).T
    projected = np.empty_like(camera_coordinates)
    projected[:, 0] = origin_x + camera_coordinates[:, 0] * scale
    projected[:, 1] = origin_y - camera_coordinates[:, 1] * scale
    projected[:, 2] = camera_coordinates[:, 2]
    return projected


def trail_point_in_system_frame(
    x_m: float,
    y_m: float,
    reference_position: tuple[float, float] | None,
) -> tuple[float, float]:
    if reference_position is None:
        return x_m, y_m
    return x_m + reference_position[0], y_m + reference_position[1]


def trail_point_in_system_frame_3d(
    point_m: Position3D,
    reference_position: Position3D | None,
) -> tuple[float, float, float]:
    if reference_position is None:
        return tuple(float(point_m[axis]) for axis in range(3))
    return tuple(
        float(point_m[axis]) + float(reference_position[axis])
        for axis in range(3)
    )


def view_distance(
    x_m: float,
    y_m: float,
    center_x_m: float,
    center_y_m: float,
    view_mode: str,
) -> float:
    distance = math.hypot(x_m - center_x_m, y_m - center_y_m)
    if view_mode == "log_overview":
        return AU * math.log1p(distance / AU)
    return distance


def view_distance_3d(position_m: Position3D, center_m: Position3D, view_mode: str) -> float:
    distance = math.dist(position_m, center_m)
    if view_mode == "log_overview":
        return AU * math.log1p(distance / AU)
    return distance


def uncompress_view_distance(distance_m: float, view_mode: str) -> float:
    if view_mode == "log_overview":
        return AU * math.expm1(max(0.0, distance_m) / AU)
    return max(0.0, distance_m)


def body_view_center(
    bodies: list[Body],
    view_mode: str,
    selected_index: int,
    selected_group_center: Sequence[float] | None = None,
    hybrid_bounds: CanvasBounds | None = None,
) -> tuple[float, float]:
    if not bodies:
        return (0.0, 0.0)
    if hybrid_bounds is not None:
        return hybrid_bounds.center
    if view_mode == "follow_selected":
        if selected_group_center is not None:
            return (float(selected_group_center[0]), float(selected_group_center[1]))
        selected = bodies[max(0, min(selected_index, len(bodies) - 1))]
        if selected.parent_id is not None:
            parent = next((body for body in bodies if body.id == selected.parent_id), None)
            if parent is not None:
                return (parent.position_m[0], parent.position_m[1])
        return (selected.position_m[0], selected.position_m[1])
    total_mass = sum(body.mass_kg for body in bodies)
    if total_mass <= 0.0:
        return (0.0, 0.0)
    return (
        sum(body.mass_kg * body.position_m[0] for body in bodies) / total_mass,
        sum(body.mass_kg * body.position_m[1] for body in bodies) / total_mass,
    )


def body_view_center_3d(
    bodies: list[Body],
    view_mode: str,
    selected_index: int,
    selected_group_center: Position3D | None = None,
    hybrid_bounds: CanvasBounds3D | None = None,
) -> tuple[float, float, float]:
    if not bodies:
        return (0.0, 0.0, 0.0)
    if hybrid_bounds is not None:
        return hybrid_bounds.center
    if view_mode == "follow_selected":
        if selected_group_center is not None:
            return tuple(float(selected_group_center[axis]) for axis in range(3))
        selected = bodies[max(0, min(selected_index, len(bodies) - 1))]
        if selected.parent_id is not None:
            parent = next((body for body in bodies if body.id == selected.parent_id), None)
            if parent is not None:
                return tuple(float(parent.position_m[axis]) for axis in range(3))
        return tuple(float(selected.position_m[axis]) for axis in range(3))
    total_mass = sum(body.mass_kg for body in bodies)
    if total_mass <= 0.0:
        return (0.0, 0.0, 0.0)
    return tuple(
        sum(body.mass_kg * body.position_m[axis] for body in bodies) / total_mass
        for axis in range(3)
    )


def overview_view_center(entities: list[OverviewEntity], positions: Sequence[Position2D]) -> tuple[float, float]:
    total_mass = sum(entity.mass_kg for entity in entities)
    if total_mass <= 0.0:
        return (0.0, 0.0)
    return (
        sum(entities[index].mass_kg * float(positions[index][0]) for index in range(len(entities))) / total_mass,
        sum(entities[index].mass_kg * float(positions[index][1]) for index in range(len(entities))) / total_mass,
    )


def overview_view_center_3d(
    entities: list[OverviewEntity],
    positions: Sequence[Position3D],
) -> tuple[float, float, float]:
    total_mass = sum(entity.mass_kg for entity in entities)
    if total_mass <= 0.0:
        return (0.0, 0.0, 0.0)
    return tuple(
        sum(
            entities[index].mass_kg * float(positions[index][axis])
            for index in range(len(entities))
        ) / total_mass
        for axis in range(3)
    )


def overview_canvas_scale(
    width: int,
    height: int,
    positions: Sequence[Position2D],
    center_x_m: float,
    center_y_m: float,
    zoom_factor: float,
    view_mode: str,
) -> float:
    max_distance = max(
        view_distance(float(position[0]), float(position[1]), center_x_m, center_y_m, view_mode)
        for position in positions
    )
    return min(width, height) * 0.45 / max(max_distance, AU) * zoom_factor


def overview_canvas_scale_3d(
    width: int,
    height: int,
    positions: Sequence[Position3D],
    center_m: Position3D,
    zoom_factor: float,
    view_mode: str,
) -> float:
    max_distance = max(
        view_distance_3d(position, center_m, view_mode)
        for position in positions
    )
    return min(width, height) * 0.45 / max(max_distance, AU) * zoom_factor


def canvas_scale(
    width: int,
    height: int,
    bodies: list[Body],
    active_indices: list[int],
    center_x_m: float,
    center_y_m: float,
    zoom_factor: float,
    view_mode: str,
    *,
    use_focused_bounds: bool = False,
    focused_bounds: CanvasBounds | None = None,
) -> float:
    if use_focused_bounds:
        bounds = focused_bounds or focused_fit_bounds(bodies, active_indices)
        if bounds is not None:
            horizontal_scale = width * 0.45 / bounds.half_width_m
            vertical_scale = height * 0.45 / bounds.half_height_m
            return min(horizontal_scale, vertical_scale) * zoom_factor
    max_distance = max(
        view_distance(body.position_m[0], body.position_m[1], center_x_m, center_y_m, view_mode)
        for index, body in enumerate(bodies)
        if index in active_indices
    )
    return min(width, height) * 0.45 / max(max_distance, AU) * zoom_factor


def canvas_scale_3d(
    width: int,
    height: int,
    bodies: list[Body],
    active_indices: list[int],
    center_m: Position3D,
    zoom_factor: float,
    view_mode: str,
    *,
    use_focused_bounds: bool = False,
    focused_bounds: CanvasBounds3D | None = None,
) -> float:
    if use_focused_bounds:
        bounds = focused_bounds or focused_fit_bounds_3d(bodies, active_indices)
        if bounds is not None:
            return min(width, height) * 0.45 / bounds.radius_m * zoom_factor
    active_index_set = set(active_indices)
    max_distance = max(
        view_distance_3d(body.position_m, center_m, view_mode)
        for index, body in enumerate(bodies)
        if index in active_index_set
    )
    return min(width, height) * 0.45 / max(max_distance, AU) * zoom_factor


def body_index_at_point(
    bodies: list[Body],
    active_indices: list[int],
    pointer_x: float,
    pointer_y: float,
    width: int,
    height: int,
    scale: float,
    center_x_m: float,
    center_y_m: float,
    view_mode: str,
    display_radius: Callable[[Body], float],
) -> int | None:
    if not bodies or width <= 0 or height <= 0:
        return None

    origin_x = width / 2.0
    origin_y = height / 2.0
    active_index_set = set(active_indices)
    closest_index = None
    closest_distance = math.inf
    for index, body in enumerate(bodies):
        if index not in active_index_set or not body.visible:
            continue
        body_x, body_y = project(
            body.position_m[0],
            body.position_m[1],
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
            view_mode,
        )
        distance = math.hypot(pointer_x - body_x, pointer_y - body_y)
        hit_radius = max(display_radius(body) + 4.0, 8.0)
        if distance <= hit_radius and distance < closest_distance:
            closest_index = index
            closest_distance = distance
    return closest_index


def body_index_at_point_3d(
    bodies: list[Body],
    active_indices: list[int],
    pointer_x: float,
    pointer_y: float,
    width: int,
    height: int,
    scale: float,
    center_m: Position3D,
    view_mode: str,
    camera: Camera3D,
    display_radius: Callable[[Body], float],
) -> int | None:
    if not bodies or width <= 0 or height <= 0:
        return None
    active_index_set = set(active_indices)
    candidates: list[tuple[float, float, int]] = []
    basis = camera_basis(camera)
    for index, body in enumerate(bodies):
        if index not in active_index_set or not body.visible:
            continue
        point = project_3d(
            body.position_m,
            width / 2.0,
            height / 2.0,
            scale,
            center_m,
            view_mode,
            camera,
            basis,
        )
        distance = math.hypot(pointer_x - point.x, pointer_y - point.y)
        if distance <= max(display_radius(body) + 4.0, 8.0):
            candidates.append((point.depth, -distance, index))
    return max(candidates)[2] if candidates else None


def entity_at_point(
    entities: list[OverviewEntity],
    positions: Sequence[Position2D],
    pointer_x: float,
    pointer_y: float,
    width: int,
    height: int,
    scale: float,
    center_x_m: float,
    center_y_m: float,
    view_mode: str,
    hit_radius: float,
    *,
    origin_x: float | None = None,
    origin_y: float | None = None,
) -> OverviewEntity | None:
    origin_x = width / 2.0 if origin_x is None else origin_x
    origin_y = height / 2.0 if origin_y is None else origin_y
    closest_entity = None
    closest_distance = math.inf

    for index, entity in enumerate(entities):
        position = positions[index]
        entity_x, entity_y = project(
            float(position[0]),
            float(position[1]),
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
            view_mode,
        )
        distance = math.hypot(pointer_x - entity_x, pointer_y - entity_y)
        if distance <= hit_radius and distance < closest_distance:
            closest_entity = entity
            closest_distance = distance
    return closest_entity


def entity_at_point_3d(
    entities: list[OverviewEntity],
    positions: Sequence[Position3D],
    pointer_x: float,
    pointer_y: float,
    width: int,
    height: int,
    scale: float,
    center_m: Position3D,
    view_mode: str,
    camera: Camera3D,
    hit_radius: float,
) -> OverviewEntity | None:
    if not entities or width <= 0 or height <= 0:
        return None
    candidates: list[tuple[float, float, int]] = []
    basis = camera_basis(camera)
    for index, entity in enumerate(entities):
        point = project_3d(
            positions[index],
            width / 2.0,
            height / 2.0,
            scale,
            center_m,
            view_mode,
            camera,
            basis,
        )
        distance = math.hypot(pointer_x - point.x, pointer_y - point.y)
        if distance <= hit_radius:
            candidates.append((point.depth, -distance, index))
    return entities[max(candidates)[2]] if candidates else None


def weighted_position_3d(
    masses: Sequence[float],
    positions: Sequence[Position3D],
) -> tuple[float, float, float] | None:
    if len(positions) == 0 or len(masses) < len(positions):
        return None
    total_mass = sum(float(masses[index]) for index in range(len(positions)))
    if total_mass <= 0.0:
        return None
    return tuple(
        sum(
            float(masses[index]) * float(positions[index][axis])
            for index in range(len(positions))
        ) / total_mass
        for axis in range(3)
    )


def shared_barycenter_point_3d(
    entities: list[OverviewEntity],
    positions: Sequence[Position3D],
    origin_x: float,
    origin_y: float,
    scale: float,
    center_m: Position3D,
    view_mode: str,
    camera: Camera3D,
) -> ProjectedPoint3D | None:
    if len(entities) < 2 or len(positions) < len(entities):
        return None
    barycenter = weighted_position_3d(
        [entity.mass_kg for entity in entities],
        positions[: len(entities)],
    )
    if barycenter is None:
        return None
    return project_3d(barycenter, origin_x, origin_y, scale, center_m, view_mode, camera)


def focused_body_barycenter_point_3d(
    bodies: list[Body],
    active_indices: set[int],
    origin_x: float,
    origin_y: float,
    scale: float,
    center_m: Position3D,
    view_mode: str,
    camera: Camera3D,
) -> ProjectedPoint3D | None:
    visible_indices = [
        index
        for index in active_indices
        if 0 <= index < len(bodies) and bodies[index].visible
    ]
    if len(visible_indices) < 2:
        return None
    barycenter = weighted_position_3d(
        [bodies[index].mass_kg for index in visible_indices],
        [bodies[index].position_m for index in visible_indices],
    )
    if barycenter is None:
        return None
    return project_3d(barycenter, origin_x, origin_y, scale, center_m, view_mode, camera)


def shared_barycenter_point(
    entities: list[OverviewEntity],
    positions: Sequence[Position2D],
    origin_x: float,
    origin_y: float,
    scale: float,
    center_x_m: float,
    center_y_m: float,
    view_mode: str,
) -> tuple[float, float] | None:
    if len(entities) < 2 or len(positions) < len(entities):
        return None
    total_mass = sum(entity.mass_kg for entity in entities)
    if total_mass <= 0.0:
        return None
    barycenter_x_m = sum(
        entity.mass_kg * float(positions[index][0])
        for index, entity in enumerate(entities)
    ) / total_mass
    barycenter_y_m = sum(
        entity.mass_kg * float(positions[index][1])
        for index, entity in enumerate(entities)
    ) / total_mass
    return project(barycenter_x_m, barycenter_y_m, origin_x, origin_y, scale, center_x_m, center_y_m, view_mode)


def focused_body_barycenter_point(
    bodies: list[Body],
    active_indices: set[int],
    origin_x: float,
    origin_y: float,
    scale: float,
    center_x_m: float,
    center_y_m: float,
    view_mode: str,
) -> tuple[float, float] | None:
    visible_indices = [
        index
        for index in active_indices
        if 0 <= index < len(bodies) and bodies[index].visible
    ]
    if len(visible_indices) < 2:
        return None
    total_mass = sum(bodies[index].mass_kg for index in visible_indices)
    if total_mass <= 0.0:
        return None
    barycenter_x_m = sum(
        bodies[index].mass_kg * bodies[index].position_m[0]
        for index in visible_indices
    ) / total_mass
    barycenter_y_m = sum(
        bodies[index].mass_kg * bodies[index].position_m[1]
        for index in visible_indices
    ) / total_mass
    return project(barycenter_x_m, barycenter_y_m, origin_x, origin_y, scale, center_x_m, center_y_m, view_mode)
