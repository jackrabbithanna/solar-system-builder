# window.py
#
# Copyright 2026 Jackrabbithanna
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from . import hierarchy, playback
from .canvas import CanvasScene, SolarSystemCanvas
from .constants import AU, DAY, G
from .horizons import (
    HorizonsClient,
    HorizonsImportDraft,
    HorizonsSearchResult,
    add_imported_body,
    horizons_catalog_id,
    horizons_import_available,
    parse_required_physical_value,
    shift_horizons_frame_epoch,
)
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
    focused_visible_step_s,
    focus_overview_entity,
    focus_target_body_indices,
    format_elapsed_time,
    recommended_trail_sample_interval_s,
    unit_factor,
)
from .sidebar import BodyHierarchyList, BodyInspectorPanel, SystemPropertiesPanel
from .storage import Library
from .system_editing import (
    BODY_DEFAULTS,
    DEFAULT_ORBIT_RADIUS_M,
    BodyStateInput,
    add_body_from_state,
    add_star_system,
    create_system,
    delete_body_cascade,
    delete_group_cascade,
    deletion_summary_for_body,
    deletion_summary_for_group,
    update_body_from_state,
)
from .system_library import SystemLibraryController

MAX_AUTOMATIC_SIDEBAR_WIDTH = 520
SIDEBAR_HORIZONTAL_MARGINS = 24
WINDOW_MONITOR_MARGIN = 32


