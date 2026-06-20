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

from gi.repository import Adw, GLib, Gtk

from . import hierarchy, playback
from .canvas import CanvasScene, SolarSystemCanvas
from .constants import AU, DAY
from .models import Body, DataSource, ModelError, OrbitData, SolarSystem, SystemGroup, SystemSettings
from .orbit_editing import (
    generate_binary_pair_orbit,
    generate_body_orbit,
    generate_group_barycenter_orbit,
)
from .orbits import body_indices_for_group, group_barycenter
from .presets import load_builtin_solar_system, load_builtin_solar_systems
from .scales import (
    DISTANCE_UNITS,
    FocusState,
    distance_between_bodies_m,
    effective_focus_settings,
    focused_canvas_bounds,
    focused_visible_step_s,
    focus_overview_entity,
    focus_target_body_indices,
    format_elapsed_time,
    recommended_trail_sample_interval_s,
    unit_factor,
)
from .sidebar import BodyHierarchyList, BodyInspectorPanel, SystemPropertiesPanel
from .storage import Library
from .system_library import SystemLibraryController

MAX_AUTOMATIC_SIDEBAR_WIDTH = 520
SIDEBAR_HORIZONTAL_MARGINS = 24
WINDOW_MONITOR_MARGIN = 32


@Gtk.Template(resource_path="/io/github/jackrabbithanna/solarsystembuilder/window.ui")
class SolarSystemBuilderWindow(Adw.ApplicationWindow):
    __gtype_name__ = "SolarSystemBuilderWindow"

    canvas = Gtk.Template.Child()
    main_paned = Gtk.Template.Child()
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

        self.system = load_builtin_solar_system()
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.simulation = playback.SimulationSession.from_bodies(self.system.bodies)
        self.selected_index = 0
        self.focus_group_id: str | None = None
        self.focus_target: str | None = None
        self.focus_state: FocusState | None = None
        self.selected_group_id: str | None = None
        self.playing = False
        self.editing = False
        self.closed = False
        self.simulation_future: Future | None = None
        self.simulation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation")
        self.zoom_factor = 1.0
        self.timer_id = GLib.timeout_add(33, self._tick)
        self.sidebar_resize_id = 0

        self.system_panel = SystemPropertiesPanel(
            self.system_name_entry,
            self.delete_system_button,
            self.speed_spin,
            self.time_unit_dropdown,
            self.accuracy_dropdown,
            self.view_mode_dropdown,
            self.simulation_scope_dropdown,
            self.distance_unit_dropdown,
        )
        self.body_inspector = BodyInspectorPanel(
            self.selected_name_label,
            self.focus_button,
            self.selected_distance_list,
            self.orbit_expander,
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
            self.orbit_target_label,
            self.orbit_target_dropdown,
            self.generate_orbit_button,
            self.generate_group_orbit_button,
            self.generate_binary_orbit_button,
            self.orbit_status_label,
            self.mass_entry,
            self.x_label,
            self.x_spin,
            self.y_label,
            self.y_spin,
            self.vx_spin,
            self.vy_spin,
        )
        self.system_library = SystemLibraryController(
            self,
            self.system_dropdown,
            self.save_button,
            self.duplicate_button,
            self.system_panel,
            Library(),
            load_builtin_solar_systems,
            load_builtin_solar_system,
            lambda: self.system,
            self._prepare_system_for_save,
            self._load_system,
            self._on_system_saved,
            self._update_title,
        )

        self.canvas.connect("body-selected", self._on_canvas_body_selected)
        self.canvas.connect("group-selected", self._on_canvas_group_selected)
        self.canvas.connect("focus-target-selected", self._on_canvas_focus_target_selected)
        self.canvas.connect("zoom-factor-changed", self._on_canvas_zoom_factor_changed)
        self.play_button.connect("clicked", self._on_play_clicked)
        self.step_back_button.connect("clicked", self._on_step_back_clicked)
        self.reset_button.connect("clicked", self._on_reset_clicked)
        self.step_forward_button.connect("clicked", self._on_step_forward_clicked)
        self.zoom_out_button.connect("clicked", self._on_zoom_out_clicked)
        self.reset_zoom_button.connect("clicked", self._on_reset_zoom_clicked)
        self.zoom_in_button.connect("clicked", self._on_zoom_in_clicked)
        self.body_list.connect("body-selected", self._on_hierarchy_body_selected)
        self.body_list.connect("group-selected", self._on_hierarchy_group_selected)
        self.system_panel.connect("time-step-changed", self._on_time_step_changed)
        self.system_panel.connect("accuracy-changed", self._on_accuracy_changed)
        self.system_panel.connect("view-mode-changed", self._on_view_mode_changed)
        self.system_panel.connect("simulation-scope-changed", self._on_simulation_scope_changed)
        self.system_panel.connect("distance-unit-changed", self._on_distance_unit_changed)
        self.body_inspector.connect("body-edited", self._on_body_edit)
        self.body_inspector.connect("focus-requested", self._on_focus_clicked)
        self.body_inspector.connect("generate-body-orbit", self._on_generate_orbit_clicked)
        self.body_inspector.connect("generate-group-orbit", self._on_generate_group_orbit_clicked)
        self.body_inspector.connect("generate-binary-orbit", self._on_generate_binary_orbit_clicked)

        self.system_library.refresh()
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
        if self.sidebar_resize_id:
            GLib.source_remove(self.sidebar_resize_id)
            self.sidebar_resize_id = 0
        self.simulation_executor.shutdown(wait=False, cancel_futures=True)
        return False

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
        self.simulation.replace_bodies(self.system.bodies)
        self.selected_index = self._body_index_for_id(selected_body_id)
        self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.focus_target = None
        self.focus_state = None
        self.selected_group_id = None
        self._populate_body_list()
        self._select_body(self.selected_index)
        self._load_system_editor()
        self._load_settings_editor()
        self._update_title()
        self._update_time_label()
        self._refresh_canvas()
        self._schedule_sidebar_expansion()

    def _schedule_sidebar_expansion(self) -> None:
        if self.sidebar_resize_id:
            GLib.source_remove(self.sidebar_resize_id)
        self.sidebar_resize_id = GLib.idle_add(self._expand_sidebar_for_content)

    def _expand_sidebar_for_content(self) -> bool:
        self.sidebar_resize_id = 0
        if self.closed or self.is_maximized() or self.is_fullscreen():
            return GLib.SOURCE_REMOVE

        paned_width = self.main_paned.get_width()
        left_pane_width = self.main_paned.get_position()
        window_width = self.get_width()
        window_height = self.get_height()
        if paned_width <= 0 or left_pane_width <= 0 or window_width <= 0:
            return GLib.SOURCE_REMOVE

        _minimum, natural_width, _minimum_baseline, _natural_baseline = self.body_list.measure(
            Gtk.Orientation.HORIZONTAL,
            -1,
        )
        desired_sidebar_width = min(
            natural_width + SIDEBAR_HORIZONTAL_MARGINS,
            MAX_AUTOMATIC_SIDEBAR_WIDTH,
        )
        current_sidebar_width = paned_width - left_pane_width
        additional_width = math.ceil(desired_sidebar_width - current_sidebar_width)
        if additional_width <= 0:
            return GLib.SOURCE_REMOVE

        maximum_window_width = self._maximum_window_width()
        target_window_width = min(window_width + additional_width, maximum_window_width)
        if target_window_width > window_width:
            self.set_default_size(target_window_width, window_height)
            self.main_paned.set_position(left_pane_width)
        return GLib.SOURCE_REMOVE

    def _maximum_window_width(self) -> int:
        surface = self.get_surface()
        if surface is None:
            return self.get_width()
        monitor = self.get_display().get_monitor_at_surface(surface)
        if monitor is None:
            return self.get_width()
        return max(self.get_width(), monitor.get_geometry().width - WINDOW_MONITOR_MARGIN)

    def _body_index_for_id(self, body_id: str | None) -> int:
        if body_id is None:
            return 0
        return next(
            (index for index, body in enumerate(self.system.bodies) if body.id == body_id),
            0,
        )

    def _populate_body_list(self) -> None:
        self.body_list.set_system(self.system.bodies, self.system.groups)

    def _group_by_id(self, group_id: str | None) -> SystemGroup | None:
        return hierarchy.group_by_id(self.system.groups, group_id)

    def _group_for_body_id(self, body_id: str) -> SystemGroup | None:
        return hierarchy.group_for_body_id(self.system.bodies, self.system.groups, body_id)

    def _group_id_for_body_index(self, body_index: int) -> str | None:
        return hierarchy.group_id_for_body_index(self.system.bodies, self.system.groups, body_index)

    def _body_indices_for_group(self, group_id: str) -> list[int]:
        return hierarchy.body_indices_for_group(self.system.bodies, self.system.groups, group_id)

    def _descendant_group_ids(self, group_id: str) -> set[str]:
        return hierarchy.descendant_group_ids(self.system.groups, group_id)

    def _refresh_body_relationship_labels(self) -> None:
        self.body_list.refresh_relationship_labels()

    def _select_body(self, index: int) -> None:
        if not self.system.bodies:
            return
        self.selected_index = max(0, min(index, len(self.system.bodies) - 1))
        self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.selected_group_id = None
        self.body_list.select_body(self.selected_index)
        self._load_body_editor(self.system.bodies[self.selected_index])

    def _select_group(self, group_id: str) -> None:
        if self._group_by_id(group_id) is None:
            return
        self.body_list.select_group(group_id)
        if self.selected_group_id != group_id:
            self.focus_target = None
            self.selected_group_id = group_id
            self._load_group_focus(group_id)
            self._update_title()
        self._refresh_canvas()

    def _load_body_editor(self, body: Body) -> None:
        self.body_inspector.set_editing(True)
        distance_factor = self._distance_factor()
        self._set_body_editor_sensitive(True)
        self.selected_group_id = None
        self.body_inspector.set_selected_name(body.name)
        self._configure_focus_button(self._body_focus_target(body))
        self._populate_selected_distance_list(body)
        self._load_orbit_editor(body)
        self._configure_position_spins()
        self.body_inspector.set_body_values(body, distance_factor)
        self.body_inspector.set_editing(False)

    def _load_group_focus(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        self.focus_group_id = group.id
        self.body_inspector.set_editing(True)
        self._set_body_editor_sensitive(False)
        self.body_inspector.set_selected_name(group.name)
        self._configure_focus_button(f"group:{group.id}")
        self.body_inspector.hide_distance_list()
        self._load_group_orbit_editor(group)
        self.body_inspector.clear_body_values()
        self.body_inspector.set_editing(False)

    def _configure_focus_button(self, target: str | None) -> None:
        if target is None:
            self.body_inspector.configure_focus_button(False, False)
            return
        active_indices = focus_target_body_indices(self.system.bodies, self.system.groups, target)
        self.body_inspector.configure_focus_button(bool(active_indices), target == self.focus_target)

    def _body_focus_target(self, body: Body) -> str | None:
        if any(candidate.parent_id == body.id for candidate in self.system.bodies):
            return f"body:{body.id}"
        return None

    def _clear_dynamic_simulation_state(self) -> None:
        self.simulation.increment_generation()
        self.simulation.clear_dynamic(self.system.bodies)

    def _effective_settings(self) -> SystemSettings:
        return effective_focus_settings(self.system.settings, self.focus_state)

    def _exit_focus(self, *, reload_settings: bool = True) -> bool:
        if self.focus_state is None:
            return False
        self.focus_state = None
        self.focus_target = None
        if self.selected_group_id is not None:
            self.focus_group_id = self.selected_group_id
        else:
            self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.zoom_factor = 1.0
        self.canvas.set_zoom_factor(1.0)
        self._clear_dynamic_simulation_state()
        if reload_settings:
            self._load_settings_editor()
        return True

    def _set_body_editor_sensitive(self, sensitive: bool) -> None:
        self.body_inspector.set_body_editor_sensitive(
            sensitive,
            sensitive and self.selected_group_id is None,
        )

    def _orbit_editor_widgets(self):
        return self.body_inspector.orbit_editor_widgets()

    def _set_orbit_editor_sensitive(self, sensitive: bool) -> None:
        self.body_inspector.set_orbit_editor_sensitive(sensitive)

    def _load_orbit_editor(self, body: Body | None) -> None:
        parent = None
        if body is not None and body.parent_id is not None:
            parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)

        self._show_group_orbit_controls(False)
        self.body_inspector.set_orbit_expander_sensitive(body is not None)
        self._set_orbit_editor_sensitive(body is not None and parent is not None)
        self.body_inspector.set_generate_body_orbit_sensitive(body is not None and parent is not None)
        if body is None or parent is None:
            if body is None:
                self._load_orbit_values(None, None, 0.0)
                self.body_inspector.set_orbit_status("")
            elif binary_group := self._direct_binary_group_for_body(body):
                self._load_orbit_values(binary_group.orbit, binary_group.data_source, 0.0)
                self._set_orbit_editor_sensitive(True)
                self.body_inspector.set_generate_body_orbit_sensitive(False)
                self.body_inspector.configure_binary_orbit_button(True, True)
                self.body_inspector.set_orbit_status(
                    f"{body.name} is a member of {binary_group.name}. "
                    "Edit these fields to generate the binary pair around the shared barycenter."
                )
            else:
                self._load_orbit_values(None, None, 0.0)
                self.body_inspector.set_orbit_status("Orbital generation requires a parent body.")
            return

        orbit = body.orbit
        self._load_orbit_values(orbit, body.data_source, distance_between_bodies_m(body, parent) / AU)
        self.body_inspector.set_orbit_status(f"Generate an approximate state vector around {parent.name}.")

    def _load_group_orbit_editor(self, group: SystemGroup) -> None:
        self.body_inspector.set_orbit_expander_sensitive(True)
        self._show_group_orbit_controls(True)
        self._set_orbit_editor_sensitive(True)
        self.body_inspector.set_generate_body_orbit_sensitive(False)
        self._populate_orbit_target_dropdown(group)
        self._load_orbit_values(group.orbit, group.data_source, 0.0)
        has_target = bool(self.body_inspector.orbit_target_options)
        self.body_inspector.set_group_orbit_target_sensitive(
            has_target,
            self._group_direct_body_indices(group) is not None,
        )
        if has_target:
            self.body_inspector.set_orbit_status("Generate an approximate group barycenter orbit around the selected target.")
        else:
            self.body_inspector.set_orbit_status("Group barycenter generation requires an eligible body or group target.")

    def _load_orbit_values(self, orbit: OrbitData | None, source: DataSource | None, default_axis_au: float) -> None:
        self.body_inspector.load_orbit_values(orbit, source, default_axis_au, self.system.epoch)

    def _show_group_orbit_controls(self, visible: bool) -> None:
        self.body_inspector.show_group_orbit_controls(visible)

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
        selected = 0
        if group.orbit_target_type is not None and group.orbit_target_id is not None:
            selected = next(
                (
                    index
                    for index, (target_type, target_id, _label) in enumerate(options)
                    if target_type == group.orbit_target_type and target_id == group.orbit_target_id
                ),
                0,
            )
        self.body_inspector.set_orbit_target_options(options, selected)

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
        self.body_inspector.populate_distance_list(self._selected_distance_rows(body))

    def _selected_distance_rows(self, body: Body) -> list[tuple[str, str]]:
        return hierarchy.selected_distance_rows(self.system.bodies, body)

    def _load_system_editor(self) -> None:
        self.system_library.load_editor()

    def _load_settings_editor(self) -> None:
        self.system_panel.load_settings(self._effective_settings())
        self._configure_position_spins()

    def _on_body_edit(self, *_args) -> None:
        if not self.system.bodies:
            return
        selected_body_id = self.system.bodies[self.selected_index].id
        body = self.system.bodies[self.selected_index]
        distance_factor = self._distance_factor()
        values = self.body_inspector.edited_body_values(distance_factor)
        if values is None:
            return
        mass, x_m, y_m, vx_mps, vy_mps = values
        body.mass_kg = mass
        body.position_m[0] = x_m
        body.position_m[1] = y_m
        body.velocity_mps[0] = vx_mps
        body.velocity_mps[1] = vy_mps
        self.simulation.replace_bodies(self.system.bodies)
        self._populate_body_list()
        self._select_body(self._body_index_for_id(selected_body_id))
        self._update_title()
        self._refresh_canvas()

    def _on_time_step_changed(self, _panel, visible_step_s: float) -> None:
        if self.focus_state is not None:
            self.focus_state.visible_step_s = visible_step_s
            self.focus_state.trail_sample_interval_s = recommended_trail_sample_interval_s(visible_step_s)
            self.focus_state.step_manually_overridden = True
            self._update_title()
            return
        self.system.settings.visible_step_s = visible_step_s
        self.system.settings.trail_sample_interval_s = recommended_trail_sample_interval_s(
            self.system.settings.visible_step_s
        )
        self._update_title()

    def _on_accuracy_changed(self, _panel, accuracy_profile: str) -> None:
        self.system.settings.accuracy_profile = accuracy_profile
        if self.focus_state is not None and not self.focus_state.step_manually_overridden:
            active_indices = focus_target_body_indices(
                self.system.bodies,
                self.system.groups,
                self.focus_state.target,
            )
            focused_bodies = [self.system.bodies[index] for index in active_indices]
            self.focus_state.visible_step_s = focused_visible_step_s(focused_bodies, accuracy_profile)
            self.focus_state.trail_sample_interval_s = recommended_trail_sample_interval_s(
                self.focus_state.visible_step_s
            )
            self._load_settings_editor()
        self._update_title()

    def _on_view_mode_changed(self, _panel, view_mode: str) -> None:
        self._exit_focus(reload_settings=False)
        self.system.settings.view_mode = view_mode
        self._clear_dynamic_simulation_state()
        self._update_title()
        self._refresh_canvas()

    def _on_simulation_scope_changed(self, _panel, simulation_scope: str) -> None:
        self._exit_focus(reload_settings=False)
        if simulation_scope == "full_nbody" and self.simulation.auto_approximation_locked:
            self._load_settings_editor()
            self._prompt_reset_for_full_physics()
            return
        self.system.settings.simulation_scope = simulation_scope
        self._clear_dynamic_simulation_state()
        self._update_title()
        self._refresh_canvas()

    def _prompt_reset_for_full_physics(self) -> None:
        dialog = Adw.AlertDialog.new(
            "Reset for Full N-body?",
            "Auto has already used approximate physics. Reset the simulation before switching to full N-body so omitted orbital history is not treated as exact.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset and Use Full N-body")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("reset")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_full_physics_reset_response)
        dialog.present(self)

    def _on_full_physics_reset_response(self, _dialog, response: str) -> None:
        if response != "reset":
            return
        selected_body_id = self.system.bodies[self.selected_index].id if self.system.bodies else None
        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self._replace_system(
            self.loaded_system_snapshot,
            refresh_snapshot=False,
            selected_body_id=selected_body_id,
        )
        self.system.settings.simulation_scope = "full_nbody"
        self._load_settings_editor()
        self._update_title()
        self._refresh_canvas()

    def _on_distance_unit_changed(self, _panel, distance_unit: str) -> None:
        self.system.settings.distance_unit = distance_unit
        if self.system.bodies:
            self._load_body_editor(self.system.bodies[self.selected_index])

    def _on_generate_orbit_clicked(self, _button) -> None:
        if self.editing or not self.system.bodies or self.selected_group_id is not None:
            return
        body = self.system.bodies[self.selected_index]

        try:
            orbit = self._orbit_from_editor()
            body = generate_body_orbit(
                self.system,
                self.simulation,
                body.id,
                orbit,
                self._data_source_from_orbit_editor(),
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Generate Orbit", str(error))
            return

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
        selected_target = self.body_inspector.selected_orbit_target()
        if selected_target is None:
            self._show_error_dialog("Cannot Generate Group Orbit", "Select a target body or group.")
            return
        target_type, target_id = selected_target

        try:
            orbit = self._orbit_from_editor()
            group = generate_group_barycenter_orbit(
                self.system,
                self.simulation,
                group.id,
                target_type,
                target_id,
                orbit,
                self._data_source_from_orbit_editor(),
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Generate Group Orbit", str(error))
            return

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
        try:
            orbit = self._orbit_from_editor()
            group = generate_binary_pair_orbit(
                self.system,
                self.simulation,
                group.id,
                orbit,
                self._data_source_from_orbit_editor(),
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Generate Binary Pair", str(error))
            return

        self.selected_group_id = group.id
        self._after_orbit_generated()
        self._load_group_focus(group.id)

    def _orbit_from_editor(self) -> OrbitData:
        return self.body_inspector.orbit_from_editor(self.system.epoch)

    def _data_source_from_orbit_editor(self) -> DataSource | None:
        return self.body_inspector.data_source_from_orbit_editor()

    def _after_orbit_generated(self) -> None:
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

        if self.focus_state is not None and self.focus_state.target == target:
            self._exit_focus()
            self._configure_focus_button(target)
            self._update_title()
            self._refresh_canvas()
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
        visible_step_s = focused_visible_step_s(
            focused_bodies,
            self.system.settings.accuracy_profile,
        )
        self.focus_state = FocusState(
            target=target,
            visible_step_s=visible_step_s,
            trail_sample_interval_s=recommended_trail_sample_interval_s(visible_step_s),
        )
        self.zoom_factor = 1.0
        self.canvas.set_zoom_factor(1.0)
        self._clear_dynamic_simulation_state()
        self._load_settings_editor()
        self._configure_focus_button(target)
        self._update_title()
        self._refresh_canvas()

    def _on_hierarchy_body_selected(self, _list_box: BodyHierarchyList, body_index: int) -> None:
        if body_index != self.selected_index or self.selected_group_id is not None:
            self._exit_focus()
            self.selected_group_id = None
            self.selected_index = body_index
            self.focus_group_id = self._group_id_for_body_index(body_index)
            self._load_body_editor(self.system.bodies[body_index])
            self._update_title()
            self._refresh_canvas()

    def _on_hierarchy_group_selected(self, _list_box: BodyHierarchyList, group_id: str) -> None:
        if group_id != self.selected_group_id:
            self._exit_focus()
            self.selected_group_id = group_id
            self._load_group_focus(group_id)
            self._update_title()
            self._refresh_canvas()

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
        self._exit_focus()
        self._select_body(body_index)
        self._refresh_canvas()

    def _on_canvas_group_selected(self, _canvas: SolarSystemCanvas, group_id: str) -> None:
        self._exit_focus()
        self._select_group(group_id)

    def _on_canvas_focus_target_selected(self, _canvas: SolarSystemCanvas, target: str) -> None:
        target_type, _, target_id = target.partition(":")
        self._exit_focus()
        if target_type == "group" and self._group_by_id(target_id) is not None:
            self._select_group(target_id)
        elif target_type == "body":
            self._select_body(self._body_index_for_id(target_id))
        self._update_title()
        self._refresh_canvas()

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

    def _prepare_system_for_save(self, system: SolarSystem) -> None:
        self.simulation.apply_to_bodies(system.bodies)

    def _on_system_saved(self, system: SolarSystem) -> None:
        self.system = system
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.simulation.increment_generation()
        self._update_title()

    def _clone_system(self, system: SolarSystem) -> SolarSystem:
        return SolarSystem.from_dict(system.to_dict())

    def _tick(self) -> bool:
        if self.playing:
            self._queue_simulation_job()
        return True

    def _advance(self, dt_s: float) -> None:
        settings = self._effective_settings()
        job = self._simulation_job(dt_s)
        result = playback.run_simulation_job(job)
        if self.simulation.apply_result(result, self.system.bodies, self.system.groups, settings):
            self._after_simulation_applied()

    def _queue_simulation_job(self) -> None:
        if self.closed or self.simulation_future is not None:
            return

        job = self._simulation_job(self._step_seconds())
        self.simulation_future = self.simulation_executor.submit(playback.run_simulation_job, job)
        self.simulation_future.add_done_callback(
            lambda future: GLib.idle_add(
                self._finish_simulation_job,
                future,
            )
        )

    def _simulation_job(self, dt_s: float) -> playback.SimulationJob:
        return self.simulation.create_job(
            self.system.bodies,
            self.system.groups,
            self._effective_settings(),
            self.selected_index,
            self.focus_group_id,
            self.focus_target,
            dt_s,
        )

    def _finish_simulation_job(self, future: Future) -> bool:
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

        if self.simulation.apply_result(
            result,
            self.system.bodies,
            self.system.groups,
            self._effective_settings(),
        ):
            self._after_simulation_applied()

        if self.playing:
            self._queue_simulation_job()

        return False

    def _after_simulation_applied(self) -> None:
        self._refresh_body_relationship_labels()
        if self.selected_group_id is not None:
            self._load_group_focus(self.selected_group_id)
        else:
            self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_title()
        self._update_time_label()
        self._refresh_canvas()

    def _step_seconds(self) -> float:
        return self.system_panel.step_seconds()

    def _max_step_seconds(self) -> float:
        settings = self._effective_settings()
        return self.simulation.max_step_seconds(
            self.system.bodies,
            self.system.groups,
            settings,
            self.selected_index,
            self.focus_group_id,
            self.focus_target,
        )

    def _update_title(self) -> None:
        self.window_title.set_title(self.system.name)
        settings = self._effective_settings()
        decision = self.simulation.physics_decision(
            self.system.bodies,
            self.system.groups,
            settings,
            self.selected_index,
            self.focus_group_id,
            self.focus_target,
            settings.visible_step_s,
        )
        max_step_days = decision.max_step_s / DAY
        policy = decision.policy.replace("_", " ")
        if settings.simulation_scope == "auto":
            policy = f"Auto: {policy}"
            if decision.auto_approximation:
                policy += " approximation"
            if self.simulation.auto_approximation_locked:
                policy += " (locked until reset)"
        self.window_title.set_subtitle(
            f"{self.system.epoch} - {policy}, max step {max_step_days:,.2f} days"
        )

    def _update_time_label(self) -> None:
        self.time_label.set_label(f"Simulation time: {format_elapsed_time(self.simulation.state.elapsed_s)}")

    def _refresh_canvas(self) -> None:
        self.canvas.set_scene(self._canvas_scene())

    def _canvas_scene(self) -> CanvasScene:
        settings = self._effective_settings()
        active_indices = self._active_body_indices()
        using_hybrid_focus = self._using_hybrid_focus()
        selected_group_center = (
            self._group_center(self.selected_group_id)
            if settings.view_mode == "follow_selected" and self.selected_group_id is not None
            else None
        )
        focused_bounds = (
            focused_canvas_bounds(self.system.bodies, active_indices)
            if self.focus_state is not None
            else None
        )
        inset_entities, inset_positions, inset_targets, focused_inset_id = self._inset_overview_data()
        return CanvasScene(
            bodies=self.system.bodies,
            active_indices=active_indices,
            selected_body_index=self.selected_index,
            selected_group_id=self.selected_group_id,
            selectable_group_ids={group.id for group in self.system.groups},
            view_mode=settings.view_mode,
            using_system_overview=self._using_system_overview(),
            using_hybrid_focus=using_hybrid_focus,
            using_focused_fit=self.focus_state is not None,
            selected_group_center=selected_group_center,
            focused_bounds=focused_bounds,
            trails=list(self.simulation.trails),
            overview_entities=self._overview_entities(),
            overview_positions=self._overview_positions(),
            overview_trails=dict(self.simulation.overview_trails),
            context_entities=self._context_entities(),
            context_positions=self._context_positions(),
            context_trails=dict(self.simulation.context_trails),
            inset_entities=inset_entities,
            inset_positions=inset_positions,
            inset_trails=dict(self.simulation.context_trails),
            inset_targets=inset_targets,
            focused_inset_entity_id=focused_inset_id,
        )

    def _inset_overview_data(self):
        if not self._using_hybrid_focus():
            return [], [], {}, None
        focused = focus_overview_entity(self.system.bodies, self.system.groups, self.focus_target)
        if focused is None:
            return [], [], {}, None
        context_entities = self._context_entities()
        context_positions = self._context_positions()
        entities = [focused, *context_entities]
        positions = [focused.position_m, *context_positions]
        group_ids = {group.id for group in self.system.groups}
        targets = {focused.id: self.focus_target}
        for entity in context_entities:
            if entity.id in group_ids:
                targets[entity.id] = f"group:{entity.id}"
            elif entity.id.startswith("context-"):
                targets[entity.id] = f"body:{entity.id.removeprefix('context-')}"
        return entities, positions, targets, focused.id

    def _overview_positions(self):
        return self.simulation.overview_positions(self.system.bodies, self.system.groups)

    def _active_body_indices(self) -> list[int]:
        settings = self._effective_settings()
        return self.simulation.display_body_indices(
            self.system.bodies,
            self.system.groups,
            settings,
            self.selected_index,
            self.focus_group_id,
            self.focus_target,
        )

    def _effective_simulation_scope(self) -> str:
        settings = self._effective_settings()
        return self.simulation.effective_simulation_scope(
            self.system.bodies,
            self.system.groups,
            settings,
            self.selected_index,
            self.focus_target,
        )

    def _using_system_overview(self) -> bool:
        settings = self._effective_settings()
        return self.simulation.using_system_overview(
            self.system.bodies,
            self.system.groups,
            settings,
            self.selected_index,
            self.focus_target,
        )

    def _using_hybrid_focus(self) -> bool:
        settings = self._effective_settings()
        return self.simulation.using_hybrid_focus(
            self.system.bodies,
            self.system.groups,
            settings,
            self.selected_index,
            self.focus_group_id,
            self.focus_target,
        )

    def _overview_entities(self):
        return self.simulation.overview_entities(self.system.bodies, self.system.groups)

    def _context_entities(self):
        return self.simulation.context_entities(self.system.bodies, self.system.groups, self.focus_target)

    def _context_positions(self):
        return self.simulation.context_positions(self.system.bodies, self.system.groups, self.focus_target)

    def _group_center(self, group_id: str) -> tuple[float, float] | None:
        return hierarchy.group_center(self.system.bodies, self.system.groups, group_id)

    def _distance_factor(self) -> float:
        return unit_factor(DISTANCE_UNITS, self.system.settings.distance_unit)

    def _configure_position_spins(self) -> None:
        unit = self.system.settings.distance_unit
        factor = self._distance_factor()
        self.body_inspector.configure_position_spins(self.system.bodies, unit, factor)
