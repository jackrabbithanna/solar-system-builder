# scales.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Scale, unit, and playback policy helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .constants import AU, DAY, G, YEAR
from .models import Body, SystemGroup

LIGHT_YEAR = 299_792_458.0 * YEAR
LIGHT_YEAR_THRESHOLD_M = 10_000.0 * AU

TIME_UNITS: tuple[tuple[str, str, float], ...] = (
    ("Days", "days", DAY),
    ("Years", "years", YEAR),
    ("Decades", "decades", 10.0 * YEAR),
    ("Centuries", "centuries", 100.0 * YEAR),
    ("Millennia", "millennia", 1_000.0 * YEAR),
    ("Myr", "myr", 1_000_000.0 * YEAR),
)

DISTANCE_UNITS: tuple[tuple[str, str, float], ...] = (
    ("km", "km", 1_000.0),
    ("AU", "AU", AU),
    ("kAU", "kAU", 1_000.0 * AU),
    ("ly", "ly", LIGHT_YEAR),
)

ACCURACY_LABELS: tuple[tuple[str, str], ...] = (
    ("High", "high"),
    ("Balanced", "balanced"),
    ("Fast", "fast"),
)

VIEW_MODE_LABELS: tuple[tuple[str, str], ...] = (
    ("Fit System", "fit_system"),
    ("Follow Selected", "follow_selected"),
    ("Log Overview", "log_overview"),
)

SIMULATION_SCOPE_LABELS: tuple[tuple[str, str], ...] = (
    ("Auto", "auto"),
    ("System Overview", "system_overview"),
    ("Full N-body", "full_nbody"),
    ("Stellar Overview", "stellar_overview"),
    ("Focused Subsystem", "focused_subsystem"),
)

_PERIOD_FRACTIONS = {
    "high": 1.0 / 120.0,
    "balanced": 1.0 / 80.0,
    "fast": 1.0 / 32.0,
}

_PROFILE_CLAMPS_S = {
    "high": (0.125 * DAY, 7.0 * DAY),
    "balanced": (0.25 * DAY, 30.0 * DAY),
    "fast": (0.5 * DAY, 120.0 * DAY),
}

_OVERVIEW_PROFILE_CLAMPS_S = {
    "high": (YEAR, 10.0 * YEAR),
    "balanced": (10.0 * YEAR, 100.0 * YEAR),
    "fast": (100.0 * YEAR, 1_000.0 * YEAR),
}


@dataclass(frozen=True)
class OverviewEntity:
    id: str
    name: str
    kind: str
    mass_kg: float
    position_m: list[float]
    velocity_mps: list[float]
    color: str


def unit_index(items: tuple[tuple[str, str, float], ...] | tuple[tuple[str, str], ...], value: str) -> int:
    return next((index for index, item in enumerate(items) if item[1] == value), 0)


def time_unit_for_seconds(seconds: float) -> str:
    abs_seconds = abs(seconds)
    if abs_seconds >= 1_000_000.0 * YEAR:
        return "myr"
    if abs_seconds >= 1_000.0 * YEAR:
        return "millennia"
    if abs_seconds >= 100.0 * YEAR:
        return "centuries"
    if abs_seconds >= 10.0 * YEAR:
        return "decades"
    if abs_seconds >= YEAR:
        return "years"
    return "days"


def unit_factor(items: tuple[tuple[str, str, float], ...], value: str) -> float:
    return next((item[2] for item in items if item[1] == value), items[0][2])


def format_elapsed_time(seconds: float) -> str:
    abs_seconds = abs(seconds)
    sign = "-" if seconds < 0.0 else ""
    if abs_seconds >= 1_000_000.0 * YEAR:
        return f"{sign}{abs_seconds / (1_000_000.0 * YEAR):,.2f} Myr"
    if abs_seconds >= 1_000.0 * YEAR:
        return f"{sign}{abs_seconds / (1_000.0 * YEAR):,.2f} millennia"
    if abs_seconds >= 100.0 * YEAR:
        return f"{sign}{abs_seconds / (100.0 * YEAR):,.2f} centuries"
    if abs_seconds >= 2.0 * YEAR:
        return f"{sign}{abs_seconds / YEAR:,.2f} years"
    return f"{sign}{abs_seconds / DAY:,.2f} days"


def distance_between_bodies_m(body: Body, other: Body) -> float:
    return math.dist(body.position_m, other.position_m)


def format_distance(distance_m: float) -> str:
    abs_distance_m = abs(distance_m)
    if abs_distance_m >= LIGHT_YEAR_THRESHOLD_M:
        light_years = abs_distance_m / LIGHT_YEAR
        if light_years < 10.0:
            return f"{light_years:.4f} ly"
        return f"{light_years:,.2f} ly"

    au = abs_distance_m / AU
    if au < 0.01:
        return f"{au:.4f} AU"
    if au < 100.0:
        return f"{au:.2f} AU"
    return f"{au:,.1f} AU"


