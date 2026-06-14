# window.py
#
# Copyright 2026 Jackrabbithanna
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math
import traceback
from concurrent.futures import Future, ThreadPoolExecutor

import gi
import numpy as np

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from .constants import AU, DAY
from .models import Body, SolarSystem, SystemGroup
from .physics import SimulationState, advance_with_samples
from .presets import load_builtin_solar_system, load_builtin_solar_systems
from .scales import (
    ACCURACY_LABELS,
    DISTANCE_UNITS,
    TIME_UNITS,
    VIEW_MODE_LABELS,
    SIMULATION_SCOPE_LABELS,
    OverviewEntity,
    active_body_indices,
    collapsed_child_counts,
    derived_max_step_s,
    derived_overview_max_step_s,
    distance_between_bodies_m,
    effective_simulation_scope,
    context_overview_entities,
    focused_canvas_bounds,
    focused_visible_step_s,
    focus_target_body_indices,
    format_distance,
    format_elapsed_time,
    recommended_trail_sample_interval_s,
    system_overview_entities,
    time_unit_for_seconds,
    unit_factor,
    unit_index,
)
from .storage import Library

TRAIL_POINT_LIMIT = 2000


def _advance_hybrid_simulations(
    focused_state: SimulationState,
    context_state: SimulationState | None,
    dt_s: float,
    focused_max_step_s: float,
    context_max_step_s: float,
):
    focused_result = advance_with_samples(
        focused_state,
        dt_s,
        "post_newtonian",
        focused_max_step_s,
    )
    if context_state is None:
        return focused_result, None
    context_result = advance_with_samples(
        context_state,
        dt_s,
        "post_newtonian",
        context_max_step_s,
    )
    return focused_result, context_result