@Gtk.Template(resource_path="/io/github/jackrabbithanna/solarsystembuilder/window.ui")
class SolarSystemBuilderWindow(Adw.ApplicationWindow):
    __gtype_name__ = "SolarSystemBuilderWindow"

    canvas = Gtk.Template.Child()
    main_paned = Gtk.Template.Child()
    settings_controls_box = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    step_back_button = Gtk.Template.Child()
    reset_button = Gtk.Template.Child()
    step_forward_button = Gtk.Template.Child()
    zoom_out_button = Gtk.Template.Child()
    reset_zoom_button = Gtk.Template.Child()
    zoom_in_button = Gtk.Template.Child()
    new_system_button = Gtk.Template.Child()
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
    system_description_entry = Gtk.Template.Child()
    reference_frame_label = Gtk.Template.Child()
    preset_edit_box = Gtk.Template.Child()
    duplicate_to_edit_button = Gtk.Template.Child()
    delete_system_button = Gtk.Template.Child()
    add_body_menu_button = Gtk.Template.Child()
    add_star_system_button = Gtk.Template.Child()
    add_star_button = Gtk.Template.Child()
    add_planet_button = Gtk.Template.Child()
    add_dwarf_planet_button = Gtk.Template.Child()
    add_moon_button = Gtk.Template.Child()
    add_comet_button = Gtk.Template.Child()
    add_asteroid_button = Gtk.Template.Child()
    search_horizons_button = Gtk.Template.Child()
    delete_selected_button = Gtk.Template.Child()
    selected_name_entry = Gtk.Template.Child()
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
    body_kind_dropdown = Gtk.Template.Child()
    body_parent_dropdown = Gtk.Template.Child()
    mass_entry = Gtk.Template.Child()
    radius_entry = Gtk.Template.Child()
    body_color_button = Gtk.Template.Child()
    body_visible_switch = Gtk.Template.Child()
    body_trail_switch = Gtk.Template.Child()
    x_label = Gtk.Template.Child()
    x_spin = Gtk.Template.Child()
    y_label = Gtk.Template.Child()
    y_spin = Gtk.Template.Child()
    z_label = Gtk.Template.Child()
    z_spin = Gtk.Template.Child()
    vx_spin = Gtk.Template.Child()
    vy_spin = Gtk.Template.Child()
    vz_spin = Gtk.Template.Child()
    state_origin_label = Gtk.Template.Child()
    apply_body_button = Gtk.Template.Child()
    group_properties_expander = Gtk.Template.Child()
    group_kind_entry = Gtk.Template.Child()
    group_parent_dropdown = Gtk.Template.Child()
    apply_group_button = Gtk.Template.Child()
    body_properties_grid = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.system = load_builtin_solar_system()
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.simulation = playback.SimulationSession.from_bodies(self.system.bodies)
        self.selected_index = 0
        self.focus_group_id: str | None = None
        self.focus_target: str | None = None
        self.focus_state: FocusState | None = None
        self.focus_fit_session = 0
        self.selected_group_id: str | None = None
        self.playing = False
        self.editing = False
        self.dirty = False
        self.allow_close = False
        self.closed = False
        self.simulation_future: Future | None = None
        self.simulation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation")
        self.horizons_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="horizons")
        self.horizons_client = HorizonsClient()
        self.horizons_generation = 0
        self.zoom_factor = 1.0
        self.timer_id = GLib.timeout_add(33, self._tick)
        self.sidebar_resize_id = 0
        self.compact_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 760px")
        )
        self.compact_breakpoint.add_setter(
            self.main_paned,
            "orientation",
            Gtk.Orientation.VERTICAL,
        )
        self.compact_breakpoint.add_setter(self.main_paned, "position", 420)
        self.compact_breakpoint.add_setter(
            self.settings_controls_box,
            "orientation",
            Gtk.Orientation.VERTICAL,
        )
        self.add_breakpoint(self.compact_breakpoint)

        self.system_panel = SystemPropertiesPanel(
            self.system_name_entry,
            self.system_description_entry,
            self.reference_frame_label,
            self.preset_edit_box,
            self.delete_system_button,
            self.speed_spin,
            self.time_unit_dropdown,
            self.accuracy_dropdown,
            self.view_mode_dropdown,
            self.simulation_scope_dropdown,
            self.distance_unit_dropdown,
        )
        self.body_inspector = BodyInspectorPanel(
            self.selected_name_entry,
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
            self.group_properties_expander,
            self.group_kind_entry,
            self.group_parent_dropdown,
            self.apply_group_button,
            self.body_properties_grid,
            self.body_kind_dropdown,
            self.body_parent_dropdown,
            self.mass_entry,
            self.radius_entry,
            self.body_color_button,
            self.body_visible_switch,
            self.body_trail_switch,
            self.x_label,
            self.x_spin,
            self.y_label,
            self.y_spin,
            self.z_label,
            self.z_spin,
            self.vx_spin,
            self.vy_spin,
            self.vz_spin,
            self.state_origin_label,
            self.apply_body_button,
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
            self._request_system_load,
            self._on_system_saved,
            self._on_system_mutated,
            self._load_system,
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
        self.new_system_button.connect("clicked", self._on_new_system_clicked)
        self.duplicate_to_edit_button.connect("clicked", lambda *_args: self.duplicate_button.emit("clicked"))
        self.add_star_system_button.connect("clicked", self._on_add_star_system_clicked)
        self.add_star_button.connect("clicked", self._on_add_star_clicked)
        self.add_planet_button.connect("clicked", self._on_add_planet_clicked)
        self.add_dwarf_planet_button.connect(
            "clicked",
            lambda *_args: self._on_add_orbiting_kind("dwarf planet"),
        )
        self.add_moon_button.connect("clicked", self._on_add_moon_clicked)
        self.add_comet_button.connect("clicked", lambda *_args: self._on_add_orbiting_kind("comet"))
        self.add_asteroid_button.connect("clicked", lambda *_args: self._on_add_orbiting_kind("asteroid"))
        self.search_horizons_button.connect("clicked", self._on_search_horizons_clicked)
        self.delete_selected_button.connect("clicked", self._on_delete_selected_clicked)
        self.body_list.connect("body-selected", self._on_hierarchy_body_selected)
        self.body_list.connect("group-selected", self._on_hierarchy_group_selected)
        self.system_panel.connect("time-step-changed", self._on_time_step_changed)
        self.system_panel.connect("accuracy-changed", self._on_accuracy_changed)
        self.system_panel.connect("view-mode-changed", self._on_view_mode_changed)
        self.system_panel.connect("simulation-scope-changed", self._on_simulation_scope_changed)
        self.system_panel.connect("distance-unit-changed", self._on_distance_unit_changed)
        self.system_panel.connect("system-description-edited", self._on_system_description_edit)
        self.body_inspector.connect("body-edited", self._on_body_edit)
        self.body_inspector.connect("group-edited", self._on_group_edit)
        self.body_inspector.connect("name-edited", self._on_selected_name_edit)
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
        self._sync_structural_controls()
        self._set_dirty(False)

    def do_close_request(self):
        if self.dirty and not self.allow_close:
            self._resolve_dirty_before(
                self._close_after_dirty_resolution,
                cancel_action=lambda: None,
            )
            return True
        self.closed = True
        self.playing = False
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = 0
        if self.sidebar_resize_id:
            GLib.source_remove(self.sidebar_resize_id)
            self.sidebar_resize_id = 0
        self.simulation_executor.shutdown(wait=False, cancel_futures=True)
        self.horizons_generation += 1
        self.horizons_executor.shutdown(wait=False, cancel_futures=True)
        return False

    def _close_after_dirty_resolution(self) -> None:
        self.allow_close = True
        self.close()

    def _request_system_load(self, system: SolarSystem) -> None:
        if system.id == self.system.id:
            return
        self._resolve_dirty_before(
            lambda: self._load_system(system),
            cancel_action=lambda: self.system_library.refresh(self.system),
        )

    def _resolve_dirty_before(self, action, *, cancel_action=None) -> None:
        if not self.dirty:
            action()
            return
        dialog = Adw.AlertDialog.new(
            "Save Changes?",
            f"Save changes to '{self.system.name}' before continuing?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_close_response("cancel")
        dialog.set_default_response("save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def finish(_dialog, response: str) -> None:
            if response == "save":
                # Preserve any pending selector state until the guarded action completes.
                self.system_library.save_current(refresh_dropdown=False)
                action()
            elif response == "discard":
                self._set_dirty(False)
                action()
            elif cancel_action is not None:
                cancel_action()

        dialog.connect("response", finish)
        dialog.present(self)

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
            self._set_dirty(False)
        self.simulation.replace_bodies(self.system.bodies, elapsed_s=0.0)
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
        if (
            self.closed
            or self.is_maximized()
            or self.is_fullscreen()
            or self.main_paned.get_orientation() == Gtk.Orientation.VERTICAL
        ):
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
        self._sync_structural_controls()

    def _select_group(self, group_id: str) -> None:
        if self._group_by_id(group_id) is None:
            return
        self.body_list.select_group(group_id)
        if self.selected_group_id != group_id:
            self.focus_target = None
            self.selected_group_id = group_id
            self._load_group_focus(group_id)
            self._update_title()
            self._sync_structural_controls()
        self._refresh_canvas()

    def _load_body_editor(self, body: Body) -> None:
        self.body_inspector.set_editing(True)
        self.body_inspector.show_group_properties(False)
        distance_factor = self._distance_factor()
        editable = self._current_system_editable()
        self._set_body_editor_sensitive(editable)
        self.body_inspector.set_name_sensitive(editable)
        self.selected_group_id = None
        self.body_inspector.set_selected_name(body.name)
        self.body_inspector.set_parent_options(
            [candidate for candidate in self.system.bodies if candidate.id != body.id],
            body.parent_id,
        )
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
        self.body_inspector.show_group_properties(True)
        self._set_body_editor_sensitive(False)
        self.body_inspector.set_name_sensitive(self._current_system_editable())
        self.body_inspector.set_selected_name(group.name)
        descendant_ids = self._descendant_group_ids(group.id)
        parent_options = [
            candidate
            for candidate in self.system.groups
            if candidate.id != group.id and candidate.id not in descendant_ids
        ]
        self.body_inspector.set_group_values(
            group,
            parent_options,
            self._current_system_editable(),
        )
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

    def _current_system_editable(self) -> bool:
        return self.system_library.is_user_saved(self.system)

    def _orbit_editor_widgets(self):
        return self.body_inspector.orbit_editor_widgets()

    def _set_orbit_editor_sensitive(self, sensitive: bool) -> None:
        self.body_inspector.set_orbit_editor_sensitive(sensitive)

    def _load_orbit_editor(self, body: Body | None) -> None:
        parent = None
        if body is not None and body.parent_id is not None:
            parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)

        self._show_group_orbit_controls(False)
        editable = self._current_system_editable()
        self.body_inspector.set_orbit_expander_sensitive(body is not None)
        self._set_orbit_editor_sensitive(editable and body is not None and parent is not None)
        self.body_inspector.set_generate_body_orbit_sensitive(
            editable and body is not None and parent is not None
        )
        if body is None or parent is None:
            if body is None:
                self._load_orbit_values(None, None, 0.0)
                self.body_inspector.set_orbit_status("")
            elif binary_group := self._direct_binary_group_for_body(body):
                self._load_orbit_values(binary_group.orbit, binary_group.data_source, 0.0)
                self._set_orbit_editor_sensitive(editable)
                self.body_inspector.set_generate_body_orbit_sensitive(False)
                self.body_inspector.configure_binary_orbit_button(True, editable)
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
        editable = self._current_system_editable()
        self.body_inspector.set_orbit_expander_sensitive(True)
        self._show_group_orbit_controls(True)
        self._set_orbit_editor_sensitive(editable)
        self.body_inspector.set_generate_body_orbit_sensitive(False)
        self._populate_orbit_target_dropdown(group)
        self._load_orbit_values(group.orbit, group.data_source, 0.0)
        has_target = bool(self.body_inspector.orbit_target_options)
        self.body_inspector.set_group_orbit_target_sensitive(
            editable and has_target,
            editable and self._group_direct_body_indices(group) is not None,
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
        if not self.system.bodies or not self._current_system_editable():
            return
        selected_body_id = self.system.bodies[self.selected_index].id
        body = self.system.bodies[self.selected_index]
        distance_factor = self._distance_factor()
        values = self.body_inspector.edited_body_values(distance_factor)
        if values is None:
            self._show_error_dialog("Cannot Apply Body Changes", "Mass and radius must be positive numbers.")
            return
        (
            kind,
            parent_id,
            mass,
            radius,
            color,
            visible,
            trail_enabled,
            x_m,
            y_m,
            z_m,
            vx_mps,
            vy_mps,
            vz_mps,
        ) = values
        physics_changed = (
            kind != body.kind
            or parent_id != body.parent_id
            or mass != body.mass_kg
            or radius != body.radius_m
            or [x_m, y_m, z_m] != body.position_m
            or [vx_mps, vy_mps, vz_mps] != body.velocity_mps
        )
        group = self._group_for_body_id(body.id)
        try:
            body = update_body_from_state(
                self.system,
                body.id,
                BodyStateInput(
                    name=body.name,
                    kind=kind,
                    mass_kg=mass,
                    radius_m=radius,
                    position_m=(x_m, y_m, z_m),
                    velocity_mps=(vx_mps, vy_mps, vz_mps),
                    color=color,
                    parent_id=parent_id,
                    group_id=group.id if group is not None and kind == "star" else None,
                    visible=visible,
                    trail_enabled=trail_enabled,
                ),
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Apply Body Changes", str(error))
            self._load_body_editor(self.system.bodies[self.selected_index])
            return
        if physics_changed:
            body.orbit = None
            body.data_source = None
            body.state_origin = "cartesian"
            self.playing = False
            self.play_button.set_icon_name("media-playback-start-symbolic")
            self._clear_dynamic_simulation_state()
        self.simulation.replace_bodies(self.system.bodies)
        self._populate_body_list()
        self._select_body(self._body_index_for_id(selected_body_id))
        self._mark_dirty()
        self._update_title()
        self._refresh_canvas()

    def _on_group_edit(self, *_args) -> None:
        if self.selected_group_id is None or not self._current_system_editable():
            return
        values = self.body_inspector.edited_group_values()
        if values is None:
            self._show_error_dialog("Cannot Apply Group Changes", "Group kind is required.")
            return
        kind, parent_group_id = values
        candidate = self._clone_system(self.system)
        candidate_group = hierarchy.group_by_id(candidate.groups, self.selected_group_id)
        if candidate_group is None:
            return
        candidate_group.kind = kind
        candidate_group.parent_group_id = parent_group_id
        try:
            candidate.validate()
        except ModelError as error:
            self._show_error_dialog("Cannot Apply Group Changes", str(error))
            self._load_group_focus(self.selected_group_id)
            return
        group = self._group_by_id(self.selected_group_id)
        if group is None:
            return
        if group.kind == kind and group.parent_group_id == parent_group_id:
            return
        group.kind = kind
        group.parent_group_id = parent_group_id
        self._populate_body_list()
        self._select_group(group.id)
        self.system_library.refresh(self.system)
        self._mark_dirty()
        self._update_title()
        self._refresh_canvas()

    def _on_selected_name_edit(self, _panel, name: str) -> None:
        if not name:
            if self.selected_group_id is not None:
                self._load_group_focus(self.selected_group_id)
            elif self.system.bodies:
                self._load_body_editor(self.system.bodies[self.selected_index])
            return

        try:
            self._ensure_editable_for_structural_edit()
            if self.selected_group_id is not None:
                group = self._group_by_id(self.selected_group_id)
                if group is None:
                    return
                if group.name == name:
                    return
                group.name = name
                self.system.validate()
                self._populate_body_list()
                self._select_group(group.id)
            else:
                if not self.system.bodies:
                    return
                body = self.system.bodies[self.selected_index]
                if body.name == name:
                    return
                body.name = name
                self.system.validate()
                self._populate_body_list()
                self._select_body(self.selected_index)
        except ModelError as error:
            self._show_error_dialog("Cannot Rename", str(error))
            if self.selected_group_id is not None:
                self._load_group_focus(self.selected_group_id)
            elif self.system.bodies:
                self._load_body_editor(self.system.bodies[self.selected_index])
            return

        self.system_library.refresh(self.system)
        self._mark_dirty()
        self._refresh_body_relationship_labels()
        self._update_title()
        self._refresh_canvas()

    def _on_system_description_edit(self, _panel, description: str) -> None:
        if not self._current_system_editable() or description == self.system.description:
            self._load_system_editor()
            return
        self.system.description = description
        self._mark_dirty()

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
        self._mark_dirty_if_editable()
        self._update_title()

    def _on_accuracy_changed(self, _panel, accuracy_profile: str) -> None:
        self.system.settings.accuracy_profile = accuracy_profile
        self._mark_dirty_if_editable()
        if self.focus_state is not None and not self.focus_state.step_manually_overridden:
            focused_bodies = playback.focused_timing_bodies(
                self.system.bodies,
                self.system.groups,
                self.focus_state.target,
                collapse_moons=self.system.settings.simulation_scope != "full_nbody",
            )
            self.focus_state.visible_step_s = focused_visible_step_s(focused_bodies, accuracy_profile)
            self.focus_state.trail_sample_interval_s = recommended_trail_sample_interval_s(
                self.focus_state.visible_step_s
            )
            self._load_settings_editor()
        self._update_title()

    def _on_view_mode_changed(self, _panel, view_mode: str) -> None:
        self._exit_focus(reload_settings=False)
        self.system.settings.view_mode = view_mode
        self._mark_dirty_if_editable()
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
        self._mark_dirty_if_editable()
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
        self._mark_dirty_if_editable()
        self._load_settings_editor()
        self._update_title()
        self._refresh_canvas()

    def _on_distance_unit_changed(self, _panel, distance_unit: str) -> None:
        self.system.settings.distance_unit = distance_unit
        self._mark_dirty_if_editable()
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
        self._mark_dirty()

    def _on_search_horizons_clicked(self, _button) -> None:
        if not self._current_system_editable() or not horizons_import_available(self.system):
            self._show_error_dialog(
                "Horizons Unavailable",
                "Create or duplicate a Sol system with a compatible reference frame first.",
            )
            return
        self._stop_playback_for_structural_work()
        query_entry = Gtk.SearchEntry()
        search_button = Gtk.Button(label="Search")
        search_button.add_css_class("suggested-action")
        spinner = Gtk.Spinner()
        results_dropdown = Gtk.DropDown.new_from_strings([])
        status_label = Gtk.Label(xalign=0, wrap=True)
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        query_entry.set_hexpand(True)
        search_row.append(query_entry)
        search_row.append(search_button)
        search_row.append(spinner)
        dialog = self._editor_dialog(
            "Search JPL Horizons",
            "Fetch State",
            [("Search", search_row), ("Results", results_dropdown), ("Status", status_label)],
        )
        dialog.set_response_enabled("create", False)
        state = {"results": []}

        def start_search(*_args) -> None:
            query = query_entry.get_text().strip()
            if not query:
                status_label.set_label("Enter a body name, designation, or SPK ID.")
                return
            self.horizons_generation += 1
            generation = self.horizons_generation
            search_button.set_sensitive(False)
            spinner.start()
            status_label.set_label("Searching...")
            future = self.horizons_executor.submit(self.horizons_client.search, query)
            future.add_done_callback(
                lambda completed: GLib.idle_add(
                    self._finish_horizons_search,
                    completed,
                    generation,
                    dialog,
                    results_dropdown,
                    status_label,
                    search_button,
                    spinner,
                    state,
                )
            )

        search_button.connect("clicked", start_search)
        query_entry.connect("activate", start_search)

        def finish_search_dialog(_dialog, response: str) -> None:
            if response != "create":
                self.horizons_generation += 1
                return
            selected = results_dropdown.get_selected()
            results = state["results"]
            if selected == Gtk.INVALID_LIST_POSITION or selected >= len(results):
                self._show_error_dialog("Cannot Fetch Body", "Select a Horizons search result.")
                return
            self._fetch_horizons_import(results[selected])

        dialog.connect("response", finish_search_dialog)
        dialog.present(self)
        query_entry.grab_focus()

    def _finish_horizons_search(
        self,
        future: Future,
        generation: int,
        dialog,
        results_dropdown,
        status_label,
        search_button,
        spinner,
        state: dict,
    ) -> bool:
        if self.closed or generation != self.horizons_generation:
            return GLib.SOURCE_REMOVE
        spinner.stop()
        search_button.set_sensitive(True)
        try:
            all_results = future.result()
        except Exception as error:
            state["results"] = []
            results_dropdown.set_model(Gtk.StringList.new([]))
            dialog.set_response_enabled("create", False)
            status_label.set_label(str(error))
            return GLib.SOURCE_REMOVE
        results = [result for result in all_results if result.supported and result.suggested_kind != "star"]
        state["results"] = results
        results_dropdown.set_model(
            Gtk.StringList.new(
                [f"{result.name} - {result.object_type} - SPK {result.spkid}" for result in results]
            )
        )
        if results:
            results_dropdown.set_selected(0)
            omitted = len(all_results) - len(results)
            suffix = f" {omitted} unsupported result(s) omitted." if omitted else ""
            status_label.set_label(f"{len(results)} supported result(s).{suffix}")
            dialog.set_response_enabled("create", True)
        else:
            status_label.set_label("No supported physical bodies found.")
            dialog.set_response_enabled("create", False)
        return GLib.SOURCE_REMOVE

    def _fetch_horizons_import(self, result: HorizonsSearchResult) -> None:
        parent_options = self._horizons_parent_options(result.suggested_kind or "planet")
        if not parent_options:
            self._show_error_dialog(
                "Cannot Import Body",
                "The system does not contain an eligible parent for this body.",
            )
            return
        parent = parent_options[0]
        parent_catalog_id = horizons_catalog_id(parent)
        if result.suggested_kind == "moon" and parent_catalog_id is None:
            self._show_error_dialog(
                "Cannot Import Moon",
                f"{parent.name} does not have a JPL Horizons catalog id.",
            )
            return
        try:
            request_frame = shift_horizons_frame_epoch(
                self.system.reference_frame,
                self.simulation.state.elapsed_s,
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Fetch Body", str(error))
            return
        self.horizons_generation += 1
        generation = self.horizons_generation
        progress = Adw.AlertDialog.new("Fetching Horizons State", result.name)
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        spinner.set_margin_top(12)
        spinner.set_margin_bottom(12)
        progress.set_extra_child(spinner)
        progress.add_response("cancel", "Cancel")
        progress.set_close_response("cancel")
        progress.connect("response", lambda *_args: setattr(self, "horizons_generation", self.horizons_generation + 1))
        progress.present(self)
        future = self.horizons_executor.submit(
            self.horizons_client.fetch_import,
            result,
            request_frame,
            parent_catalog_id=parent_catalog_id,
        )
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._finish_horizons_fetch,
                completed,
                generation,
                progress,
                parent.id,
            )
        )

    def _finish_horizons_fetch(
        self,
        future: Future,
        generation: int,
        progress,
        parent_id: str,
    ) -> bool:
        if self.closed or generation != self.horizons_generation:
            return GLib.SOURCE_REMOVE
        progress.close()
        try:
            draft = future.result()
        except Exception as error:
            self._show_error_dialog("Horizons Request Failed", str(error))
            return GLib.SOURCE_REMOVE
        self._show_horizons_review(draft, parent_id)
        return GLib.SOURCE_REMOVE

    def _show_horizons_review(self, draft: HorizonsImportDraft, parent_id: str) -> None:
        parent = next(body for body in self.system.bodies if body.id == parent_id)
        name_entry = Gtk.Entry(text=draft.name)
        kinds = (
            ("moon",)
            if draft.kind == "moon"
            else ("planet", "dwarf planet", "comet", "asteroid")
        )
        kind_dropdown = Gtk.DropDown.new_from_strings([kind.title() for kind in kinds])
        kind_dropdown.set_selected(kinds.index(draft.kind) if draft.kind in kinds else 0)
        mass_entry = Gtk.Entry(
            text=f"{draft.mass_kg:.12g}" if draft.mass_kg is not None else ""
        )
        mass_entry.set_placeholder_text("Required positive number")
        radius_entry = Gtk.Entry(
            text=f"{draft.radius_m:.12g}" if draft.radius_m is not None else ""
        )
        radius_entry.set_placeholder_text("Required positive number")
        color_entry = Gtk.Entry(text=BODY_DEFAULTS[draft.kind][2])
        physical_status = Gtk.Label(xalign=0, wrap=True)
        if draft.vector_center_catalog_id is None:
            vector_text = (
                "System-frame position (m): "
                + ", ".join(f"{value:.6g}" for value in draft.position_m)
                + "\nSystem-frame velocity (m/s): "
                + ", ".join(f"{value:.6g}" for value in draft.velocity_mps)
            )
        else:
            system_position = [
                parent.position_m[axis] + draft.position_m[axis]
                for axis in range(3)
            ]
            system_velocity = [
                parent.velocity_mps[axis] + draft.velocity_mps[axis]
                for axis in range(3)
            ]
            vector_text = (
                f"Position relative to {parent.name} (m): "
                + ", ".join(f"{value:.6g}" for value in draft.position_m)
                + f"\nVelocity relative to {parent.name} (m/s): "
                + ", ".join(f"{value:.6g}" for value in draft.velocity_mps)
                + "\nSystem-frame position (m): "
                + ", ".join(f"{value:.6g}" for value in system_position)
                + "\nSystem-frame velocity (m/s): "
                + ", ".join(f"{value:.6g}" for value in system_velocity)
            )
        vector_label = Gtk.Label(
            label=vector_text,
            xalign=0,
            wrap=True,
            selectable=True,
        )
        rows = [
            ("Name", name_entry),
            ("Kind", kind_dropdown),
            ("Parent", Gtk.Label(label=parent.name, xalign=0)),
            ("Mass (kg)", mass_entry),
            ("Radius (m)", radius_entry),
            ("Physical Data", physical_status),
            ("Color", color_entry),
            ("Imported State", vector_label),
            ("Source", Gtk.Label(label=f"JPL Horizons - SPK {draft.catalog_id}", xalign=0)),
        ]
        if draft.warning:
            rows.append(("Import Note", Gtk.Label(label=draft.warning, xalign=0, wrap=True)))
        dialog = self._editor_dialog(
            "Review Horizons Body",
            "Add Body",
            rows,
        )

        def update_physical_validation(*_args) -> None:
            physical_status.remove_css_class("error")
            physical_status.remove_css_class("success")
            try:
                parse_required_physical_value(mass_entry.get_text(), "Mass")
                parse_required_physical_value(radius_entry.get_text(), "Radius")
            except ModelError as error:
                message = str(error)
                if draft.physical_data_notes:
                    message += "\nPrefilled: " + "; ".join(draft.physical_data_notes)
                physical_status.set_label(message)
                physical_status.add_css_class("error")
                dialog.set_response_enabled("create", False)
            else:
                if draft.physical_data_notes:
                    physical_status.set_label(
                        "Prefilled: " + "; ".join(draft.physical_data_notes)
                    )
                    physical_status.add_css_class("success")
                else:
                    physical_status.set_label("")
                dialog.set_response_enabled("create", True)

        mass_entry.connect("changed", update_physical_validation)
        radius_entry.connect("changed", update_physical_validation)
        update_physical_validation()

        def add_reviewed(_dialog, response: str) -> None:
            if response != "create":
                return
            try:
                mass = parse_required_physical_value(mass_entry.get_text(), "Mass")
                radius = parse_required_physical_value(radius_entry.get_text(), "Radius")
                selected_kind = kind_dropdown.get_selected()
                kind = kinds[selected_kind] if selected_kind < len(kinds) else draft.kind
                reviewed = HorizonsImportDraft(
                    name=name_entry.get_text().strip() or draft.name,
                    kind=draft.kind,
                    catalog_id=draft.catalog_id,
                    position_m=draft.position_m,
                    velocity_mps=draft.velocity_mps,
                    orbit=draft.orbit,
                    data_source=draft.data_source,
                    vector_center_catalog_id=draft.vector_center_catalog_id,
                    mass_kg=mass,
                    radius_m=radius,
                    physical_data_notes=draft.physical_data_notes,
                    warning=draft.warning,
                )
                body_id = add_imported_body(
                    self.system,
                    reviewed,
                    mass_kg=mass,
                    radius_m=radius,
                    parent_id=parent_id,
                    kind=kind,
                    color=color_entry.get_text().strip() or BODY_DEFAULTS[kind][2],
                )
            except ModelError as error:
                self._show_error_dialog("Cannot Add Horizons Body", str(error))
                return
            self._after_structural_edit(selected_body_id=body_id)

        dialog.connect("response", add_reviewed)
        dialog.present(self)

    def _horizons_parent_options(self, kind: str) -> list[Body]:
        allowed = {"planet", "dwarf planet"} if kind == "moon" else {"star"}
        selected = self._selected_context_bodies()
        preferred = [body for body in selected if body.kind in allowed]
        return preferred or [body for body in self.system.bodies if body.kind in allowed]

    def _show_error_dialog(self, title: str, message: str) -> None:
        dialog = Adw.AlertDialog.new(title, message)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(self)

    def _on_new_system_clicked(self, _button) -> None:
        if self.dirty:
            self._resolve_dirty_before(lambda: self._on_new_system_clicked(None))
            return
        name_entry = Gtk.Entry()
        name_entry.set_text("New System")
        description_entry = Gtk.Entry()
        description_entry.set_placeholder_text("Optional description")
        epoch_entry = Gtk.Entry()
        epoch_entry.set_text(f"{date.today().isoformat()} 00:00:00")
        workflow_dropdown = Gtk.DropDown.new_from_strings(
            ["From Preset", "Sol with JPL Horizons", "Custom System"]
        )
        workflow_dropdown.set_selected(2)
        presets = load_builtin_solar_systems()
        preset_dropdown = Gtk.DropDown.new_from_strings([preset.name for preset in presets])
        preset_dropdown.set_selected(0)
        starter_dropdown = Gtk.DropDown.new_from_strings(
            ["Single Star", "Binary Star", "Hierarchical"]
        )
        starter_dropdown.set_selected(0)

        primary_mass, primary_radius, primary_color = BODY_DEFAULTS["star"]
        secondary_mass = 0.8 * primary_mass
        total_mass = primary_mass + secondary_mass
        relative_speed = math.sqrt(G * total_mass / AU)
        primary_position_x = -AU * secondary_mass / total_mass
        secondary_position_x = AU * primary_mass / total_mass
        primary_velocity_y = -relative_speed * secondary_mass / total_mass
        secondary_velocity_y = relative_speed * primary_mass / total_mass

        primary_controls = {
            "name": Gtk.Entry(text="Primary Star"),
            "mass": Gtk.Entry(text=f"{primary_mass:.12g}"),
            "radius": Gtk.Entry(text=f"{primary_radius:.12g}"),
            "position": Gtk.Entry(text="0, 0, 0"),
            "velocity": Gtk.Entry(text="0, 0, 0"),
        }
        secondary_controls = {
            "name": Gtk.Entry(text="Secondary Star"),
            "mass": Gtk.Entry(text=f"{secondary_mass:.12g}"),
            "radius": Gtk.Entry(text=f"{0.85 * primary_radius:.12g}"),
            "position": Gtk.Entry(text=f"{secondary_position_x:.12g}, 0, 0"),
            "velocity": Gtk.Entry(text=f"0, {secondary_velocity_y:.12g}, 0"),
        }
        binary_primary_position = f"{primary_position_x:.12g}, 0, 0"
        binary_primary_velocity = f"0, {primary_velocity_y:.12g}, 0"

        custom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        custom_box.append(starter_dropdown)
        custom_box.append(Gtk.Label(label="Primary Star", xalign=0, css_classes=["heading"]))
        primary_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        for row, (label, key) in enumerate(
            (
                ("Name", "name"),
                ("Mass (kg)", "mass"),
                ("Radius (m)", "radius"),
                ("Position XYZ (m)", "position"),
                ("Velocity XYZ (m/s)", "velocity"),
            )
        ):
            self._attach_dialog_row(primary_grid, row, label, primary_controls[key])
        custom_box.append(primary_grid)
        secondary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        secondary_box.append(Gtk.Label(label="Secondary Star", xalign=0, css_classes=["heading"]))
        secondary_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        for row, (label, key) in enumerate(
            (
                ("Name", "name"),
                ("Mass (kg)", "mass"),
                ("Radius (m)", "radius"),
                ("Position XYZ (m)", "position"),
                ("Velocity XYZ (m/s)", "velocity"),
            )
        ):
            self._attach_dialog_row(secondary_grid, row, label, secondary_controls[key])
        secondary_box.append(secondary_grid)
        secondary_box.set_visible(False)
        custom_box.append(secondary_box)
        custom_scroller = Gtk.ScrolledWindow()
        custom_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        custom_scroller.set_min_content_height(320)
        custom_scroller.set_max_content_height(430)
        custom_scroller.set_propagate_natural_height(True)
        custom_scroller.set_child(custom_box)

        config_stack = Gtk.Stack()
        config_stack.add_named(preset_dropdown, "preset")
        config_stack.add_named(Gtk.Label(label="Sun", xalign=0), "sol")
        config_stack.add_named(custom_scroller, "custom")
        config_stack.set_visible_child_name("custom")

        def update_creation_configuration(*_args) -> None:
            workflow = workflow_dropdown.get_selected()
            config_stack.set_visible_child_name(("preset", "sol", "custom")[min(workflow, 2)])
            epoch_entry.set_sensitive(workflow != 0)

        def update_secondary_visibility(*_args) -> None:
            binary = starter_dropdown.get_selected() == 1
            secondary_box.set_visible(binary)
            current_position = primary_controls["position"].get_text()
            current_velocity = primary_controls["velocity"].get_text()
            if binary and current_position == "0, 0, 0" and current_velocity == "0, 0, 0":
                primary_controls["position"].set_text(binary_primary_position)
                primary_controls["velocity"].set_text(binary_primary_velocity)
            elif not binary and current_position == binary_primary_position and current_velocity == binary_primary_velocity:
                primary_controls["position"].set_text("0, 0, 0")
                primary_controls["velocity"].set_text("0, 0, 0")

        workflow_dropdown.connect("notify::selected", update_creation_configuration)
        starter_dropdown.connect("notify::selected", update_secondary_visibility)
        dialog = self._editor_dialog(
            "Create New System",
            "Create",
            [
                ("Name", name_entry),
                ("Description", description_entry),
                ("Epoch", epoch_entry),
                ("Workflow", workflow_dropdown),
                ("Configuration", config_stack),
            ],
        )
        dialog.connect(
            "response",
            lambda dialog, response: self._finish_new_system_dialog(
                dialog,
                response,
                name_entry,
                description_entry,
                epoch_entry,
                workflow_dropdown,
                preset_dropdown,
                starter_dropdown,
                presets,
                primary_controls,
                secondary_controls,
                primary_color,
            ),
        )
        dialog.present(self)

    def _finish_new_system_dialog(
        self,
        _dialog,
        response: str,
        name_entry,
        description_entry,
        epoch_entry,
        workflow_dropdown,
        preset_dropdown,
        starter_dropdown,
        presets: list[SolarSystem],
        primary_controls: dict,
        secondary_controls: dict,
        primary_color: str,
    ) -> None:
        if response != "create":
            return
        try:
            name = name_entry.get_text().strip()
            if not name:
                raise ModelError("system name is required")
            if any(
                saved.name.casefold() == name.casefold()
                for saved in self.system_library.library.list_systems()
            ):
                raise ModelError("a saved system with this name already exists")
            workflow = workflow_dropdown.get_selected()
            if workflow == 0:
                selected = preset_dropdown.get_selected()
                if selected >= len(presets):
                    raise ModelError("select a preset")
                system = presets[selected].duplicate(name)
                if description_entry.get_text().strip():
                    system.description = description_entry.get_text().strip()
            elif workflow == 1:
                system = create_system(
                    name,
                    "sol",
                    description=description_entry.get_text().strip() or None,
                    epoch=epoch_entry.get_text(),
                )
            else:
                starters = ("single_star", "binary_star", "hierarchical")
                selected = starter_dropdown.get_selected()
                starter = starters[selected] if selected < len(starters) else "single_star"
                primary_state = self._star_state_from_dialog(primary_controls, primary_color)
                secondary_state = (
                    self._star_state_from_dialog(secondary_controls, "#ffb703")
                    if starter == "binary_star"
                    else None
                )
                system = create_system(
                    name,
                    starter,
                    description=description_entry.get_text().strip() or None,
                    epoch=epoch_entry.get_text(),
                    primary_state=primary_state,
                    secondary_state=secondary_state,
                )
        except (ModelError, ValueError) as error:
            self._show_error_dialog("Cannot Create System", str(error))
            return
        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.system_library.save_new_system(system)

    def _star_state_from_dialog(self, controls: dict, color: str) -> BodyStateInput:
        return BodyStateInput(
            name=controls["name"].get_text(),
            kind="star",
            mass_kg=float(controls["mass"].get_text()),
            radius_m=float(controls["radius"].get_text()),
            position_m=self._parse_vector_entry(controls["position"].get_text(), "position"),
            velocity_mps=self._parse_vector_entry(controls["velocity"].get_text(), "velocity"),
            color=color,
        )

    @staticmethod
    def _parse_vector_entry(value: str, field_name: str) -> tuple[float, float, float]:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 3:
            raise ModelError(f"{field_name} must contain three comma-separated numbers")
        try:
            vector = tuple(float(part) for part in parts)
        except ValueError as error:
            raise ModelError(f"{field_name} must contain three comma-separated numbers") from error
        if not all(math.isfinite(component) for component in vector):
            raise ModelError(f"{field_name} must contain finite numbers")
        return vector

    def _on_add_star_system_clicked(self, _button) -> None:
        name_entry = Gtk.Entry()
        name_entry.set_text("New Star System")
        dialog = self._editor_dialog(
            "Add Star System",
            "Add",
            [("Name", name_entry)],
        )
        dialog.connect(
            "response",
            lambda dialog, response: self._finish_add_star_system_dialog(
                dialog,
                response,
                name_entry,
            ),
        )
        dialog.present(self)

    def _finish_add_star_system_dialog(self, _dialog, response: str, name_entry) -> None:
        if response != "create":
            return
        try:
            self._ensure_editable_for_structural_edit()
            parent_group_id = self.selected_group_id
            group, _star = add_star_system(self.system, name_entry.get_text(), parent_group_id)
        except ModelError as error:
            self._show_error_dialog("Cannot Add Star System", str(error))
            return
        self._after_structural_edit(selected_group_id=group.id)

    def _on_add_star_clicked(self, _button) -> None:
        self._show_manual_body_dialog("star", "Add Star", "New Star", [])

    def _on_add_planet_clicked(self, _button) -> None:
        self._on_add_orbiting_kind("planet")

    def _on_add_moon_clicked(self, _button) -> None:
        self._on_add_orbiting_kind("moon")

    def _on_add_orbiting_kind(self, kind: str) -> None:
        allowed_parents = {"planet", "dwarf planet"} if kind == "moon" else {"star"}
        parent_options = self._eligible_parent_options(allowed_parents)
        if not parent_options:
            role = "planet or dwarf planet" if kind == "moon" else "star"
            self._show_error_dialog(f"Cannot Add {kind.title()}", f"Select or create a {role} first.")
            return
        self._show_manual_body_dialog(
            kind,
            f"Add {kind.title()}",
            f"New {kind.title()}",
            parent_options,
        )

    def _show_manual_body_dialog(
        self,
        kind: str,
        title: str,
        default_name: str,
        parent_options: list[tuple[str, str]],
    ) -> None:
        mass_default, radius_default, color_default = BODY_DEFAULTS[kind]
        name_entry = Gtk.Entry(text=default_name)
        mass_entry = Gtk.Entry(text=f"{mass_default:.12g}")
        radius_entry = Gtk.Entry(text=f"{radius_default:.12g}")
        color_entry = Gtk.Entry(text=color_default)
        parent_dropdown = Gtk.DropDown.new_from_strings(
            [label for _body_id, label in parent_options] or ["No Parent"]
        )
        parent_dropdown.set_selected(0)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        cartesian_grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        cartesian = {
            "x": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 6, 0.01),
            "y": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 6, 0.01),
            "z": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 6, 0.01),
            "vx": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 3, 10.0),
            "vy": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 3, 10.0),
            "vz": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 3, 10.0),
        }
        for row, (label, key) in enumerate(
            (("X (AU)", "x"), ("Y (AU)", "y"), ("Z (AU)", "z"),
             ("VX (m/s)", "vx"), ("VY (m/s)", "vy"), ("VZ (m/s)", "vz"))
        ):
            self._attach_dialog_row(cartesian_grid, row, label, cartesian[key])
        stack.add_titled(cartesian_grid, "cartesian", "Cartesian")

        orbit = None
        if kind != "star":
            orbit_grid = Gtk.Grid(column_spacing=10, row_spacing=8)
            orbit = {
                "size_mode": Gtk.DropDown.new_from_strings(["Semi-major Axis", "Orbital Period"]),
                "axis": self._dialog_spin(-1.0e9, 1.0e9, DEFAULT_ORBIT_RADIUS_M[kind] / AU, 6, 0.01),
                "period": self._dialog_spin(0.0, 1.0e9, 0.0, 6, 1.0),
                "eccentricity": self._dialog_spin(0.0, 100.0, 0.0, 6, 0.01),
                "inclination": self._dialog_spin(-360.0, 360.0, 0.0, 3, 1.0),
                "node": self._dialog_spin(-360.0, 360.0, 0.0, 3, 1.0),
                "periapsis": self._dialog_spin(-360.0, 360.0, 0.0, 3, 1.0),
                "anomaly": self._dialog_spin(-1.0e9, 1.0e9, 0.0, 3, 1.0),
            }
            for row, (label, key) in enumerate(
                (("Orbit Size From", "size_mode"), ("Semi-major Axis (AU)", "axis"),
                 ("Period (days)", "period"), ("Eccentricity", "eccentricity"),
                 ("Inclination (deg)", "inclination"), ("Node (deg)", "node"),
                 ("Periapsis (deg)", "periapsis"), ("Mean Anomaly (deg)", "anomaly"))
            ):
                self._attach_dialog_row(orbit_grid, row, label, orbit[key])
            stack.add_titled(orbit_grid, "orbital", "Orbital")
            stack.set_visible_child_name("orbital")

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(stack)
        switcher.set_halign(Gtk.Align.CENTER)
        state_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        state_box.append(switcher)
        state_box.append(stack)
        visible_switch = Gtk.Switch(active=True, halign=Gtk.Align.START)
        trail_switch = Gtk.Switch(active=True, halign=Gtk.Align.START)
        rows = [("Name", name_entry)]
        if parent_options:
            rows.append(("Parent", parent_dropdown))
        rows.extend(
            [
                ("Mass (kg)", mass_entry),
                ("Radius (m)", radius_entry),
                ("Color", color_entry),
                ("Visible", visible_switch),
                ("Trail", trail_switch),
                ("Initial State", state_box),
            ]
        )
        dialog = self._editor_dialog(title, "Add", rows)
        controls = {
            "name": name_entry,
            "mass": mass_entry,
            "radius": radius_entry,
            "color": color_entry,
            "parent": parent_dropdown,
            "visible": visible_switch,
            "trail": trail_switch,
            "stack": stack,
            "cartesian": cartesian,
            "orbit": orbit,
        }
        dialog.connect(
            "response",
            lambda source, response: self._finish_manual_body_dialog(
                source,
                response,
                kind,
                parent_options,
                controls,
            ),
        )
        dialog.present(self)

    def _finish_manual_body_dialog(
        self,
        _dialog,
        response: str,
        kind: str,
        parent_options: list[tuple[str, str]],
        controls: dict,
    ) -> None:
        if response != "create":
            return
        try:
            mass = float(controls["mass"].get_text())
            radius = float(controls["radius"].get_text())
            selected_parent = controls["parent"].get_selected()
            parent_id = (
                parent_options[selected_parent][0]
                if parent_options and selected_parent < len(parent_options)
                else None
            )
            group_id = None
            if kind == "star":
                group = self._group_by_id(self.selected_group_id) or self._group_for_body_id(
                    self.system.bodies[self.selected_index].id
                )
                group_id = group.id if group is not None else None
            self._ensure_editable_for_structural_edit()
            cartesian = controls["cartesian"]
            body = add_body_from_state(
                self.system,
                BodyStateInput(
                    name=controls["name"].get_text(),
                    kind=kind,
                    mass_kg=mass,
                    radius_m=radius,
                    position_m=(
                        cartesian["x"].get_value() * AU,
                        cartesian["y"].get_value() * AU,
                        cartesian["z"].get_value() * AU,
                    ),
                    velocity_mps=(
                        cartesian["vx"].get_value(),
                        cartesian["vy"].get_value(),
                        cartesian["vz"].get_value(),
                    ),
                    color=controls["color"].get_text().strip() or BODY_DEFAULTS[kind][2],
                    parent_id=parent_id,
                    group_id=group_id,
                    visible=controls["visible"].get_active(),
                    trail_enabled=controls["trail"].get_active(),
                ),
            )
            if controls["stack"].get_visible_child_name() == "orbital":
                orbit_controls = controls["orbit"]
                use_period = orbit_controls["size_mode"].get_selected() == 1
                orbit = OrbitData(
                    semi_major_axis_m=None if use_period else orbit_controls["axis"].get_value() * AU,
                    orbital_period_s=orbit_controls["period"].get_value() * DAY if use_period else None,
                    eccentricity=orbit_controls["eccentricity"].get_value(),
                    inclination_deg=orbit_controls["inclination"].get_value(),
                    longitude_of_ascending_node_deg=orbit_controls["node"].get_value(),
                    argument_of_periapsis_deg=orbit_controls["periapsis"].get_value(),
                    mean_anomaly_deg=orbit_controls["anomaly"].get_value(),
                    epoch=self.system.reference_frame.epoch if self.system.reference_frame else self.system.epoch,
                )
                generate_body_orbit(self.system, self.simulation, body.id, orbit, None)
        except (ModelError, ValueError) as error:
            if "body" in locals() and any(candidate.id == body.id for candidate in self.system.bodies):
                self.system.bodies = [candidate for candidate in self.system.bodies if candidate.id != body.id]
                for group in self.system.groups:
                    group.body_ids = [body_id for body_id in group.body_ids if body_id != body.id]
                self.simulation.replace_bodies(self.system.bodies)
            self._show_error_dialog("Cannot Add Body", str(error))
            return
        self._after_structural_edit(selected_body_id=body.id)

    def _dialog_spin(
        self,
        lower: float,
        upper: float,
        value: float,
        digits: int,
        step: float,
    ) -> Gtk.SpinButton:
        spin = Gtk.SpinButton()
        spin.set_digits(digits)
        spin.set_adjustment(Gtk.Adjustment.new(value, lower, upper, step, step * 10.0, 0.0))
        return spin

    def _attach_dialog_row(self, grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> None:
        grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
        grid.attach(widget, 1, row, 1, 1)

    def _on_delete_selected_clicked(self, _button) -> None:
        if self.selected_group_id is not None:
            try:
                body_names, group_names = deletion_summary_for_group(self.system, self.selected_group_id)
            except ModelError as error:
                self._show_error_dialog("Cannot Delete Star System", str(error))
                return
            title = "Delete Star System?"
            message = self._delete_summary_message(body_names, group_names)
            target_type = "group"
            target_id = self.selected_group_id
        elif self.system.bodies:
            body = self.system.bodies[self.selected_index]
            try:
                body_names, group_names = deletion_summary_for_body(self.system, body.id)
            except ModelError as error:
                self._show_error_dialog("Cannot Delete Body", str(error))
                return
            title = "Delete Body?"
            message = self._delete_summary_message(body_names, group_names)
            target_type = "body"
            target_id = body.id
        else:
            return
        dialog = Adw.AlertDialog.new(title, message)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda dialog, response: self._finish_delete_selected(
                dialog,
                response,
                target_type,
                target_id,
            ),
        )
        dialog.present(self)

    def _finish_delete_selected(self, _dialog, response: str, target_type: str, target_id: str) -> None:
        if response != "delete":
            return
        try:
            self._ensure_editable_for_structural_edit()
            if target_type == "group":
                if not any(group.id == target_id for group in self.system.groups):
                    target_id = self.selected_group_id or target_id
                delete_group_cascade(self.system, target_id)
            else:
                if not any(body.id == target_id for body in self.system.bodies):
                    if self.selected_index < len(self.system.bodies):
                        target_id = self.system.bodies[self.selected_index].id
                delete_body_cascade(self.system, target_id)
        except ModelError as error:
            self._show_error_dialog("Cannot Delete", str(error))
            return
        selected_index = min(self.selected_index, len(self.system.bodies) - 1)
        selected_body_id = self.system.bodies[selected_index].id if self.system.bodies else None
        self._after_structural_edit(selected_body_id=selected_body_id)

    def _delete_summary_message(self, body_names: list[str], group_names: list[str]) -> str:
        parts = []
        if group_names:
            parts.append(f"Star systems: {', '.join(group_names[:5])}")
        if body_names:
            parts.append(f"Bodies: {', '.join(body_names[:8])}")
        if len(group_names) > 5 or len(body_names) > 8:
            parts.append("Additional descendants will also be removed.")
        return "\n".join(parts) or "The selected item will be removed."

    def _eligible_parent_options(self, allowed_kinds: set[str]) -> list[tuple[str, str]]:
        selected_bodies = self._selected_context_bodies()
        preferred = [
            body
            for body in selected_bodies
            if body.kind in allowed_kinds
        ]
        candidates = preferred or [body for body in self.system.bodies if body.kind in allowed_kinds]
        return [(body.id, f"{body.name} ({body.kind})") for body in candidates]

    def _selected_context_bodies(self) -> list[Body]:
        if self.selected_group_id is not None:
            return [
                self.system.bodies[index]
                for index in self._body_indices_for_group(self.selected_group_id)
            ]
        if not self.system.bodies:
            return []
        selected = self.system.bodies[self.selected_index]
        descendants = {selected.id}
        changed = True
        while changed:
            changed = False
            for body in self.system.bodies:
                if body.parent_id in descendants and body.id not in descendants:
                    descendants.add(body.id)
                    changed = True
        return [body for body in self.system.bodies if body.id in descendants]

    def _editor_dialog(self, title: str, action_label: str, rows: list[tuple[str, Gtk.Widget]]):
        dialog = Adw.AlertDialog.new(title, None)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(6)
        for label_text, widget in rows:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            label = Gtk.Label(label=label_text, xalign=0)
            label.add_css_class("dim-label")
            widget.set_hexpand(True)
            row.append(label)
            row.append(widget)
            box.append(row)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", action_label)
        dialog.set_close_response("cancel")
        dialog.set_default_response("create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        return dialog

    def _ensure_editable_for_structural_edit(self) -> None:
        if self.system_library.is_user_saved(self.system):
            return
        raise ModelError("Bundled presets are read-only. Use Duplicate to Edit first.")

    def _stop_playback_for_structural_work(self) -> None:
        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.simulation.increment_generation()

    def _after_structural_edit(
        self,
        selected_body_id: str | None = None,
        selected_group_id: str | None = None,
    ) -> None:
        self.playing = False
        self.play_button.set_icon_name("media-playback-start-symbolic")
        self.focus_target = None
        self.focus_state = None
        self.selected_group_id = None
        self.simulation.replace_bodies(self.system.bodies)
        self._clear_dynamic_simulation_state()
        self._populate_body_list()
        if selected_group_id is not None:
            self._select_group(selected_group_id)
        else:
            self._select_body(self._body_index_for_id(selected_body_id))
        self.system_library.refresh(self.system)
        self._load_system_editor()
        self._load_settings_editor()
        self._update_title()
        self._update_time_label()
        self._refresh_canvas()
        self._sync_structural_controls()
        self._mark_dirty()

    def _sync_structural_controls(self) -> None:
        has_bodies = bool(self.system.bodies)
        editable = self._current_system_editable()
        selected_body = self.system.bodies[self.selected_index] if has_bodies else None
        self.add_body_menu_button.set_sensitive(editable and has_bodies)
        self.add_star_system_button.set_sensitive(editable and has_bodies)
        self.add_star_button.set_sensitive(editable and has_bodies)
        self.add_planet_button.set_sensitive(
            editable and bool(self._eligible_parent_options({"star"}))
        )
        self.add_dwarf_planet_button.set_sensitive(
            editable and bool(self._eligible_parent_options({"star"}))
        )
        self.add_moon_button.set_sensitive(
            editable and bool(self._eligible_parent_options({"planet", "dwarf planet"}))
        )
        self.add_comet_button.set_sensitive(editable and bool(self._eligible_parent_options({"star"})))
        self.add_asteroid_button.set_sensitive(editable and bool(self._eligible_parent_options({"star"})))
        self.search_horizons_button.set_sensitive(editable and horizons_import_available(self.system))
        self.delete_selected_button.set_sensitive(editable and has_bodies)

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
        focused_bodies = playback.focused_timing_bodies(
            self.system.bodies,
            self.system.groups,
            target,
            collapse_moons=self.system.settings.simulation_scope != "full_nbody",
        )
        visible_step_s = focused_visible_step_s(
            focused_bodies,
            self.system.settings.accuracy_profile,
        )
        self.focus_state = FocusState(
            target=target,
            visible_step_s=visible_step_s,
            trail_sample_interval_s=recommended_trail_sample_interval_s(visible_step_s),
        )
        self.focus_fit_session += 1
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
        self._resolve_dirty_before(self._reset_to_loaded_system)

    def _reset_to_loaded_system(self) -> None:
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
        self._set_dirty(False)

    def _prepare_system_for_save(self, system: SolarSystem) -> None:
        self.simulation.apply_to_bodies(system.bodies)

    def _on_system_saved(self, system: SolarSystem) -> None:
        self.system = system
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.simulation.increment_generation()
        self._set_dirty(False)
        self._load_system_editor()
        self._sync_structural_controls()
        self._update_title()

    def _on_system_mutated(self) -> None:
        self._mark_dirty_if_editable()
        self._update_title()

    def _mark_dirty_if_editable(self) -> None:
        if self._current_system_editable():
            self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty and self._current_system_editable()
        self.save_button.set_sensitive(self.dirty)
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
        dirty_suffix = " *" if self.dirty else ""
        self.window_title.set_title(f"{self.system.name}{dirty_suffix}")
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
        if decision.moons_collapsed:
            policy += " (moons collapsed)"
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
            focused_fit_session=self.focus_fit_session,
            selected_group_center=selected_group_center,
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