def derived_max_step_s(bodies: list[Body], accuracy_profile: str) -> float:
    fraction = _PERIOD_FRACTIONS.get(accuracy_profile, _PERIOD_FRACTIONS["balanced"])
    lower, upper = _PROFILE_CLAMPS_S.get(accuracy_profile, _PROFILE_CLAMPS_S["balanced"])
    shortest_period = _shortest_parent_orbit_period_s(bodies) or _shortest_unparented_pair_period_s(bodies)
    if shortest_period is None:
        return DAY
    return max(lower, min(upper, shortest_period * fraction))


def derived_overview_max_step_s(entities: list[OverviewEntity], accuracy_profile: str) -> float:
    lower, upper = _OVERVIEW_PROFILE_CLAMPS_S.get(
        accuracy_profile,
        _OVERVIEW_PROFILE_CLAMPS_S["balanced"],
    )
    shortest_period = _shortest_entity_pair_period_s(entities)
    if shortest_period is None:
        return lower
    return max(lower, min(upper, shortest_period / 32.0))


def recommended_trail_sample_interval_s(visible_step_s: float) -> float:
    return max(DAY, abs(visible_step_s))


def effective_simulation_scope(
    bodies: list[Body],
    requested_scope: str,
    view_mode: str,
    selected_index: int,
    groups: list[SystemGroup] | None = None,
) -> str:
    if requested_scope != "auto":
        return requested_scope
    if view_mode == "log_overview" and len(system_overview_entities(bodies, groups or [])) > 1:
        return "system_overview"
    root_star_count = sum(1 for body in bodies if body.kind == "star" and body.parent_id is None)
    if view_mode == "follow_selected":
        return "focused_subsystem"
    if root_star_count > 1:
        return "stellar_overview"
    return "full_nbody"


def active_body_indices(
    bodies: list[Body],
    requested_scope: str,
    view_mode: str,
    selected_index: int,
    groups: list[SystemGroup] | None = None,
    focus_group_id: str | None = None,
) -> list[int]:
    if not bodies:
        return []
    scope = effective_simulation_scope(bodies, requested_scope, view_mode, selected_index, groups)
    if scope == "full_nbody":
        return list(range(len(bodies)))
    if scope == "stellar_overview":
        root_stars = [
            index
            for index, body in enumerate(bodies)
            if body.kind == "star" and body.parent_id is None
        ]
        return root_stars or list(range(len(bodies)))
    if scope == "focused_subsystem":
        return _focused_subsystem_indices(bodies, selected_index, groups or [], focus_group_id)
    return list(range(len(bodies)))


def system_overview_entities(bodies: list[Body], groups: list[SystemGroup]) -> list[OverviewEntity]:
    if not bodies or not groups:
        return []
    child_group_ids = {group.parent_group_id for group in groups if group.parent_group_id is not None}
    entities: list[OverviewEntity] = []
    for group in groups:
        if not group.body_ids:
            continue
        if group.id in child_group_ids:
            continue
        indices = _body_indices_for_group(bodies, groups, group.id)
        if not indices:
            continue
        entities.append(_overview_entity_for_indices(group, bodies, indices))
    return entities


def _overview_entity_for_indices(
    group: SystemGroup,
    bodies: list[Body],
    indices: list[int],
) -> OverviewEntity:
    total_mass = sum(bodies[index].mass_kg for index in indices)
    if total_mass <= 0.0:
        raise ValueError("overview entity mass must be positive")
    position = [
        sum(bodies[index].mass_kg * bodies[index].position_m[axis] for index in indices) / total_mass
        for axis in range(3)
    ]
    velocity = [
        sum(bodies[index].mass_kg * bodies[index].velocity_mps[axis] for index in indices) / total_mass
        for axis in range(3)
    ]
    color = bodies[indices[0]].color
    return OverviewEntity(
        id=group.id,
        name=group.name,
        kind=group.kind,
        mass_kg=total_mass,
        position_m=position,
        velocity_mps=velocity,
        color=color,
    )


def _body_indices_for_group(bodies: list[Body], groups: list[SystemGroup], group_id: str) -> list[int]:
    group_ids = _descendant_group_ids(groups, group_id)
    if not group_ids:
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
    return [index for index, body in enumerate(bodies) if body.id in body_ids]


def _descendant_group_ids(groups: list[SystemGroup], group_id: str) -> set[str]:
    if not any(group.id == group_id for group in groups):
        return set()
    group_ids = {group_id}
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group.parent_group_id in group_ids and group.id not in group_ids:
                group_ids.add(group.id)
                changed = True
    return group_ids


def collapsed_child_counts(bodies: list[Body], active_indices: list[int]) -> dict[int, int]:
    active_ids = {bodies[index].id for index in active_indices}
    index_by_id = {body.id: index for index, body in enumerate(bodies)}
    counts: dict[int, int] = {}
    for body in bodies:
        if body.parent_id is None or body.id in active_ids:
            continue
        ancestor_id = body.parent_id
        while ancestor_id is not None:
            ancestor_index = index_by_id.get(ancestor_id)
            if ancestor_index is None:
                break
            ancestor = bodies[ancestor_index]
            if ancestor.id in active_ids:
                counts[ancestor_index] = counts.get(ancestor_index, 0) + 1
                break
            ancestor_id = ancestor.parent_id
    return counts