@Gtk.Template(resource_path="/io/github/jackrabbithanna/solarsystembuilder/window.ui")
class SolarSystemBuilderWindow(Adw.ApplicationWindow):
    __gtype_name__ = "SolarSystemBuilderWindow"

    canvas = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    step_back_button = Gtk.Template.Child()
    reset_button = Gtk.Template.Child()
    step_forward_button = Gtk.Template.Child()
    zoom_out_button = Gtk.Template.Child()
    reset_zoom_button = Gtk.Template.Child()
    zoom_in_button = Gtk.Template.Child()
    save_button = Gtk.Template.Child()
    duplicate_button = Gtk.Template.Child()
    system_dropdown = Gtk.Template.Child()
    body_list = Gtk.Template.Child()
    speed_spin = Gtk.Template.Child()
    time_unit_dropdown = Gtk.Template.Child()
    accuracy_dropdown = Gtk.Template.Child()
    view_mode_dropdown = Gtk.Template.Child()
    simulation_scope_dropdown = Gtk.Template.Child()
    time_label = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    system_name_entry = Gtk.Template.Child()
    delete_system_button = Gtk.Template.Child()
    selected_name_label = Gtk.Template.Child()
    focus_button = Gtk.Template.Child()
    selected_distance_list = Gtk.Template.Child()
    distance_unit_dropdown = Gtk.Template.Child()
    mass_entry = Gtk.Template.Child()
    x_label = Gtk.Template.Child()
    x_spin = Gtk.Template.Child()
    y_label = Gtk.Template.Child()
    y_spin = Gtk.Template.Child()
    vx_spin = Gtk.Template.Child()
    vy_spin = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.library = Library()
        self.systems: list[SolarSystem] = []
        self.system = load_builtin_solar_system()
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.state = SimulationState.from_bodies(self.system.bodies)
        self.selected_index = 0
        self.focus_group_id: str | None = None
        self.focus_target: str | None = None
        self.selected_group_id: str | None = None
        self.body_list_indices: list[int] = []
        self.body_list_rows: list[tuple[str, str | int]] = []
        self.body_relationship_labels: dict[int, Gtk.Label] = {}
        self.playing = False
        self.editing = False
        self.updating_system_dropdown = False
        self.closed = False
        self.simulation_generation = 0
        self.simulation_future: Future | None = None
        self.simulation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation")
        self.trails: list[list[tuple[float, float]]] = [[] for _ in self.system.bodies]
        self.overview_trails: dict[str, list[tuple[float, float]]] = {}
        self.overview_state: SimulationState | None = None
        self.overview_entity_ids: list[str] = []
        self.context_trails: dict[str, list[tuple[float, float]]] = {}
        self.context_state: SimulationState | None = None
        self.context_entity_ids: list[str] = []
        self.last_trail_sample_elapsed_s = self.state.elapsed_s
        self.zoom_factor = 1.0
        self.timer_id = GLib.timeout_add(33, self._tick)

        self._setup_dropdown(self.time_unit_dropdown, TIME_UNITS)
        self._setup_dropdown(self.accuracy_dropdown, ACCURACY_LABELS)
        self._setup_dropdown(self.view_mode_dropdown, VIEW_MODE_LABELS)
        self._setup_dropdown(self.simulation_scope_dropdown, SIMULATION_SCOPE_LABELS)
        self._setup_dropdown(self.distance_unit_dropdown, DISTANCE_UNITS)

        self.canvas.set_draw_func(self._draw)
        self.canvas.set_has_tooltip(True)
        self.canvas.connect("query-tooltip", self._on_canvas_query_tooltip)
        click_controller = Gtk.GestureClick.new()
        click_controller.connect("pressed", self._on_canvas_pressed)
        self.canvas.add_controller(click_controller)
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect("scroll", self._on_canvas_scroll)
        self.canvas.add_controller(scroll_controller)
        self.play_button.connect("clicked", self._on_play_clicked)
        self.step_back_button.connect("clicked", self._on_step_back_clicked)
        self.reset_button.connect("clicked", self._on_reset_clicked)
        self.step_forward_button.connect("clicked", self._on_step_forward_clicked)
        self.zoom_out_button.connect("clicked", self._on_zoom_out_clicked)
        self.reset_zoom_button.connect("clicked", self._on_reset_zoom_clicked)
        self.zoom_in_button.connect("clicked", self._on_zoom_in_clicked)
        self.save_button.connect("clicked", self._on_save_clicked)
        self.duplicate_button.connect("clicked", self._on_duplicate_clicked)
        self.focus_button.connect("clicked", self._on_focus_clicked)
        self.system_dropdown.connect("notify::selected", self._on_system_selected)
        self.body_list.connect("row-selected", self._on_body_selected)
        self.speed_spin.connect("value-changed", self._on_time_step_changed)
        self.time_unit_dropdown.connect("notify::selected", self._on_time_step_changed)
        self.accuracy_dropdown.connect("notify::selected", self._on_accuracy_changed)
        self.view_mode_dropdown.connect("notify::selected", self._on_view_mode_changed)
        self.simulation_scope_dropdown.connect("notify::selected", self._on_simulation_scope_changed)
        self.distance_unit_dropdown.connect("notify::selected", self._on_distance_unit_changed)

        self.system_name_entry.connect("activate", self._on_system_name_edit)
        self.system_name_entry.connect("notify::has-focus", self._on_system_name_focus_changed)
        self.delete_system_button.connect("clicked", self._on_delete_system_clicked)
        self.mass_entry.connect("activate", self._on_body_edit)
        self.mass_entry.connect("notify::has-focus", self._on_mass_focus_changed)
        for spin in (self.x_spin, self.y_spin, self.vx_spin, self.vy_spin):
            spin.connect("value-changed", self._on_body_edit)

        self._reload_system_list()
        self._load_system_editor()
        self._load_settings_editor()
        self._populate_body_list()
        self._select_body(0)
        self._update_title()
        self._update_time_label()
        self._set_zoom_factor(1.0)

    def do_close_request(self):
        self.closed = True
        self.playing = False
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = 0
        self.simulation_executor.shutdown(wait=False, cancel_futures=True)
        return False

    def _reload_system_list(self) -> None:
        saved = self.library.list_systems()
        self.systems = [*load_builtin_solar_systems(), *saved]
        active = next((index for index, system in enumerate(self.systems) if system.id == self.system.id), 0)
        self.updating_system_dropdown = True
        try:
            self._refresh_system_dropdown_labels()
            self.system_dropdown.set_selected(active)
        finally:
            self.updating_system_dropdown = False

    def _refresh_system_dropdown_labels(self) -> None:
        names = [
            self.system.name if system.id == self.system.id else system.name
            for system in self.systems
        ]
        self.system_dropdown.set_model(Gtk.StringList.new(names))

    def _setup_dropdown(self, dropdown, items) -> None:
        dropdown.set_model(Gtk.StringList.new([item[0] for item in items]))

    def _load_system(self, system: SolarSystem) -> None:
        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self._replace_system(system, refresh_snapshot=True)

    def _replace_system(
        self,
        system: SolarSystem,
        refresh_snapshot: bool,
        selected_body_id: str | None = None,
    ) -> None:
        self.system = self._clone_system(system)
        if refresh_snapshot:
            self.loaded_system_snapshot = self._clone_system(self.system)
        self.state = SimulationState.from_bodies(self.system.bodies)
        self.simulation_generation += 1
        self.selected_index = self._body_index_for_id(selected_body_id)
        self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.focus_target = None
        self.selected_group_id = None
        self._clear_dynamic_simulation_state()
        self._populate_body_list()
        self._select_body(self.selected_index)
        self._load_system_editor()
        self._load_settings_editor()
        self._update_title()
        self._update_time_label()
        self.canvas.queue_draw()

    def _body_index_for_id(self, body_id: str | None) -> int:
        if body_id is None:
            return 0
        return next(
            (index for index, body in enumerate(self.system.bodies) if body.id == body_id),
            0,
        )

    def _populate_body_list(self) -> None:
        while child := self.body_list.get_first_child():
            self.body_list.remove(child)
        self.body_relationship_labels = {}
        self.body_list_rows = self._body_list_rows()
        self.body_list_indices = [
            int(row_id)
            for row_type, row_id in self.body_list_rows
            if row_type == "body"
        ]
        for row_type, row_id in self.body_list_rows:
            if row_type == "group":
                self._append_group_row(str(row_id))
                continue
            body_index = int(row_id)
            body = self.system.bodies[body_index]
            self._append_body_row(body_index, self._body_row_depth(body_index))

    def _append_group_row(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10 + self._group_depth(group) * 18)
        box.set_margin_end(10)
        name = Gtk.Label(label=group.name, xalign=0)
        name.set_hexpand(True)
        name.add_css_class("heading")
        kind = Gtk.Label(label=self._group_label(group))
        kind.add_css_class("dim-label")
        box.append(name)
        box.append(kind)
        row.set_child(box)
        self.body_list.append(row)

    def _append_body_row(self, body_index: int, depth: int) -> None:
        body = self.system.bodies[body_index]
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10 + depth * 18)
        box.set_margin_end(10)
        swatch = Gtk.DrawingArea()
        swatch.set_content_width(14)
        swatch.set_content_height(14)
        swatch.set_draw_func(self._draw_swatch, body.color)
        name = Gtk.Label(label=body.name, xalign=0)
        name.set_hexpand(True)
        kind = Gtk.Label(label=self._body_relationship_label(body))
        kind.add_css_class("dim-label")
        self.body_relationship_labels[body_index] = kind
        box.append(swatch)
        box.append(name)
        box.append(kind)
        row.set_child(box)
        self.body_list.append(row)

    def _body_list_rows(self) -> list[tuple[str, str | int]]:
        if not self.system.groups:
            return [("body", index) for index in self._body_list_order()]

        rows: list[tuple[str, str | int]] = []
        group_children: dict[str | None, list[SystemGroup]] = {}
        for group in self.system.groups:
            group_children.setdefault(group.parent_group_id, []).append(group)
        body_indices_by_id = {
            body.id: index
            for index, body in enumerate(self.system.bodies)
        }
        rendered_body_indices: set[int] = set()

        def append_group(group: SystemGroup) -> None:
            rows.append(("group", group.id))
            for body_id in group.body_ids:
                body_index = body_indices_by_id.get(body_id)
                if body_index is None:
                    continue
                for child_index in self._body_subtree_order(body_index):
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

        for body_index in self._body_list_order():
            if body_index not in rendered_body_indices:
                rows.append(("body", body_index))
        return rows

    def _body_subtree_order(self, body_index: int) -> list[int]:
        children_by_parent_id: dict[str | None, list[int]] = {}
        for index, body in enumerate(self.system.bodies):
            children_by_parent_id.setdefault(body.parent_id, []).append(index)

        ordered: list[int] = []

        def append_body(index: int) -> None:
            ordered.append(index)
            body = self.system.bodies[index]
            for child_index in sorted(
                children_by_parent_id.get(body.id, []),
                key=lambda item: self._body_sort_key(index, item),
            ):
                append_body(child_index)

        append_body(body_index)
        return ordered

    def _body_sort_key(self, parent_index: int | None, child_index: int) -> tuple[int, float, str]:
        body = self.system.bodies[child_index]
        parent = self.system.bodies[parent_index] if parent_index is not None else None
        distance_m = math.dist(body.position_m, parent.position_m) if parent is not None else 0.0
        root_rank = 0 if body.kind == "star" else 1
        return (root_rank, distance_m, body.name.casefold())

    def _body_list_order(self) -> list[int]:
        if not self.system.bodies:
            return []
        body_index_by_id = {
            body.id: index
            for index, body in enumerate(self.system.bodies)
        }
        children_by_parent_id: dict[str | None, list[int]] = {}
        for index, body in enumerate(self.system.bodies):
            children_by_parent_id.setdefault(body.parent_id, []).append(index)

        ordered: list[int] = []

        def append_children(parent_id: str | None) -> None:
            parent_index = body_index_by_id.get(parent_id) if parent_id is not None else None
            for child_index in sorted(
                children_by_parent_id.get(parent_id, []),
                key=lambda index: self._body_sort_key(parent_index, index),
            ):
                ordered.append(child_index)
                append_children(self.system.bodies[child_index].id)

        append_children(None)
        return ordered

    def _body_row_depth(self, body_index: int) -> int:
        body = self.system.bodies[body_index]
        group = self._group_for_body_id(body.id)
        group_depth = self._group_depth(group) + 1 if group is not None else 0
        return group_depth + self._body_depth(body)

    def _body_depth(self, body: Body) -> int:
        bodies_by_id = {item.id: item for item in self.system.bodies}
        depth = 0
        parent_id = body.parent_id
        while parent_id is not None:
            depth += 1
            parent = bodies_by_id.get(parent_id)
            if parent is None:
                break
            parent_id = parent.parent_id
        return depth

    def _body_relationship_label(self, body: Body) -> str:
        if body.parent_id is None:
            nearest_star = self._nearest_other_star(body)
            if body.kind == "star" and nearest_star is not None:
                distance = distance_between_bodies_m(body, nearest_star)
                return f"star - nearest star {format_distance(distance)}"
            return body.kind
        parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)
        if parent is None:
            return body.kind
        distance = distance_between_bodies_m(body, parent)
        return f"orbits {parent.name} - {format_distance(distance)}"

    def _group_label(self, group: SystemGroup) -> str:
        count = len(self._body_indices_for_group(group.id))
        label = group.kind.replace("_", " ")
        return f"{label} - {count} bodies"

    def _group_by_id(self, group_id: str | None) -> SystemGroup | None:
        if group_id is None:
            return None
        return next((group for group in self.system.groups if group.id == group_id), None)

    def _group_depth(self, group: SystemGroup | None) -> int:
        if group is None:
            return 0
        groups_by_id = {item.id: item for item in self.system.groups}
        depth = 0
        parent_group_id = group.parent_group_id
        while parent_group_id is not None:
            depth += 1
            parent = groups_by_id.get(parent_group_id)
            if parent is None:
                break
            parent_group_id = parent.parent_group_id
        return depth

    def _group_for_body_id(self, body_id: str) -> SystemGroup | None:
        group = next((item for item in self.system.groups if body_id in item.body_ids), None)
        if group is not None:
            return group
        body = next((item for item in self.system.bodies if item.id == body_id), None)
        parent_id = body.parent_id if body is not None else None
        while parent_id is not None:
            group = next((item for item in self.system.groups if parent_id in item.body_ids), None)
            if group is not None:
                return group
            parent = next((item for item in self.system.bodies if item.id == parent_id), None)
            parent_id = parent.parent_id if parent is not None else None
        return None

    def _group_id_for_body_index(self, body_index: int) -> str | None:
        if body_index < 0 or body_index >= len(self.system.bodies):
            return None
        group = self._group_for_body_id(self.system.bodies[body_index].id)
        return group.id if group is not None else None

    def _body_indices_for_group(self, group_id: str) -> list[int]:
        group_ids = self._descendant_group_ids(group_id)
        if not group_ids:
            return []
        body_ids: set[str] = set()
        for group in self.system.groups:
            if group.id in group_ids:
                body_ids.update(group.body_ids)
        changed = True
        while changed:
            changed = False
            for body in self.system.bodies:
                if body.parent_id in body_ids and body.id not in body_ids:
                    body_ids.add(body.id)
                    changed = True
        return [
            index
            for index, body in enumerate(self.system.bodies)
            if body.id in body_ids
        ]

    def _descendant_group_ids(self, group_id: str) -> set[str]:
        if self._group_by_id(group_id) is None:
            return set()
        group_ids = {group_id}
        changed = True
        while changed:
            changed = False
            for group in self.system.groups:
                if group.parent_group_id in group_ids and group.id not in group_ids:
                    group_ids.add(group.id)
                    changed = True
        return group_ids

    def _nearest_other_star(self, body: Body) -> Body | None:
        if body.kind != "star":
            return None
        other_stars = [
            other
            for other in self.system.bodies
            if other.kind == "star" and other.id != body.id
        ]
        if not other_stars:
            return None
        return min(other_stars, key=lambda other: distance_between_bodies_m(body, other))

    def _refresh_body_relationship_labels(self) -> None:
        for body_index, label in self.body_relationship_labels.items():
            if body_index < len(self.system.bodies):
                label.set_label(self._body_relationship_label(self.system.bodies[body_index]))

    def _select_body(self, index: int) -> None:
        if not self.system.bodies:
            return
        self.selected_index = max(0, min(index, len(self.system.bodies) - 1))
        self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.selected_group_id = None
        row_index = next(
            (
                list_index
                for list_index, (row_type, row_id) in enumerate(self.body_list_rows)
                if row_type == "body" and int(row_id) == self.selected_index
            ),
            self.selected_index,
        )
        row = self.body_list.get_row_at_index(row_index)
        if row:
            self.body_list.select_row(row)
        self._load_body_editor(self.system.bodies[self.selected_index])

    def _select_group(self, group_id: str) -> None:
        if self._group_by_id(group_id) is None:
            return
        row_index = next(
            (
                list_index
                for list_index, (row_type, row_id) in enumerate(self.body_list_rows)
                if row_type == "group" and str(row_id) == group_id
            ),
            None,
        )
        if row_index is not None:
            row = self.body_list.get_row_at_index(row_index)
            if row:
                self.body_list.select_row(row)
        if self.selected_group_id != group_id:
            self.focus_target = None
            self.selected_group_id = group_id
            self._load_group_focus(group_id)
            self._update_title()
        self.canvas.queue_draw()

    def _load_body_editor(self, body: Body) -> None:
        self.editing = True
        distance_factor = self._distance_factor()
        self._set_body_editor_sensitive(True)
        self.selected_group_id = None
        self.selected_name_label.set_label(body.name)
        self._configure_focus_button(self._body_focus_target(body))
        self._populate_selected_distance_list(body)
        self.mass_entry.set_text(f"{body.mass_kg:.12g}")
        self._configure_position_spins()
        self.x_spin.set_value(body.position_m[0] / distance_factor)
        self.y_spin.set_value(body.position_m[1] / distance_factor)
        self.vx_spin.set_value(body.velocity_mps[0])
        self.vy_spin.set_value(body.velocity_mps[1])
        self.editing = False

    def _load_group_focus(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        self.focus_group_id = group.id
        self.editing = True
        self._set_body_editor_sensitive(False)
        self.selected_name_label.set_label(group.name)
        self._configure_focus_button(f"group:{group.id}")
        while child := self.selected_distance_list.get_first_child():
            self.selected_distance_list.remove(child)
        self.selected_distance_list.set_visible(False)
        self.mass_entry.set_text("")
        self.x_spin.set_value(0.0)
        self.y_spin.set_value(0.0)
        self.vx_spin.set_value(0.0)
        self.vy_spin.set_value(0.0)
        self.editing = False

    def _configure_focus_button(self, target: str | None) -> None:
        if target is None:
            self.focus_button.set_visible(False)
            return
        active_indices = focus_target_body_indices(self.system.bodies, self.system.groups, target)
        self.focus_button.set_visible(bool(active_indices))

    def _body_focus_target(self, body: Body) -> str | None:
        if any(candidate.parent_id == body.id for candidate in self.system.bodies):
            return f"body:{body.id}"
        return None

    def _clear_dynamic_simulation_state(self) -> None:
        self.trails = [[] for _ in self.system.bodies]
        self.overview_trails = {}
        self.overview_state = None
        self.overview_entity_ids = []
        self.context_trails = {}
        self.context_state = None
        self.context_entity_ids = []
        self.last_trail_sample_elapsed_s = self.state.elapsed_s

    def _set_body_editor_sensitive(self, sensitive: bool) -> None:
        for widget in (self.mass_entry, self.x_spin, self.y_spin, self.vx_spin, self.vy_spin):
            widget.set_sensitive(sensitive)

    def _populate_selected_distance_list(self, body: Body) -> None:
        while child := self.selected_distance_list.get_first_child():
            self.selected_distance_list.remove(child)

        rows = self._selected_distance_rows(body)
        self.selected_distance_list.set_visible(bool(rows))
        for label, value in rows:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            name = Gtk.Label(label=label, xalign=0)
            name.set_hexpand(True)
            name.add_css_class("dim-label")
            distance = Gtk.Label(label=value, xalign=1)
            box.append(name)
            box.append(distance)
            row.set_child(box)
            self.selected_distance_list.append(row)

    def _selected_distance_rows(self, body: Body) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        if body.parent_id is not None:
            parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)
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
            for other in self.system.bodies
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

    def _load_system_editor(self) -> None:
        self.editing = True
        self.system_name_entry.set_text(self.system.name)
        editable = self._system_is_user_saved()
        self.system_name_entry.set_sensitive(editable)
        self.delete_system_button.set_sensitive(editable)
        self.editing = False

    def _load_settings_editor(self) -> None:
        self.editing = True
        try:
            time_unit = time_unit_for_seconds(self.system.settings.visible_step_s)
            time_factor = unit_factor(TIME_UNITS, time_unit)
            self.speed_spin.set_value(self.system.settings.visible_step_s / time_factor)
            self.time_unit_dropdown.set_selected(unit_index(TIME_UNITS, time_unit))
            self.accuracy_dropdown.set_selected(
                unit_index(ACCURACY_LABELS, self.system.settings.accuracy_profile)
            )
            self.view_mode_dropdown.set_selected(
                unit_index(VIEW_MODE_LABELS, self.system.settings.view_mode)
            )
            self.simulation_scope_dropdown.set_selected(
                unit_index(SIMULATION_SCOPE_LABELS, self.system.settings.simulation_scope)
            )
            self.distance_unit_dropdown.set_selected(
                unit_index(DISTANCE_UNITS, self.system.settings.distance_unit)
            )
            self._configure_position_spins()
        finally:
            self.editing = False

    def _system_is_user_saved(self) -> bool:
        return not self.system.id.startswith("builtin-")

    def _on_body_edit(self, *_args) -> None:
        if self.editing or not self.system.bodies:
            return
        selected_body_id = self.system.bodies[self.selected_index].id
        body = self.system.bodies[self.selected_index]
        try:
            mass = float(self.mass_entry.get_text())
        except ValueError:
            return
        if mass <= 0.0:
            return
        distance_factor = self._distance_factor()
        body.mass_kg = mass
        body.position_m[0] = self.x_spin.get_value() * distance_factor
        body.position_m[1] = self.y_spin.get_value() * distance_factor
        body.velocity_mps[0] = self.vx_spin.get_value()
        body.velocity_mps[1] = self.vy_spin.get_value()
        self.state = SimulationState.from_bodies(self.system.bodies)
        self.simulation_generation += 1
        self._clear_dynamic_simulation_state()
        self._populate_body_list()
        self._select_body(self._body_index_for_id(selected_body_id))
        self._update_title()
        self.canvas.queue_draw()

    def _on_mass_focus_changed(self, entry, _param) -> None:
        if not entry.has_focus():
            self._on_body_edit()

    def _on_system_name_focus_changed(self, entry, _param) -> None:
        if not entry.has_focus():
            self._on_system_name_edit()

    def _on_system_name_edit(self, *_args) -> None:
        if self.editing:
            return
        if not self._system_is_user_saved():
            self._load_system_editor()
            return
        name = self.system_name_entry.get_text().strip()
        if not name:
            self._load_system_editor()
            return
        if name == self.system.name:
            return
        selected = self.system_dropdown.get_selected()
        self.system.name = name
        self._update_title()
        self.updating_system_dropdown = True
        try:
            self._refresh_system_dropdown_labels()
            if selected != Gtk.INVALID_LIST_POSITION:
                self.system_dropdown.set_selected(selected)
        finally:
            self.updating_system_dropdown = False

    def _on_time_step_changed(self, *_args) -> None:
        if self.editing:
            return
        self.system.settings.visible_step_s = self._step_seconds()
        self.system.settings.trail_sample_interval_s = recommended_trail_sample_interval_s(
            self.system.settings.visible_step_s
        )
        self._update_title()

    def _on_accuracy_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(ACCURACY_LABELS):
            self.system.settings.accuracy_profile = ACCURACY_LABELS[selected][1]
            self._update_title()

    def _on_view_mode_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(VIEW_MODE_LABELS):
            self.system.settings.view_mode = VIEW_MODE_LABELS[selected][1]
            self.focus_target = None
            self._clear_dynamic_simulation_state()
            self._update_title()
            self.canvas.queue_draw()

    def _on_simulation_scope_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(SIMULATION_SCOPE_LABELS):
            self.system.settings.simulation_scope = SIMULATION_SCOPE_LABELS[selected][1]
            self._clear_dynamic_simulation_state()
            self._update_title()
            self.canvas.queue_draw()

    def _on_distance_unit_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(DISTANCE_UNITS):
            self.system.settings.distance_unit = DISTANCE_UNITS[selected][1]
            if self.system.bodies:
                self._load_body_editor(self.system.bodies[self.selected_index])

    def _on_focus_clicked(self, _button) -> None:
        target = f"group:{self.selected_group_id}" if self.selected_group_id is not None else None
        if target is None and self.system.bodies:
            target = self._body_focus_target(self.system.bodies[self.selected_index])
        if target is None:
            return

        active_indices = focus_target_body_indices(self.system.bodies, self.system.groups, target)
        if not active_indices:
            return

        self.focus_target = target
        if target.startswith("group:"):
            self.focus_group_id = target.removeprefix("group:")
        else:
            self.focus_group_id = None
        focused_bodies = [self.system.bodies[index] for index in active_indices]
        self.system.settings.visible_step_s = focused_visible_step_s(
            focused_bodies,
            self.system.settings.accuracy_profile,
        )
        self.system.settings.trail_sample_interval_s = recommended_trail_sample_interval_s(
            self.system.settings.visible_step_s
        )
        self.system.settings.view_mode = "follow_selected"
        self.system.settings.simulation_scope = "auto"
        self.zoom_factor = 1.0
        self._clear_dynamic_simulation_state()
        self._load_settings_editor()
        self._update_title()
        self.canvas.queue_draw()

    def _on_delete_system_clicked(self, _button) -> None:
        if not self._system_is_user_saved():
            return

        system_id = self.system.id
        dialog = Adw.AlertDialog.new(
            "Delete Saved System?",
            f"'{self.system.name}' will be permanently deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.choose(
            self,
            None,
            lambda dialog, result, *_args: self._on_delete_system_response(dialog, result, system_id),
        )

    def _on_delete_system_response(self, dialog, result, system_id: str) -> None:
        if dialog.choose_finish(result) != "delete":
            return
        if system_id.startswith("builtin-"):
            return

        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.library.delete(system_id)
        self._reload_system_list()
        self._load_system(load_builtin_solar_system())

    def _on_body_selected(self, _list_box, row) -> None:
        if row is None:
            return
        row_index = row.get_index()
        if row_index < 0 or row_index >= len(self.body_list_rows):
            return
        row_type, row_id = self.body_list_rows[row_index]
        if row_type == "group":
            group_id = str(row_id)
            if group_id != self.selected_group_id:
                self.focus_target = None
                self.selected_group_id = group_id
                self._load_group_focus(group_id)
                self._update_title()
                self.canvas.queue_draw()
            return

        body_index = int(row_id)
        if body_index != self.selected_index or self.selected_group_id is not None:
            self.focus_target = None
            self.selected_group_id = None
            self.selected_index = body_index
            self.focus_group_id = self._group_id_for_body_index(body_index)
            self._load_body_editor(self.system.bodies[body_index])
            self._update_title()
            self.canvas.queue_draw()

    def _on_system_selected(self, dropdown, _param) -> None:
        if self.updating_system_dropdown:
            return
        selected = dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION or selected >= len(self.systems):
            return
        chosen = self.systems[selected]
        if chosen.id != self.system.id:
            self._load_system(chosen)

    def _on_play_clicked(self, _button) -> None:
        self.playing = not self.playing
        icon = "media-playback-pause-symbolic" if self.playing else "media-playback-start-symbolic"
        self.play_button.set_icon_name(icon)
        if self.playing:
            self._queue_simulation_job()

    def _on_step_back_clicked(self, _button) -> None:
        self._advance(-self._step_seconds())

    def _on_step_forward_clicked(self, _button) -> None:
        self._advance(self._step_seconds())

    def _on_zoom_out_clicked(self, _button) -> None:
        self._set_zoom_factor(self.zoom_factor / 1.5)

    def _on_reset_zoom_clicked(self, _button) -> None:
        self._set_zoom_factor(1.0)

    def _on_zoom_in_clicked(self, _button) -> None:
        self._set_zoom_factor(self.zoom_factor * 1.5)

    def _on_canvas_scroll(self, _controller, _dx: float, dy: float) -> bool:
        if dy < 0.0:
            self._set_zoom_factor(self.zoom_factor * 1.5)
            return True
        if dy > 0.0:
            self._set_zoom_factor(self.zoom_factor / 1.5)
            return True
        return False

    def _on_canvas_query_tooltip(self, _widget, x: int, y: int, _keyboard_mode: bool, tooltip) -> bool:
        body_index = self._body_index_at_canvas_point(float(x), float(y))
        if body_index is not None:
            tooltip.set_text(self.system.bodies[body_index].name)
            return True
        entity = self._overview_entity_at_canvas_point(float(x), float(y))
        if entity is not None:
            tooltip.set_text(entity.name)
            return True
        return False

    def _on_canvas_pressed(self, _gesture, _n_press: int, x: float, y: float) -> None:
        body_index = self._body_index_at_canvas_point(x, y)
        if body_index is not None:
            self._select_body(body_index)
            self.canvas.queue_draw()
            return
        entity = self._overview_entity_at_canvas_point(x, y)
        if entity is not None and self._group_by_id(entity.id) is not None:
            self._select_group(entity.id)

    def _set_zoom_factor(self, zoom_factor: float) -> None:
        self.zoom_factor = max(1.0, min(64.0, zoom_factor))
        self.zoom_out_button.set_sensitive(self.zoom_factor > 1.0)
        self.reset_zoom_button.set_sensitive(self.zoom_factor > 1.0)
        self.zoom_in_button.set_sensitive(self.zoom_factor < 64.0)
        self.canvas.queue_draw()

    def _on_reset_clicked(self, _button) -> None:
        selected_body_id = None
        if self.system.bodies:
            selected_body_id = self.system.bodies[self.selected_index].id
        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self._replace_system(
            self.loaded_system_snapshot,
            refresh_snapshot=False,
            selected_body_id=selected_body_id,
        )

    def _on_save_clicked(self, _button) -> None:
        if self.system.id.startswith("builtin-"):
            self.system = self.system.duplicate()
        self.state.apply_to_bodies(self.system.bodies)
        self.library.save(self.system)
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.simulation_generation += 1
        self._reload_system_list()
        self._load_system_editor()
        self._update_title()

    def _on_duplicate_clicked(self, _button) -> None:
        self.state.apply_to_bodies(self.system.bodies)
        duplicate = self.system.duplicate()
        self.library.save(duplicate)
        self._reload_system_list()
        self._load_system(duplicate)

    def _clone_system(self, system: SolarSystem) -> SolarSystem:
        return SolarSystem.from_dict(system.to_dict())

    def _tick(self) -> bool:
        if self.playing:
            self._queue_simulation_job()
        return True

    def _advance(self, dt_s: float) -> None:
        if self._using_system_overview():
            state, position_samples = advance_with_samples(
                self._overview_simulation_state(),
                dt_s,
                "post_newtonian",
                self._max_step_seconds(),
            )
            self._apply_overview_simulation_state(state, position_samples, self.state.elapsed_s)
            return

        if self._using_hybrid_focus():
            active_indices = self._active_body_indices()
            active_state = self._simulation_state_for_indices(active_indices)
            context_state = self._context_simulation_state()
            focused_result, context_result = _advance_hybrid_simulations(
                active_state,
                context_state,
                dt_s,
                self._focused_max_step_seconds(),
                self._context_max_step_seconds(),
            )
            state, position_samples = focused_result
            self._apply_hybrid_simulation_state(
                state,
                position_samples,
                active_indices,
                context_result,
                self.state.elapsed_s,
            )
            return

        active_indices = self._active_body_indices()
        active_state = self._simulation_state_for_indices(active_indices)
        state, position_samples = advance_with_samples(
            active_state,
            dt_s,
            "post_newtonian",
            self._max_step_seconds(),
        )
        self._apply_simulation_state(state, position_samples, active_indices, self.state.elapsed_s)

    def _queue_simulation_job(self) -> None:
        if self.closed or self.simulation_future is not None:
            return

        dt_s = self._step_seconds()
        generation = self.simulation_generation
        if self._using_system_overview():
            mode = "system_overview"
            active_indices = []
            state = self._overview_simulation_state()
            self.simulation_future = self.simulation_executor.submit(
                advance_with_samples,
                state,
                dt_s,
                "post_newtonian",
                self._max_step_seconds(),
            )
            start_elapsed_s = state.elapsed_s
        elif self._using_hybrid_focus():
            mode = "hybrid_focus"
            active_indices = self._active_body_indices()
            state = self._simulation_state_for_indices(active_indices)
            context_state = self._context_simulation_state()
            self.simulation_future = self.simulation_executor.submit(
                _advance_hybrid_simulations,
                state,
                context_state,
                dt_s,
                self._focused_max_step_seconds(),
                self._context_max_step_seconds(),
            )
            start_elapsed_s = state.elapsed_s
        else:
            mode = "body_detail"
            active_indices = self._active_body_indices()
            state = self._simulation_state_for_indices(active_indices)
            self.simulation_future = self.simulation_executor.submit(
                advance_with_samples,
                state,
                dt_s,
                "post_newtonian",
                self._max_step_seconds(),
            )
            start_elapsed_s = state.elapsed_s
        self.simulation_future.add_done_callback(
            lambda future: GLib.idle_add(
                self._finish_simulation_job,
                future,
                generation,
                mode,
                active_indices,
                start_elapsed_s,
            )
        )

    def _finish_simulation_job(
        self,
        future: Future,
        generation: int,
        mode: str,
        active_indices: list[int],
        start_elapsed_s: float,
    ) -> bool:
        if future is self.simulation_future:
            self.simulation_future = None

        if self.closed:
            return False

        try:
            result = future.result()
        except Exception:
            traceback.print_exc()
            self.playing = False
            self.play_button.set_icon_name("media-playback-start-symbolic")
            return False

        if generation == self.simulation_generation and mode == "system_overview":
            state, position_samples = result
            self._apply_overview_simulation_state(state, position_samples, start_elapsed_s)
        elif generation == self.simulation_generation and mode == "hybrid_focus":
            focused_result, context_result = result
            state, position_samples = focused_result
            self._apply_hybrid_simulation_state(
                state,
                position_samples,
                active_indices,
                context_result,
                start_elapsed_s,
            )
        elif generation == self.simulation_generation:
            state, position_samples = result
            self._apply_simulation_state(state, position_samples, active_indices, start_elapsed_s)

        if self.playing:
            self._queue_simulation_job()

        return False

    def _apply_simulation_state(
        self,
        state: SimulationState,
        position_samples,
        active_indices: list[int],
        start_elapsed_s: float,
    ) -> None:
        self._merge_active_state(state, active_indices)
        self.overview_trails = {}
        self.overview_state = None
        self.overview_entity_ids = []
        self.state.apply_to_bodies(self.system.bodies)
        self._append_trails(position_samples, active_indices, start_elapsed_s, state.elapsed_s)
        self._refresh_body_relationship_labels()
        if self.selected_group_id is not None:
            self._load_group_focus(self.selected_group_id)
        else:
            self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_time_label()
        self.canvas.queue_draw()

    def _apply_hybrid_simulation_state(
        self,
        state: SimulationState,
        position_samples,
        active_indices: list[int],
        context_result,
        start_elapsed_s: float,
    ) -> None:
        self._merge_active_state(state, active_indices)
        self.overview_trails = {}
        self.overview_state = None
        self.overview_entity_ids = []
        self.state.apply_to_bodies(self.system.bodies)
        if context_result is not None:
            context_state, context_samples = context_result
            self.context_state = context_state
            self._append_context_trails(context_samples, start_elapsed_s, context_state.elapsed_s)
        self._append_trails(position_samples, active_indices, start_elapsed_s, state.elapsed_s)
        self._refresh_body_relationship_labels()
        if self.selected_group_id is not None:
            self._load_group_focus(self.selected_group_id)
        else:
            self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_time_label()
        self.canvas.queue_draw()

    def _apply_overview_simulation_state(self, state: SimulationState, position_samples, start_elapsed_s: float) -> None:
        self.overview_state = state
        self.state.elapsed_s = state.elapsed_s
        self._append_overview_trails(position_samples, start_elapsed_s, state.elapsed_s)
        if self.selected_group_id is not None:
            self._load_group_focus(self.selected_group_id)
        else:
            self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_time_label()
        self.canvas.queue_draw()

    def _step_seconds(self) -> float:
        selected = self.time_unit_dropdown.get_selected()
        unit = TIME_UNITS[selected][1] if selected < len(TIME_UNITS) else "days"
        return self.speed_spin.get_value() * unit_factor(TIME_UNITS, unit)

    def _max_step_seconds(self) -> float:
        if self._using_system_overview():
            return derived_overview_max_step_s(self._overview_entities(), self.system.settings.accuracy_profile)
        if self._using_hybrid_focus():
            return self._focused_max_step_seconds()
        active_bodies = [self.system.bodies[index] for index in self._active_body_indices()]
        return derived_max_step_s(active_bodies, self.system.settings.accuracy_profile)

    def _focused_max_step_seconds(self) -> float:
        active_bodies = [self.system.bodies[index] for index in self._active_body_indices()]
        return derived_max_step_s(active_bodies, self.system.settings.accuracy_profile)

    def _context_max_step_seconds(self) -> float:
        entities = self._context_entities()
        if not entities:
            return self._focused_max_step_seconds()
        return derived_overview_max_step_s(entities, self.system.settings.accuracy_profile)

    def _append_trails(
        self,
        position_samples,
        active_indices: list[int],
        start_elapsed_s: float,
        end_elapsed_s: float,
    ) -> None:
        if not position_samples:
            return
        sample_count = len(position_samples)
        elapsed_delta = end_elapsed_s - start_elapsed_s
        direction = 1.0 if elapsed_delta >= 0.0 else -1.0
        interval_s = self.system.settings.trail_sample_interval_s
        next_sample_elapsed_s = self.last_trail_sample_elapsed_s + direction * interval_s

        selected_samples = []
        for sample_index, positions_m in enumerate(position_samples, start=1):
            sample_elapsed_s = start_elapsed_s + elapsed_delta * sample_index / sample_count
            if direction > 0.0 and sample_elapsed_s + 1.0e-6 < next_sample_elapsed_s:
                continue
            if direction < 0.0 and sample_elapsed_s - 1.0e-6 > next_sample_elapsed_s:
                continue
            selected_samples.append(positions_m)
            self.last_trail_sample_elapsed_s = sample_elapsed_s
            next_sample_elapsed_s = self.last_trail_sample_elapsed_s + direction * interval_s

        if not selected_samples:
            return
        for sample_index, body_index in enumerate(active_indices):
            body = self.system.bodies[body_index]
            if not body.trail_enabled:
                continue
            trail = self.trails[body_index]
            for positions_m in selected_samples:
                trail.append((float(positions_m[sample_index][0]), float(positions_m[sample_index][1])))
            if len(trail) > TRAIL_POINT_LIMIT:
                del trail[: len(trail) - TRAIL_POINT_LIMIT]

    def _append_overview_trails(self, position_samples, start_elapsed_s: float, end_elapsed_s: float) -> None:
        if not position_samples:
            return
        entities = self._overview_entities()
        sample_count = len(position_samples)
        elapsed_delta = end_elapsed_s - start_elapsed_s
        direction = 1.0 if elapsed_delta >= 0.0 else -1.0
        interval_s = self.system.settings.trail_sample_interval_s
        next_sample_elapsed_s = self.last_trail_sample_elapsed_s + direction * interval_s

        selected_samples = []
        for sample_index, positions_m in enumerate(position_samples, start=1):
            sample_elapsed_s = start_elapsed_s + elapsed_delta * sample_index / sample_count
            if direction > 0.0 and sample_elapsed_s + 1.0e-6 < next_sample_elapsed_s:
                continue
            if direction < 0.0 and sample_elapsed_s - 1.0e-6 > next_sample_elapsed_s:
                continue
            selected_samples.append(positions_m)
            self.last_trail_sample_elapsed_s = sample_elapsed_s
            next_sample_elapsed_s = self.last_trail_sample_elapsed_s + direction * interval_s

        for entity_index, entity in enumerate(entities):
            trail = self.overview_trails.setdefault(entity.id, [])
            for positions_m in selected_samples:
                trail.append((float(positions_m[entity_index][0]), float(positions_m[entity_index][1])))
            if len(trail) > TRAIL_POINT_LIMIT:
                del trail[: len(trail) - TRAIL_POINT_LIMIT]

    def _append_context_trails(self, position_samples, start_elapsed_s: float, end_elapsed_s: float) -> None:
        if not position_samples:
            return
        entities = self._context_entities()
        sample_count = len(position_samples)
        elapsed_delta = end_elapsed_s - start_elapsed_s
        direction = 1.0 if elapsed_delta >= 0.0 else -1.0
        interval_s = self.system.settings.trail_sample_interval_s
        next_sample_elapsed_s = self.last_trail_sample_elapsed_s + direction * interval_s

        selected_samples = []
        for sample_index, positions_m in enumerate(position_samples, start=1):
            sample_elapsed_s = start_elapsed_s + elapsed_delta * sample_index / sample_count
            if direction > 0.0 and sample_elapsed_s + 1.0e-6 < next_sample_elapsed_s:
                continue
            if direction < 0.0 and sample_elapsed_s - 1.0e-6 > next_sample_elapsed_s:
                continue
            selected_samples.append(positions_m)

        for entity_index, entity in enumerate(entities):
            trail = self.context_trails.setdefault(entity.id, [])
            for positions_m in selected_samples:
                trail.append((float(positions_m[entity_index][0]), float(positions_m[entity_index][1])))
            if len(trail) > TRAIL_POINT_LIMIT:
                del trail[: len(trail) - TRAIL_POINT_LIMIT]

    def _update_title(self) -> None:
        self.window_title.set_title(self.system.name)
        max_step_days = self._max_step_seconds() / DAY
        scope = self._effective_simulation_scope().replace("_", " ")
        self.window_title.set_subtitle(f"{self.system.epoch} - {scope}, max step {max_step_days:,.2f} days")

    def _update_time_label(self) -> None:
        self.time_label.set_label(f"Simulation time: {format_elapsed_time(self.state.elapsed_s)}")

    def _draw(self, _area, cr, width: int, height: int) -> None:
        cr.set_source_rgb(0.02, 0.025, 0.032)
        cr.paint()
        if not self.system.bodies:
            return
        if self._using_system_overview():
            self._draw_system_overview(cr, width, height)
            return

        center_x_m, center_y_m = self._view_center()
        scale = self._canvas_scale(width, height, center_x_m, center_y_m)
        origin_x = width / 2.0
        origin_y = height / 2.0
        active_indices = set(self._active_body_indices())

        cr.set_line_width(1.0)
        for index, trail in enumerate(self.trails):
            if index not in active_indices:
                continue
            if len(trail) < 2:
                continue
            rgba = self._rgba(self.system.bodies[index].color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.35)
            first_x, first_y = self._project(trail[0][0], trail[0][1], origin_x, origin_y, scale, center_x_m, center_y_m)
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                x, y = self._project(point_x, point_y, origin_x, origin_y, scale, center_x_m, center_y_m)
                cr.line_to(x, y)
            cr.stroke()

        for index, body in enumerate(self.system.bodies):
            if index not in active_indices:
                continue
            if not body.visible:
                continue
            x, y = self._project(body.position_m[0], body.position_m[1], origin_x, origin_y, scale, center_x_m, center_y_m)
            radius = self._display_radius(body)
            rgba = self._rgba(body.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
            cr.arc(x, y, radius, 0.0, math.tau)
            cr.fill()
            if index == self.selected_index:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
                cr.set_line_width(2.0)
                cr.arc(x, y, radius + 4.0, 0.0, math.tau)
                cr.stroke()
            self._draw_collapsed_child_indicator(cr, index, x, y, radius, active_indices)
        barycenter = self._focused_body_barycenter_point(active_indices, origin_x, origin_y, scale, center_x_m, center_y_m)
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter[0], barycenter[1])
        if self._using_hybrid_focus():
            self._draw_context_entities(cr, origin_x, origin_y, scale, center_x_m, center_y_m)

    def _draw_system_overview(self, cr, width: int, height: int) -> None:
        entities = self._overview_entities()
        positions = self._overview_positions()
        if not entities or len(positions) == 0:
            return
        center_x_m, center_y_m = self._overview_view_center(entities, positions)
        scale = self._overview_canvas_scale(width, height, positions, center_x_m, center_y_m)
        origin_x = width / 2.0
        origin_y = height / 2.0

        cr.set_line_width(1.25)
        for entity in entities:
            trail = self.overview_trails.get(entity.id, [])
            if len(trail) < 2:
                continue
            rgba = self._rgba(entity.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.45)
            first_x, first_y = self._project(trail[0][0], trail[0][1], origin_x, origin_y, scale, center_x_m, center_y_m)
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                x, y = self._project(point_x, point_y, origin_x, origin_y, scale, center_x_m, center_y_m)
                cr.line_to(x, y)
            cr.stroke()

        for index, entity in enumerate(entities):
            position = positions[index]
            x, y = self._project(float(position[0]), float(position[1]), origin_x, origin_y, scale, center_x_m, center_y_m)
            rgba = self._rgba(entity.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
            cr.arc(x, y, 7.0, 0.0, math.tau)
            cr.fill()
            if entity.id == self.selected_group_id:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
                cr.set_line_width(2.0)
                cr.arc(x, y, 12.0, 0.0, math.tau)
                cr.stroke()

        barycenter = self._shared_barycenter_point(entities, positions, origin_x, origin_y, scale, center_x_m, center_y_m)
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter[0], barycenter[1])

    def _draw_context_entities(
        self,
        cr,
        origin_x: float,
        origin_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
    ) -> None:
        entities = self._context_entities()
        positions = self._context_positions()
        if not entities or len(positions) == 0:
            return

        cr.set_line_width(1.0)
        for entity in entities:
            trail = self.context_trails.get(entity.id, [])
            if len(trail) < 2:
                continue
            rgba = self._rgba(entity.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.22)
            first_x, first_y = self._project(trail[0][0], trail[0][1], origin_x, origin_y, scale, center_x_m, center_y_m)
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                x, y = self._project(point_x, point_y, origin_x, origin_y, scale, center_x_m, center_y_m)
                cr.line_to(x, y)
            cr.stroke()

        for index, entity in enumerate(entities):
            position = positions[index]
            x, y = self._project(float(position[0]), float(position[1]), origin_x, origin_y, scale, center_x_m, center_y_m)
            rgba = self._rgba(entity.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.45)
            cr.arc(x, y, 5.0, 0.0, math.tau)
            cr.fill()

        barycenter = self._shared_barycenter_point(entities, positions, origin_x, origin_y, scale, center_x_m, center_y_m)
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter[0], barycenter[1])

    def _shared_barycenter_point(
        self,
        entities: list[OverviewEntity],
        positions,
        origin_x: float,
        origin_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
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
        return self._project(barycenter_x_m, barycenter_y_m, origin_x, origin_y, scale, center_x_m, center_y_m)

    def _focused_body_barycenter_point(
        self,
        active_indices: set[int],
        origin_x: float,
        origin_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
    ) -> tuple[float, float] | None:
        visible_indices = [
            index
            for index in active_indices
            if 0 <= index < len(self.system.bodies) and self.system.bodies[index].visible
        ]
        if len(visible_indices) < 2:
            return None
        total_mass = sum(self.system.bodies[index].mass_kg for index in visible_indices)
        if total_mass <= 0.0:
            return None
        barycenter_x_m = sum(
            self.system.bodies[index].mass_kg * self.system.bodies[index].position_m[0]
            for index in visible_indices
        ) / total_mass
        barycenter_y_m = sum(
            self.system.bodies[index].mass_kg * self.system.bodies[index].position_m[1]
            for index in visible_indices
        ) / total_mass
        return self._project(barycenter_x_m, barycenter_y_m, origin_x, origin_y, scale, center_x_m, center_y_m)

    def _draw_shared_barycenter(self, cr, x: float, y: float) -> None:
        cr.set_source_rgba(1.0, 0.08, 0.06, 0.95)
        cr.arc(x, y, 3.0, 0.0, math.tau)
        cr.fill()

    def _overview_positions(self):
        if self.overview_state is not None and self.overview_entity_ids == [entity.id for entity in self._overview_entities()]:
            return self.overview_state.positions_m
        return [entity.position_m for entity in self._overview_entities()]

    def _overview_view_center(self, entities: list[OverviewEntity], positions) -> tuple[float, float]:
        total_mass = sum(entity.mass_kg for entity in entities)
        if total_mass <= 0.0:
            return (0.0, 0.0)
        return (
            sum(entities[index].mass_kg * float(positions[index][0]) for index in range(len(entities))) / total_mass,
            sum(entities[index].mass_kg * float(positions[index][1]) for index in range(len(entities))) / total_mass,
        )

    def _overview_canvas_scale(self, width: int, height: int, positions, center_x_m: float, center_y_m: float) -> float:
        max_distance = max(
            self._view_distance(float(position[0]), float(position[1]), center_x_m, center_y_m)
            for position in positions
        )
        return min(width, height) * 0.45 / max(max_distance, AU) * self.zoom_factor

    def _canvas_scale(self, width: int, height: int, center_x_m: float, center_y_m: float) -> float:
        active_indices = self._active_body_indices()
        if self._using_hybrid_focus():
            bounds = focused_canvas_bounds(self.system.bodies, active_indices)
            if bounds is not None:
                _, radius_m = bounds
                return min(width, height) * 0.45 / max(radius_m, AU) * self.zoom_factor
        max_distance = max(
            self._view_distance(body.position_m[0], body.position_m[1], center_x_m, center_y_m)
            for index, body in enumerate(self.system.bodies)
            if index in active_indices
        )
        return min(width, height) * 0.45 / max(max_distance, AU) * self.zoom_factor

    def _body_index_at_canvas_point(self, pointer_x: float, pointer_y: float) -> int | None:
        if not self.system.bodies or self._using_system_overview():
            return None

        width = self.canvas.get_width()
        height = self.canvas.get_height()
        if width <= 0 or height <= 0:
            return None

        center_x_m, center_y_m = self._view_center()
        scale = self._canvas_scale(width, height, center_x_m, center_y_m)
        origin_x = width / 2.0
        origin_y = height / 2.0
        closest_index = None
        closest_distance = math.inf

        active_indices = set(self._active_body_indices())
        for index, body in enumerate(self.system.bodies):
            if index not in active_indices:
                continue
            if not body.visible:
                continue
            body_x, body_y = self._project(
                body.position_m[0],
                body.position_m[1],
                origin_x,
                origin_y,
                scale,
                center_x_m,
                center_y_m,
            )
            distance = math.hypot(pointer_x - body_x, pointer_y - body_y)
            hit_radius = max(self._display_radius(body) + 4.0, 8.0)
            if distance <= hit_radius and distance < closest_distance:
                closest_index = index
                closest_distance = distance

        return closest_index

    def _overview_entity_at_canvas_point(self, pointer_x: float, pointer_y: float) -> OverviewEntity | None:
        if not self.system.bodies:
            return None

        width = self.canvas.get_width()
        height = self.canvas.get_height()
        if width <= 0 or height <= 0:
            return None

        if self._using_system_overview():
            entities = self._overview_entities()
            positions = self._overview_positions()
            if not entities or len(positions) == 0:
                return None
            center_x_m, center_y_m = self._overview_view_center(entities, positions)
            scale = self._overview_canvas_scale(width, height, positions, center_x_m, center_y_m)
            return self._entity_at_point(
                entities,
                positions,
                pointer_x,
                pointer_y,
                scale,
                center_x_m,
                center_y_m,
                12.0,
            )

        if self._using_hybrid_focus():
            entities = self._context_entities()
            positions = self._context_positions()
            if not entities or len(positions) == 0:
                return None
            center_x_m, center_y_m = self._view_center()
            scale = self._canvas_scale(width, height, center_x_m, center_y_m)
            return self._entity_at_point(
                entities,
                positions,
                pointer_x,
                pointer_y,
                scale,
                center_x_m,
                center_y_m,
                10.0,
            )

        return None

    def _entity_at_point(
        self,
        entities: list[OverviewEntity],
        positions,
        pointer_x: float,
        pointer_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
        hit_radius: float,
    ) -> OverviewEntity | None:
        origin_x = self.canvas.get_width() / 2.0
        origin_y = self.canvas.get_height() / 2.0
        closest_entity = None
        closest_distance = math.inf

        for index, entity in enumerate(entities):
            position = positions[index]
            entity_x, entity_y = self._project(
                float(position[0]),
                float(position[1]),
                origin_x,
                origin_y,
                scale,
                center_x_m,
                center_y_m,
            )
            distance = math.hypot(pointer_x - entity_x, pointer_y - entity_y)
            if distance <= hit_radius and distance < closest_distance:
                closest_entity = entity
                closest_distance = distance

        return closest_entity

    def _project(
        self,
        x_m: float,
        y_m: float,
        origin_x: float,
        origin_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
    ) -> tuple[float, float]:
        x_delta = x_m - center_x_m
        y_delta = y_m - center_y_m
        if self.system.settings.view_mode == "log_overview":
            distance = math.hypot(x_delta, y_delta)
            if distance > 0.0:
                compressed = AU * math.log1p(distance / AU)
                factor = compressed / distance
                x_delta *= factor
                y_delta *= factor
        return origin_x + x_delta * scale, origin_y - y_delta * scale

    def _view_distance(self, x_m: float, y_m: float, center_x_m: float, center_y_m: float) -> float:
        distance = math.hypot(x_m - center_x_m, y_m - center_y_m)
        if self.system.settings.view_mode == "log_overview":
            return AU * math.log1p(distance / AU)
        return distance

    def _view_center(self) -> tuple[float, float]:
        if not self.system.bodies:
            return (0.0, 0.0)
        if self._using_hybrid_focus():
            bounds = focused_canvas_bounds(self.system.bodies, self._active_body_indices())
            if bounds is not None:
                return bounds[0]
        if self.system.settings.view_mode == "follow_selected":
            if self.selected_group_id is not None:
                group_center = self._group_center(self.selected_group_id)
                if group_center is not None:
                    return group_center
            selected = self.system.bodies[self.selected_index]
            if selected.parent_id is not None:
                parent = next((body for body in self.system.bodies if body.id == selected.parent_id), None)
                if parent is not None:
                    return (parent.position_m[0], parent.position_m[1])
            return (selected.position_m[0], selected.position_m[1])
        total_mass = sum(body.mass_kg for body in self.system.bodies)
        if total_mass <= 0.0:
            return (0.0, 0.0)
        return (
            sum(body.mass_kg * body.position_m[0] for body in self.system.bodies) / total_mass,
            sum(body.mass_kg * body.position_m[1] for body in self.system.bodies) / total_mass,
        )

    def _active_body_indices(self) -> list[int]:
        return active_body_indices(
            self.system.bodies,
            self.system.settings.simulation_scope,
            self.system.settings.view_mode,
            self.selected_index,
            self.system.groups,
            self.focus_group_id,
            self.focus_target,
        )

    def _effective_simulation_scope(self) -> str:
        return effective_simulation_scope(
            self.system.bodies,
            self.system.settings.simulation_scope,
            self.system.settings.view_mode,
            self.selected_index,
            self.system.groups,
            self.focus_target,
        )

    def _using_system_overview(self) -> bool:
        return self._effective_simulation_scope() == "system_overview" and len(self._overview_entities()) > 1

    def _using_hybrid_focus(self) -> bool:
        return self._effective_simulation_scope() == "hybrid_focused_context" and bool(self._active_body_indices())

    def _overview_entities(self) -> list[OverviewEntity]:
        return system_overview_entities(self.system.bodies, self.system.groups)

    def _overview_simulation_state(self) -> SimulationState:
        entities = self._overview_entities()
        entity_ids = [entity.id for entity in entities]
        if self.overview_state is None or self.overview_entity_ids != entity_ids:
            self.overview_entity_ids = entity_ids
            self.overview_state = SimulationState(
                masses_kg=np.array([entity.mass_kg for entity in entities], dtype=float),
                positions_m=np.array([entity.position_m for entity in entities], dtype=float),
                velocities_mps=np.array([entity.velocity_mps for entity in entities], dtype=float),
                elapsed_s=self.state.elapsed_s,
            )
        return self.overview_state.copy()

    def _context_entities(self) -> list[OverviewEntity]:
        return context_overview_entities(self.system.bodies, self.system.groups, self.focus_target)

    def _context_simulation_state(self) -> SimulationState | None:
        entities = self._context_entities()
        if not entities:
            return None
        entity_ids = [entity.id for entity in entities]
        if self.context_state is None or self.context_entity_ids != entity_ids:
            self.context_entity_ids = entity_ids
            self.context_state = SimulationState(
                masses_kg=np.array([entity.mass_kg for entity in entities], dtype=float),
                positions_m=np.array([entity.position_m for entity in entities], dtype=float),
                velocities_mps=np.array([entity.velocity_mps for entity in entities], dtype=float),
                elapsed_s=self.state.elapsed_s,
            )
        return self.context_state.copy()

    def _context_positions(self):
        if self.context_state is not None and self.context_entity_ids == [entity.id for entity in self._context_entities()]:
            return self.context_state.positions_m
        return [entity.position_m for entity in self._context_entities()]

    def _group_center(self, group_id: str) -> tuple[float, float] | None:
        indices = self._body_indices_for_group(group_id)
        if not indices:
            return None
        total_mass = sum(self.system.bodies[index].mass_kg for index in indices)
        if total_mass <= 0.0:
            return None
        return (
            sum(self.system.bodies[index].mass_kg * self.system.bodies[index].position_m[0] for index in indices) / total_mass,
            sum(self.system.bodies[index].mass_kg * self.system.bodies[index].position_m[1] for index in indices) / total_mass,
        )

    def _simulation_state_for_indices(self, active_indices: list[int]) -> SimulationState:
        return SimulationState(
            self.state.masses_kg[active_indices].copy(),
            self.state.positions_m[active_indices].copy(),
            self.state.velocities_mps[active_indices].copy(),
            self.state.elapsed_s,
        )

    def _merge_active_state(self, active_state: SimulationState, active_indices: list[int]) -> None:
        self.state.positions_m[active_indices] = active_state.positions_m
        self.state.velocities_mps[active_indices] = active_state.velocities_mps
        self.state.elapsed_s = active_state.elapsed_s

    def _draw_collapsed_child_indicator(
        self,
        cr,
        body_index: int,
        x: float,
        y: float,
        radius: float,
        active_indices: set[int],
    ) -> None:
        count = collapsed_child_counts(self.system.bodies, list(active_indices)).get(body_index, 0)
        if count <= 0:
            return
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.45)
        cr.set_line_width(1.0)
        cr.arc(x, y, radius + 8.0, 0.0, math.tau)
        cr.stroke()
        marker_count = min(4, count)
        marker_radius = 1.5
        orbit_radius = radius + 8.0
        for marker_index in range(marker_count):
            angle = math.tau * marker_index / marker_count
            cr.arc(
                x + math.cos(angle) * orbit_radius,
                y + math.sin(angle) * orbit_radius,
                marker_radius,
                0.0,
                math.tau,
            )
            cr.fill()

    def _distance_factor(self) -> float:
        return unit_factor(DISTANCE_UNITS, self.system.settings.distance_unit)

    def _configure_position_spins(self) -> None:
        unit = self.system.settings.distance_unit
        factor = self._distance_factor()
        self.x_label.set_label(f"X ({unit})")
        self.y_label.set_label(f"Y ({unit})")
        max_distance_m = max(
            max(abs(body.position_m[0]), abs(body.position_m[1]))
            for body in self.system.bodies
        )
        limit = max(100.0, 10.0 * max_distance_m / factor)
        step = max(0.0001, limit / 10000.0)
        page = max(step * 10.0, limit / 100.0)
        digits = 6 if unit in {"ly", "kAU"} else 4
        for spin in (self.x_spin, self.y_spin):
            adjustment = spin.get_adjustment()
            adjustment.set_lower(-limit)
            adjustment.set_upper(limit)
            adjustment.set_step_increment(step)
            adjustment.set_page_increment(page)
            spin.set_digits(digits)

    def _display_radius(self, body: Body) -> float:
        if body.kind == "star":
            return 8.5
        return max(3.0, min(7.0, math.log10(body.radius_m) - 2.0))

    def _draw_swatch(self, _area, cr, width: int, height: int, color: str) -> None:
        rgba = self._rgba(color)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
        cr.arc(width / 2.0, height / 2.0, min(width, height) / 2.5, 0.0, math.tau)
        cr.fill()

    def _rgba(self, color: str) -> Gdk.RGBA:
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            rgba.parse("#ffffff")
        return rgba
