# hierarchy.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free helpers for body and group hierarchy presentation."""

from __future__ import annotations

import math

from .models import Body, SystemGroup
from .scales import distance_between_bodies_m, format_distance

HierarchyRow = tuple[str, str | int]


def group_by_id(groups: list[SystemGroup], group_id: str | None) -> SystemGroup | None:
    if group_id is None:
        return None
    return next((group for group in groups if group.id == group_id), None)


def group_depth(groups: list[SystemGroup], group: SystemGroup | None) -> int:
    if group is None:
        return 0
    groups_by_id = {item.id: item for item in groups}
    depth = 0
    parent_group_id = group.parent_group_id
    while parent_group_id is not None:
        depth += 1
        parent = groups_by_id.get(parent_group_id)
        if parent is None:
            break
        parent_group_id = parent.parent_group_id
    return depth


def descendant_group_ids(
    groups: list[SystemGroup],
    group_id: str,
    *,
    include_self: bool = True,
) -> set[str]:
    if include_self and group_by_id(groups, group_id) is None:
        return set()
    group_ids = {group_id} if include_self else set()
    parent_ids = set(group_ids)
    if not include_self:
        parent_ids.add(group_id)
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group.parent_group_id in parent_ids and group.id not in group_ids:
                group_ids.add(group.id)
                parent_ids.add(group.id)
                changed = True
    return group_ids


def body_indices_for_group(bodies: list[Body], groups: list[SystemGroup], group_id: str) -> list[int]:
    group_ids = descendant_group_ids(groups, group_id)
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


def body_list_rows(bodies: list[Body], groups: list[SystemGroup]) -> list[HierarchyRow]:
    if not groups:
        return [("body", index) for index in body_list_order(bodies)]

    rows: list[HierarchyRow] = []
    group_children: dict[str | None, list[SystemGroup]] = {}
    for group in groups:
        group_children.setdefault(group.parent_group_id, []).append(group)
    body_indices_by_id = {body.id: index for index, body in enumerate(bodies)}
    rendered_body_indices: set[int] = set()

    def append_group(group: SystemGroup) -> None:
        rows.append(("group", group.id))
        for body_id in group.body_ids:
            body_index = body_indices_by_id.get(body_id)
            if body_index is None:
                continue
            for child_index in body_subtree_order(bodies, body_index):
                if child_index not in rendered_body_indices:
                    rows.append(("body", child_index))
                    rendered_body_indices.add(child_index)
        for child_group in sorted(
            group_children.get(group.id, []),
            key=lambda item: item.name.casefold(),
        ):
            append_group(child_group)

    for group in sorted(
        group_children.get(None, []),
        key=lambda item: item.name.casefold(),
    ):
        append_group(group)

    for body_index in body_list_order(bodies):
        if body_index not in rendered_body_indices:
            rows.append(("body", body_index))
    return rows


def body_subtree_order(bodies: list[Body], body_index: int) -> list[int]:
    children_by_parent_id: dict[str | None, list[int]] = {}
    for index, body in enumerate(bodies):
        children_by_parent_id.setdefault(body.parent_id, []).append(index)

    ordered: list[int] = []

    def append_body(index: int) -> None:
        ordered.append(index)
        body = bodies[index]
        for child_index in sorted(
            children_by_parent_id.get(body.id, []),
            key=lambda item: body_sort_key(bodies, index, item),
        ):
            append_body(child_index)

    append_body(body_index)
    return ordered


def body_list_order(bodies: list[Body]) -> list[int]:
    if not bodies:
        return []
    body_index_by_id = {body.id: index for index, body in enumerate(bodies)}
    children_by_parent_id: dict[str | None, list[int]] = {}
    for index, body in enumerate(bodies):
        children_by_parent_id.setdefault(body.parent_id, []).append(index)

    ordered: list[int] = []

    def append_children(parent_id: str | None) -> None:
        parent_index = body_index_by_id.get(parent_id) if parent_id is not None else None
        for child_index in sorted(
            children_by_parent_id.get(parent_id, []),
            key=lambda index: body_sort_key(bodies, parent_index, index),
        ):
            ordered.append(child_index)
            append_children(bodies[child_index].id)

    append_children(None)
    return ordered