def _shortest_parent_orbit_period_s(bodies: list[Body]) -> float | None:
    bodies_by_id = {body.id: body for body in bodies}
    shortest: float | None = None
    for body in bodies:
        if body.parent_id is None:
            continue
        parent = bodies_by_id.get(body.parent_id)
        if parent is None:
            continue
        radius_m = math.dist(body.position_m, parent.position_m)
        if radius_m <= 0.0 or parent.mass_kg <= 0.0:
            continue
        period_s = math.tau * math.sqrt(radius_m**3 / (G * parent.mass_kg))
        if math.isfinite(period_s):
            shortest = period_s if shortest is None else min(shortest, period_s)
    return shortest


def _shortest_unparented_pair_period_s(bodies: list[Body]) -> float | None:
    root_bodies = [body for body in bodies if body.parent_id is None]
    shortest: float | None = None
    for first_index, first in enumerate(root_bodies):
        for second in root_bodies[first_index + 1:]:
            radius_m = math.dist(first.position_m, second.position_m)
            total_mass_kg = first.mass_kg + second.mass_kg
            if radius_m <= 0.0 or total_mass_kg <= 0.0:
                continue
            period_s = math.tau * math.sqrt(radius_m**3 / (G * total_mass_kg))
            if math.isfinite(period_s):
                shortest = period_s if shortest is None else min(shortest, period_s)
    return shortest


def _shortest_entity_pair_period_s(entities: list[OverviewEntity]) -> float | None:
    shortest: float | None = None
    for first_index, first in enumerate(entities):
        for second in entities[first_index + 1:]:
            radius_m = math.dist(first.position_m, second.position_m)
            total_mass_kg = first.mass_kg + second.mass_kg
            if radius_m <= 0.0 or total_mass_kg <= 0.0:
                continue
            period_s = math.tau * math.sqrt(radius_m**3 / (G * total_mass_kg))
            if math.isfinite(period_s):
                shortest = period_s if shortest is None else min(shortest, period_s)
    return shortest


def _focused_subsystem_indices(
    bodies: list[Body],
    selected_index: int,
    groups: list[SystemGroup],
    focus_group_id: str | None,
) -> list[int]:
    if groups:
        group_indices = _focused_group_indices(bodies, groups, focus_group_id, selected_index)
        if group_indices:
            return group_indices

    selected_index = max(0, min(selected_index, len(bodies) - 1))
    bodies_by_id = {body.id: body for body in bodies}
    selected = bodies[selected_index]
    root = selected
    while root.parent_id is not None:
        parent = bodies_by_id.get(root.parent_id)
        if parent is None:
            break
        root = parent

    selected_ids = {root.id}
    changed = True
    while changed:
        changed = False
        for body in bodies:
            if body.parent_id in selected_ids and body.id not in selected_ids:
                selected_ids.add(body.id)
                changed = True
    return [index for index, body in enumerate(bodies) if body.id in selected_ids]


def _focused_group_indices(
    bodies: list[Body],
    groups: list[SystemGroup],
    focus_group_id: str | None,
    selected_index: int,
) -> list[int]:
    group = _group_for_focus(bodies, groups, focus_group_id, selected_index)
    if group is None:
        return []
    body_ids = set(group.body_ids)
    changed = True
    while changed:
        changed = False
        for body in bodies:
            if body.parent_id in body_ids and body.id not in body_ids:
                body_ids.add(body.id)
                changed = True
    return [index for index, body in enumerate(bodies) if body.id in body_ids]


def _group_for_focus(
    bodies: list[Body],
    groups: list[SystemGroup],
    focus_group_id: str | None,
    selected_index: int,
) -> SystemGroup | None:
    groups_by_id = {group.id: group for group in groups}
    if focus_group_id is not None:
        group = groups_by_id.get(focus_group_id)
        if group is not None:
            return _nearest_group_with_bodies(group, groups)
    if not bodies:
        return None
    selected_index = max(0, min(selected_index, len(bodies) - 1))
    selected_id = bodies[selected_index].id
    body_group = next((group for group in groups if selected_id in group.body_ids), None)
    if body_group is not None:
        return body_group
    ancestor_id = bodies[selected_index].parent_id
    while ancestor_id is not None:
        body_group = next((group for group in groups if ancestor_id in group.body_ids), None)
        if body_group is not None:
            return body_group
        ancestor = next((body for body in bodies if body.id == ancestor_id), None)
        ancestor_id = ancestor.parent_id if ancestor is not None else None
    return None


def _nearest_group_with_bodies(group: SystemGroup, groups: list[SystemGroup]) -> SystemGroup | None:
    if group.body_ids:
        return group
    children_by_parent_id: dict[str | None, list[SystemGroup]] = {}
    for item in groups:
        children_by_parent_id.setdefault(item.parent_group_id, []).append(item)
    pending = list(children_by_parent_id.get(group.id, []))
    while pending:
        child = pending.pop(0)
        if child.body_ids:
            return child
        pending.extend(children_by_parent_id.get(child.id, []))
    return group
