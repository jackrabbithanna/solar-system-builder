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

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

from . import hierarchy, playback
from .canvas import CanvasScene, SolarSystemCanvas
from .constants import AU, DAY
from .models import Body, DataSource, ModelError, OrbitData, SolarSystem, SystemGroup
from .orbits import (
    binary_pair_state_vectors,
    body_indices_for_group,
    desired_barycenter_from_orbit,
    group_barycenter,
    shift_group_to_barycenter,
    state_vectors_from_orbit,
    target_anchor,
)
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
    derived_max_step_s,
    derived_overview_max_step_s,
    distance_between_bodies_m,
    effective_simulation_scope,
    context_overview_entities,
    focused_canvas_bounds,
    focused_visible_step_s,
    focus_target_body_indices,
    format_elapsed_time,
    recommended_trail_sample_interval_s,
    system_overview_entities,
    time_unit_for_seconds,
    unit_factor,
    unit_index,
)
from .storage import Library


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
    orbit_expander = Gtk.Template.Child()
    orbit_axis_spin = Gtk.Template.Child()
    orbit_period_spin = Gtk.Template.Child()
    orbit_eccentricity_spin = Gtk.Template.Child()
    orbit_inclination_spin = Gtk.Template.Child()
    orbit_node_spin = Gtk.Template.Child()
    orbit_periapsis_spin = Gtk.Template.Child()
    orbit_anomaly_spin = Gtk.Template.Child()
    orbit_epoch_entry = Gtk.Template.Child()
    orbit_source_entry = Gtk.Template.Child()
    orbit_source_url_entry = Gtk.Template.Child()
    orbit_notes_entry = Gtk.Template.Child()
    orbit_target_label = Gtk.Template.Child()
    orbit_target_dropdown = Gtk.Template.Child()
    generate_orbit_button = Gtk.Template.Child()
    generate_group_orbit_button = Gtk.Template.Child()
    generate_binary_orbit_button = Gtk.Template.Child()
    orbit_status_label = Gtk.Template.Child()
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
        self.orbit_target_options: list[tuple[str, str]] = []
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

        self.canvas.connect("body-selected", self._on_canvas_body_selected)
        self.canvas.connect("group-selected", self._on_canvas_group_selected)
        self.canvas.connect("zoom-factor-changed", self._on_canvas_zoom_factor_changed)
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
        self.generate_orbit_button.connect("clicked", self._on_generate_orbit_clicked)
        self.generate_group_orbit_button.connect("clicked", self._on_generate_group_orbit_clicked)
        self.generate_binary_orbit_button.connect("clicked", self._on_generate_binary_orbit_clicked)
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
        self._refresh_canvas()

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
        return hierarchy.body_list_rows(self.system.bodies, self.system.groups)

    def _body_subtree_order(self, body_index: int) -> list[int]:
        return hierarchy.body_subtree_order(self.system.bodies, body_index)

    def _body_sort_key(self, parent_index: int | None, child_index: int) -> tuple[int, float, str]:
        return hierarchy.body_sort_key(self.system.bodies, parent_index, child_index)

    def _body_list_order(self) -> list[int]:
        return hierarchy.body_list_order(self.system.bodies)

    def _body_row_depth(self, body_index: int) -> int:
        return hierarchy.body_row_depth(self.system.bodies, self.system.groups, body_index)

    def _body_depth(self, body: Body) -> int:
        return hierarchy.body_depth(self.system.bodies, body)

    def _body_relationship_label(self, body: Body) -> str:
        return hierarchy.body_relationship_label(self.system.bodies, body)

    def _group_label(self, group: SystemGroup) -> str:
        return hierarchy.group_label(self.system.bodies, self.system.groups, group)

    def _group_by_id(self, group_id: str | None) -> SystemGroup | None:
        return hierarchy.group_by_id(self.system.groups, group_id)

    def _group_depth(self, group: SystemGroup | None) -> int:
        return hierarchy.group_depth(self.system.groups, group)

    def _group_for_body_id(self, body_id: str) -> SystemGroup | None:
        return hierarchy.group_for_body_id(self.system.bodies, self.system.groups, body_id)

    def _group_id_for_body_index(self, body_index: int) -> str | None:
        return hierarchy.group_id_for_body_index(self.system.bodies, self.system.groups, body_index)

    def _body_indices_for_group(self, group_id: str) -> list[int]:
        return hierarchy.body_indices_for_group(self.system.bodies, self.system.groups, group_id)

    def _descendant_group_ids(self, group_id: str) -> set[str]:
        return hierarchy.descendant_group_ids(self.system.groups, group_id)

    def _nearest_other_star(self, body: Body) -> Body | None:
        return hierarchy.nearest_other_star(self.system.bodies, body)

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
        self._refresh_canvas()

    def _load_body_editor(self, body: Body) -> None:
        self.editing = True
        distance_factor = self._distance_factor()
        self._set_body_editor_sensitive(True)
        self.selected_group_id = None
        self.selected_name_label.set_label(body.name)
        self._configure_focus_button(self._body_focus_target(body))
        self._populate_selected_distance_list(body)
        self._load_orbit_editor(body)
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
        self._load_group_orbit_editor(group)
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
        self._set_orbit_editor_sensitive(sensitive and self.selected_group_id is None)

    def _orbit_editor_widgets(self):
        return (
            self.orbit_axis_spin,
            self.orbit_period_spin,
            self.orbit_eccentricity_spin,
            self.orbit_inclination_spin,
            self.orbit_node_spin,
            self.orbit_periapsis_spin,
            self.orbit_anomaly_spin,
            self.orbit_epoch_entry,
            self.orbit_source_entry,
            self.orbit_source_url_entry,
            self.orbit_notes_entry,
            self.generate_orbit_button,
            self.orbit_target_dropdown,
            self.generate_group_orbit_button,
            self.generate_binary_orbit_button,
        )

    def _set_orbit_editor_sensitive(self, sensitive: bool) -> None:
        for widget in self._orbit_editor_widgets():
            widget.set_sensitive(sensitive)

    def _load_orbit_editor(self, body: Body | None) -> None:
        parent = None
        if body is not None and body.parent_id is not None:
            parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)

        self._show_group_orbit_controls(False)
        self.orbit_expander.set_sensitive(body is not None)
        self._set_orbit_editor_sensitive(body is not None and parent is not None)
        self.generate_orbit_button.set_sensitive(body is not None and parent is not None)
        if body is None or parent is None:
            if body is None:
                self._load_orbit_values(None, None, 0.0)
                self.orbit_status_label.set_label("")
            elif binary_group := self._direct_binary_group_for_body(body):
                self._load_orbit_values(binary_group.orbit, binary_group.data_source, 0.0)
                self._set_orbit_editor_sensitive(True)
                self.generate_orbit_button.set_sensitive(False)
                self.generate_binary_orbit_button.set_visible(True)
                self.generate_binary_orbit_button.set_sensitive(True)
                self.orbit_status_label.set_label(
                    f"{body.name} is a member of {binary_group.name}. "
                    "Edit these fields to generate the binary pair around the shared barycenter."
                )
            else:
                self._load_orbit_values(None, None, 0.0)
                self.orbit_status_label.set_label("Orbital generation requires a parent body.")
            return

        orbit = body.orbit
        self._load_orbit_values(orbit, body.data_source, distance_between_bodies_m(body, parent) / AU)
        self.orbit_status_label.set_label(f"Generate an approximate state vector around {parent.name}.")

    def _load_group_orbit_editor(self, group: SystemGroup) -> None:
        self.orbit_expander.set_sensitive(True)
        self._show_group_orbit_controls(True)
        self._set_orbit_editor_sensitive(True)
        self.generate_orbit_button.set_sensitive(False)
        self._populate_orbit_target_dropdown(group)
        orbit = group.orbit
        self.orbit_axis_spin.set_value((orbit.semi_major_axis_m / AU) if orbit and orbit.semi_major_axis_m else 0.0)
        self.orbit_period_spin.set_value((orbit.orbital_period_s / DAY) if orbit and orbit.orbital_period_s else 0.0)
        self.orbit_eccentricity_spin.set_value(orbit.eccentricity if orbit and orbit.eccentricity is not None else 0.0)
        self.orbit_inclination_spin.set_value(
            orbit.inclination_deg if orbit and orbit.inclination_deg is not None else 0.0
        )
        self.orbit_node_spin.set_value(
            orbit.longitude_of_ascending_node_deg
            if orbit and orbit.longitude_of_ascending_node_deg is not None
            else 0.0
        )
        self.orbit_periapsis_spin.set_value(
            orbit.argument_of_periapsis_deg
            if orbit and orbit.argument_of_periapsis_deg is not None
            else 0.0
        )
        self.orbit_anomaly_spin.set_value(orbit.mean_anomaly_deg if orbit and orbit.mean_anomaly_deg is not None else 0.0)
        self.orbit_epoch_entry.set_text(orbit.epoch if orbit and orbit.epoch else self.system.epoch)
        source = group.data_source
        self.orbit_source_entry.set_text(source.source_name if source else "")
        self.orbit_source_url_entry.set_text(source.source_url if source else "")
        self.orbit_notes_entry.set_text(orbit.approximation_notes if orbit else "")
        has_target = bool(self.orbit_target_options)
        self.orbit_target_dropdown.set_sensitive(has_target)
        self.generate_group_orbit_button.set_sensitive(has_target)
        self.generate_binary_orbit_button.set_sensitive(self._group_direct_body_indices(group) is not None)
        if has_target:
            self.orbit_status_label.set_label("Generate an approximate group barycenter orbit around the selected target.")
        else:
            self.orbit_status_label.set_label("Group barycenter generation requires an eligible body or group target.")

    def _load_orbit_values(self, orbit: OrbitData | None, source: DataSource | None, default_axis_au: float) -> None:
        if orbit is not None and orbit.semi_major_axis_m is not None:
            self.orbit_axis_spin.set_value(orbit.semi_major_axis_m / AU)
        else:
            self.orbit_axis_spin.set_value(default_axis_au)
        self.orbit_period_spin.set_value((orbit.orbital_period_s / DAY) if orbit and orbit.orbital_period_s else 0.0)
        self.orbit_eccentricity_spin.set_value(orbit.eccentricity if orbit and orbit.eccentricity is not None else 0.0)
        self.orbit_inclination_spin.set_value(
            orbit.inclination_deg if orbit and orbit.inclination_deg is not None else 0.0
        )
        self.orbit_node_spin.set_value(
            orbit.longitude_of_ascending_node_deg
            if orbit and orbit.longitude_of_ascending_node_deg is not None
            else 0.0
        )
        self.orbit_periapsis_spin.set_value(
            orbit.argument_of_periapsis_deg
            if orbit and orbit.argument_of_periapsis_deg is not None
            else 0.0
        )
        self.orbit_anomaly_spin.set_value(orbit.mean_anomaly_deg if orbit and orbit.mean_anomaly_deg is not None else 0.0)
        self.orbit_epoch_entry.set_text(orbit.epoch if orbit and orbit.epoch else self.system.epoch)
        self.orbit_source_entry.set_text(source.source_name if source else "")
        self.orbit_source_url_entry.set_text(source.source_url if source else "")
        self.orbit_notes_entry.set_text(orbit.approximation_notes if orbit else "")

    def _show_group_orbit_controls(self, visible: bool) -> None:
        for widget in (
            self.orbit_target_label,
            self.orbit_target_dropdown,
            self.generate_group_orbit_button,
            self.generate_binary_orbit_button,
        ):
            widget.set_visible(visible)

    def _populate_orbit_target_dropdown(self, group: SystemGroup) -> None:
        options: list[tuple[str, str, str]] = []
        group_body_ids = {self.system.bodies[index].id for index in body_indices_for_group(self.system.bodies, self.system.groups, group.id)}
        descendant_group_ids = hierarchy.descendant_group_ids(self.system.groups, group.id, include_self=False)
        for candidate in self.system.groups:
            if candidate.id == group.id or candidate.id in descendant_group_ids:
                continue
            candidate_body_ids = {
                self.system.bodies[index].id
                for index in body_indices_for_group(self.system.bodies, self.system.groups, candidate.id)
            }
            if group_body_ids & candidate_body_ids:
                continue
            try:
                group_barycenter(self.system.bodies, self.system.groups, candidate.id)
            except ModelError:
                continue
            options.append(("group", candidate.id, f"{candidate.name} (group)"))
        for body in self.system.bodies:
            if body.id in group_body_ids:
                continue
            options.append(("body", body.id, f"{body.name} (body)"))
        self.orbit_target_options = [(target_type, target_id) for target_type, target_id, _label in options]
        self.orbit_target_dropdown.set_model(Gtk.StringList.new([label for _target_type, _target_id, label in options]))
        selected = 0
        if group.orbit_target_type is not None and group.orbit_target_id is not None:
            selected = next(
                (
                    index
                    for index, (target_type, target_id) in enumerate(self.orbit_target_options)
                    if target_type == group.orbit_target_type and target_id == group.orbit_target_id
                ),
                0,
            )
        if self.orbit_target_options:
            self.orbit_target_dropdown.set_selected(selected)

    def _group_direct_body_indices(self, group: SystemGroup) -> tuple[int, int] | None:
        if len(group.body_ids) != 2:
            return None
        body_indices_by_id = {body.id: index for index, body in enumerate(self.system.bodies)}
        indices = [body_indices_by_id.get(body_id) for body_id in group.body_ids]
        if any(index is None for index in indices):
            return None
        return int(indices[0]), int(indices[1])

    def _direct_binary_group_for_body(self, body: Body) -> SystemGroup | None:
        for group in self.system.groups:
            if body.id in group.body_ids and self._group_direct_body_indices(group) is not None:
                return group
        return None

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
        return hierarchy.selected_distance_rows(self.system.bodies, body)

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
        self._refresh_canvas()

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
            self._refresh_canvas()

    def _on_simulation_scope_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(SIMULATION_SCOPE_LABELS):
            self.system.settings.simulation_scope = SIMULATION_SCOPE_LABELS[selected][1]
            self._clear_dynamic_simulation_state()
            self._update_title()
            self._refresh_canvas()

    def _on_distance_unit_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(DISTANCE_UNITS):
            self.system.settings.distance_unit = DISTANCE_UNITS[selected][1]
            if self.system.bodies:
                self._load_body_editor(self.system.bodies[self.selected_index])

    def _on_generate_orbit_clicked(self, _button) -> None:
        if self.editing or not self.system.bodies or self.selected_group_id is not None:
            return
        body = self.system.bodies[self.selected_index]
        if body.parent_id is None:
            return
        parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)
        if parent is None:
            return

        try:
            orbit = self._orbit_from_editor()
            position_m, velocity_mps = state_vectors_from_orbit(parent, body, orbit)
        except ModelError as error:
            self._show_error_dialog("Cannot Generate Orbit", str(error))
            return

        body.orbit = orbit
        source_name = self.orbit_source_entry.get_text().strip()
        source_url = self.orbit_source_url_entry.get_text().strip()
        body.data_source = DataSource(source_name=source_name, source_url=source_url)
        if body.data_source.to_dict() == {}:
            body.data_source = None
        body.position_m = position_m
        body.velocity_mps = velocity_mps
        self.state = SimulationState.from_bodies(self.system.bodies)
        self.simulation_generation += 1
        self._clear_dynamic_simulation_state()
        self._load_body_editor(body)
        self._refresh_body_relationship_labels()
        self._update_title()
        self._refresh_canvas()

    def _on_generate_group_orbit_clicked(self, _button) -> None:
        if self.editing or self.selected_group_id is None:
            return
        group = self._group_by_id(self.selected_group_id)
        if group is None:
            return
        selected = self.orbit_target_dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION or selected >= len(self.orbit_target_options):
            self._show_error_dialog("Cannot Generate Group Orbit", "Select a target body or group.")
            return
        target_type, target_id = self.orbit_target_options[selected]

        try:
            orbit = self._orbit_from_editor()
            current = group_barycenter(self.system.bodies, self.system.groups, group.id)
            target = target_anchor(self.system.bodies, self.system.groups, target_type, target_id)
            desired = desired_barycenter_from_orbit(target, current.mass_kg, orbit)
            shift_group_to_barycenter(self.system.bodies, self.system.groups, group.id, desired)
        except ModelError as error:
            self._show_error_dialog("Cannot Generate Group Orbit", str(error))
            return

        group.orbit = orbit
        group.orbit_target_type = target_type
        group.orbit_target_id = target_id
        group.data_source = self._data_source_from_orbit_editor()
        self._after_orbit_generated()
        self._load_group_focus(group.id)

    def _on_generate_binary_orbit_clicked(self, _button) -> None:
        if self.editing:
            return
        group = self._group_by_id(self.selected_group_id)
        if group is None and self.system.bodies:
            group = self._direct_binary_group_for_body(self.system.bodies[self.selected_index])
        if group is None:
            return
        indices = self._group_direct_body_indices(group)
        if indices is None:
            self._show_error_dialog("Cannot Generate Binary Pair", "Binary generation requires exactly two direct bodies.")
            return

        first = self.system.bodies[indices[0]]
        second = self.system.bodies[indices[1]]
        try:
            orbit = self._orbit_from_editor()
            center = group_barycenter(self.system.bodies, self.system.groups, group.id)
            first_state, second_state = binary_pair_state_vectors(
                first,
                second,
                orbit,
                center.position_m,
                center.velocity_mps,
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Generate Binary Pair", str(error))
            return

        first.position_m, first.velocity_mps = first_state
        second.position_m, second.velocity_mps = second_state
        group.orbit = orbit
        group.data_source = self._data_source_from_orbit_editor()
        self.selected_group_id = group.id
        self._after_orbit_generated()
        self._load_group_focus(group.id)

    def _orbit_from_editor(self) -> OrbitData:
        semi_major_axis_m = self.orbit_axis_spin.get_value() * AU
        orbital_period_s = self.orbit_period_spin.get_value() * DAY
        notes = self.orbit_notes_entry.get_text().strip()
        if semi_major_axis_m <= 0.0:
            semi_major_axis_m = None
        if orbital_period_s <= 0.0:
            orbital_period_s = None
        if semi_major_axis_m is None and orbital_period_s is None:
            raise ModelError("enter a semi-major axis or orbital period")
        if notes == "":
            notes = "Approximate orbital seed; unknown fields use app defaults."
        orbit = OrbitData(
            semi_major_axis_m=semi_major_axis_m,
            orbital_period_s=orbital_period_s,
            eccentricity=self.orbit_eccentricity_spin.get_value(),
            inclination_deg=self.orbit_inclination_spin.get_value(),
            longitude_of_ascending_node_deg=self.orbit_node_spin.get_value(),
            argument_of_periapsis_deg=self.orbit_periapsis_spin.get_value(),
            mean_anomaly_deg=self.orbit_anomaly_spin.get_value(),
            epoch=self.orbit_epoch_entry.get_text().strip() or self.system.epoch,
            reference_plane="app-local XY",
            approximation_notes=notes,
        )
        orbit.validate()
        return orbit

    def _data_source_from_orbit_editor(self) -> DataSource | None:
        source = DataSource(
            source_name=self.orbit_source_entry.get_text().strip(),
            source_url=self.orbit_source_url_entry.get_text().strip(),
        )
        return source if source.to_dict() else None

    def _after_orbit_generated(self) -> None:
        self.state = SimulationState.from_bodies(self.system.bodies)
        self.simulation_generation += 1
        self._clear_dynamic_simulation_state()
        self._populate_body_list()
        self._refresh_body_relationship_labels()
        self._update_title()
        self._refresh_canvas()

    def _show_error_dialog(self, title: str, message: str) -> None:
        dialog = Adw.AlertDialog.new(title, message)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(self)

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
        self._refresh_canvas()

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
                self._refresh_canvas()
            return

        body_index = int(row_id)
        if body_index != self.selected_index or self.selected_group_id is not None:
            self.focus_target = None
            self.selected_group_id = None
            self.selected_index = body_index
            self.focus_group_id = self._group_id_for_body_index(body_index)
            self._load_body_editor(self.system.bodies[body_index])
            self._update_title()
            self._refresh_canvas()

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

    def _on_canvas_body_selected(self, _canvas: SolarSystemCanvas, body_index: int) -> None:
        self._select_body(body_index)
        self._refresh_canvas()

    def _on_canvas_group_selected(self, _canvas: SolarSystemCanvas, group_id: str) -> None:
        self._select_group(group_id)

    def _on_canvas_zoom_factor_changed(self, _canvas: SolarSystemCanvas, zoom_factor: float) -> None:
        self.zoom_factor = zoom_factor
        self._sync_zoom_controls()

    def _set_zoom_factor(self, zoom_factor: float) -> None:
        self.canvas.set_zoom_factor(zoom_factor)
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._sync_zoom_controls()
        self._refresh_canvas()

    def _sync_zoom_controls(self) -> None:
        self.zoom_out_button.set_sensitive(self.zoom_factor > 1.0)
        self.reset_zoom_button.set_sensitive(self.zoom_factor > 1.0)
        self.zoom_in_button.set_sensitive(self.zoom_factor < 64.0)

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
            focused_result, context_result = playback.advance_hybrid_simulations(
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
                playback.advance_hybrid_simulations,
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
        self._refresh_canvas()

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
        self._refresh_canvas()

    def _apply_overview_simulation_state(self, state: SimulationState, position_samples, start_elapsed_s: float) -> None:
        self.overview_state = state
        self.state.elapsed_s = state.elapsed_s
        self._append_overview_trails(position_samples, start_elapsed_s, state.elapsed_s)
        if self.selected_group_id is not None:
            self._load_group_focus(self.selected_group_id)
        else:
            self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_time_label()
        self._refresh_canvas()

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
        self.last_trail_sample_elapsed_s = playback.append_body_trails(
            self.trails,
            self.system.bodies,
            position_samples,
            active_indices,
            start_elapsed_s,
            end_elapsed_s,
            self.last_trail_sample_elapsed_s,
            self.system.settings.trail_sample_interval_s,
        )

    def _append_overview_trails(self, position_samples, start_elapsed_s: float, end_elapsed_s: float) -> None:
        entities = self._overview_entities()
        self.last_trail_sample_elapsed_s = playback.append_entity_trails(
            self.overview_trails,
            [entity.id for entity in entities],
            position_samples,
            start_elapsed_s,
            end_elapsed_s,
            self.last_trail_sample_elapsed_s,
            self.system.settings.trail_sample_interval_s,
            update_last_elapsed=True,
        )

    def _append_context_trails(self, position_samples, start_elapsed_s: float, end_elapsed_s: float) -> None:
        entities = self._context_entities()
        playback.append_entity_trails(
            self.context_trails,
            [entity.id for entity in entities],
            position_samples,
            start_elapsed_s,
            end_elapsed_s,
            self.last_trail_sample_elapsed_s,
            self.system.settings.trail_sample_interval_s,
            update_last_elapsed=False,
        )

    def _update_title(self) -> None:
        self.window_title.set_title(self.system.name)
        max_step_days = self._max_step_seconds() / DAY
        scope = self._effective_simulation_scope().replace("_", " ")
        self.window_title.set_subtitle(f"{self.system.epoch} - {scope}, max step {max_step_days:,.2f} days")

    def _update_time_label(self) -> None:
        self.time_label.set_label(f"Simulation time: {format_elapsed_time(self.state.elapsed_s)}")

    def _refresh_canvas(self) -> None:
        self.canvas.set_scene(self._canvas_scene())

    def _canvas_scene(self) -> CanvasScene:
        active_indices = self._active_body_indices()
        using_hybrid_focus = self._using_hybrid_focus()
        selected_group_center = (
            self._group_center(self.selected_group_id)
            if self.system.settings.view_mode == "follow_selected" and self.selected_group_id is not None
            else None
        )
        hybrid_bounds = (
            focused_canvas_bounds(self.system.bodies, active_indices)
            if using_hybrid_focus
            else None
        )
        return CanvasScene(
            bodies=self.system.bodies,
            active_indices=active_indices,
            selected_body_index=self.selected_index,
            selected_group_id=self.selected_group_id,
            selectable_group_ids={group.id for group in self.system.groups},
            view_mode=self.system.settings.view_mode,
            using_system_overview=self._using_system_overview(),
            using_hybrid_focus=using_hybrid_focus,
            selected_group_center=selected_group_center,
            hybrid_bounds=hybrid_bounds,
            trails=list(self.trails),
            overview_entities=self._overview_entities(),
            overview_positions=self._overview_positions(),
            overview_trails=dict(self.overview_trails),
            context_entities=self._context_entities(),
            context_positions=self._context_positions(),
            context_trails=dict(self.context_trails),
        )

    def _overview_positions(self):
        if self.overview_state is not None and self.overview_entity_ids == [entity.id for entity in self._overview_entities()]:
            return self.overview_state.positions_m
        return [entity.position_m for entity in self._overview_entities()]

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
            self.overview_state = playback.overview_simulation_state(entities, self.state.elapsed_s)
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
            self.context_state = playback.overview_simulation_state(entities, self.state.elapsed_s)
        return self.context_state.copy()

    def _context_positions(self):
        if self.context_state is not None and self.context_entity_ids == [entity.id for entity in self._context_entities()]:
            return self.context_state.positions_m
        return [entity.position_m for entity in self._context_entities()]

    def _group_center(self, group_id: str) -> tuple[float, float] | None:
        return hierarchy.group_center(self.system.bodies, self.system.groups, group_id)

    def _simulation_state_for_indices(self, active_indices: list[int]) -> SimulationState:
        return playback.simulation_state_for_indices(self.state, active_indices)

    def _merge_active_state(self, active_state: SimulationState, active_indices: list[int]) -> None:
        playback.merge_active_state(self.state, active_state, active_indices)

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