def body_sort_key(bodies: list[Body], parent_index: int | None, child_index: int) -> tuple[int, float, str]:
    body = bodies[child_index]
    parent = bodies[parent_index] if parent_index is not None else None
    distance_m = math.dist(body.position_m, parent.position_m) if parent is not None else 0.0
    root_rank = 0 if body.kind == "star" else 1
    return (root_rank, distance_m, body.name.casefold())


def body_row_depth(bodies: list[Body], groups: list[SystemGroup], body_index: int) -> int:
    body = bodies[body_index]
    group = group_for_body_id(bodies, groups, body.id)
    group_depth_value = group_depth(groups, group) + 1 if group is not None else 0
    return group_depth_value + body_depth(bodies, body)


def body_depth(bodies: list[Body], body: Body) -> int:
    bodies_by_id = {item.id: item for item in bodies}
    depth = 0
    parent_id = body.parent_id
    while parent_id is not None:
        depth += 1
        parent = bodies_by_id.get(parent_id)
        if parent is None:
            break
        parent_id = parent.parent_id
    return depth


def group_for_body_id(bodies: list[Body], groups: list[SystemGroup], body_id: str) -> SystemGroup | None:
    group = next((item for item in groups if body_id in item.body_ids), None)
    if group is not None:
        return group
    body = next((item for item in bodies if item.id == body_id), None)
    parent_id = body.parent_id if body is not None else None
    while parent_id is not None:
        group = next((item for item in groups if parent_id in item.body_ids), None)
        if group is not None:
            return group
        parent = next((item for item in bodies if item.id == parent_id), None)
        parent_id = parent.parent_id if parent is not None else None
    return None


def group_id_for_body_index(bodies: list[Body], groups: list[SystemGroup], body_index: int) -> str | None:
    if body_index < 0 or body_index >= len(bodies):
        return None
    group = group_for_body_id(bodies, groups, bodies[body_index].id)
    return group.id if group is not None else None


def nearest_other_star(bodies: list[Body], body: Body) -> Body | None:
    if body.kind != "star":
        return None
    other_stars = [
        other
        for other in bodies
        if other.kind == "star" and other.id != body.id
    ]
    if not other_stars:
        return None
    return min(other_stars, key=lambda other: distance_between_bodies_m(body, other))


def body_relationship_label(bodies: list[Body], body: Body) -> str:
    if body.parent_id is None:
        nearest_star = nearest_other_star(bodies, body)
        if body.kind == "star" and nearest_star is not None:
            distance = distance_between_bodies_m(body, nearest_star)
            return f"star - nearest star {format_distance(distance)}"
        return body.kind
    parent = next((item for item in bodies if item.id == body.parent_id), None)
    if parent is None:
        return body.kind
    distance = distance_between_bodies_m(body, parent)
    return f"{body.kind} - orbits {parent.name} - {format_distance(distance)}"


def group_label(bodies: list[Body], groups: list[SystemGroup], group: SystemGroup) -> str:
    count = len(body_indices_for_group(bodies, groups, group.id))
    label = group.kind.replace("_", " ")
    return f"{label} - {count} bodies"


def group_center(bodies: list[Body], groups: list[SystemGroup], group_id: str) -> tuple[float, float] | None:
    indices = body_indices_for_group(bodies, groups, group_id)
    if not indices:
        return None
    total_mass = sum(bodies[index].mass_kg for index in indices)
    if total_mass <= 0.0:
        return None
    return (
        sum(bodies[index].mass_kg * bodies[index].position_m[0] for index in indices) / total_mass,
        sum(bodies[index].mass_kg * bodies[index].position_m[1] for index in indices) / total_mass,
    )


def selected_distance_rows(bodies: list[Body], body: Body) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if body.parent_id is not None:
        parent = next((item for item in bodies if item.id == body.parent_id), None)
        if parent is not None:
            rows.append(
                (
                    f"Distance to {parent.name}",
                    format_distance(distance_between_bodies_m(body, parent)),
                )
            )
        return rows

    if body.kind != "star":
        return rows

    other_stars = [
        other
        for other in bodies
        if other.kind == "star" and other.id != body.id
    ]
    other_stars.sort(key=lambda other: distance_between_bodies_m(body, other))
    for other in other_stars:
        rows.append(
            (
                f"Distance to {other.name}",
                format_distance(distance_between_bodies_m(body, other)),
            )
        )
    return rows
