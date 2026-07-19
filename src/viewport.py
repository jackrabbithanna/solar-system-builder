# viewport.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free canvas projection, scale, and hit-test helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Callable, Sequence

from .constants import AU
from .models import Body
from .scales import CanvasBounds, OverviewEntity

Position2D = Sequence[float]

FOCUSED_FIT_HEADROOM = 0.15
FOCUSED_FIT_CONTRACTION_THRESHOLD = 0.15
FOCUSED_FIT_CONTRACTION_RATE = 0.05


@dataclass(frozen=True)
class InsetRect:
    x: float
    y: float
    width: float
    height: float


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


def trail_point_in_system_frame(
    x_m: float,
    y_m: float,
    reference_position: tuple[float, float] | None,
) -> tuple[float, float]:
    if reference_position is None:
        return x_m, y_m
    return x_m + reference_position[0], y_m + reference_position[1]


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


def body_view_center(
    bodies: list[Body],
    view_mode: str,
    selected_index: int,
    selected_group_center: tuple[float, float] | None = None,
    hybrid_bounds: CanvasBounds | None = None,
) -> tuple[float, float]:
    if not bodies:
        return (0.0, 0.0)
    if hybrid_bounds is not None:
        return hybrid_bounds.center
    if view_mode == "follow_selected":
        if selected_group_center is not None:
            return selected_group_center
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


def overview_view_center(entities: list[OverviewEntity], positions: Sequence[Position2D]) -> tuple[float, float]:
    total_mass = sum(entity.mass_kg for entity in entities)
    if total_mass <= 0.0:
        return (0.0, 0.0)
    return (
        sum(entities[index].mass_kg * float(positions[index][0]) for index in range(len(entities))) / total_mass,
        sum(entities[index].mass_kg * float(positions[index][1]) for index in range(len(entities))) / total_mass,
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
