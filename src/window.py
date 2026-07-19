# window.py
#
# Copyright 2026 Jackrabbithanna
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import math
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from . import hierarchy, playback
from .analysis_frames import (
    AnalysisFrameSpec,
    frame_kinematics,
    relative_diagnostics,
    transform_points,
    transform_state,
)
from .canvas import CanvasScene, SolarSystemCanvas
from .constants import AU, DAY, G
from .documents import parse_document, serialize_document, unique_import_name
from .frame_exports import relative_system_snapshot, serialize_relative_csv
from .horizons import (
    HorizonsClient,
    HorizonsImportDraft,
    HorizonsSearchResult,
    add_imported_body,
    apply_system_refresh,
    horizons_catalog_id,
    horizons_import_available,
    horizons_refresh_available,
    horizons_refreshable_bodies,
    parse_required_physical_value,
    shift_horizons_frame_epoch,
)
from .flybys import radial_velocity_mps, solve_flyby
from .models import (
    Body,
    DataSource,
    FlybyData,
    ModelError,
    OrbitData,
    REFERENCE_AXES,
    ReferenceOrigin,
    SolarSystem,
    SystemGroup,
    SystemReferenceFrame,
    SystemSettings,
)
from .orbit_editing import (
    generate_binary_pair_orbit,
    generate_body_orbit,
    generate_group_barycenter_orbit,
)
from .orbits import body_indices_for_group, group_barycenter
from .orbits import configured_orbit_guides
from .presets import (
    load_builtin_solar_system,
    load_builtin_solar_system_by_id,
    load_builtin_solar_systems,
)
from .reference_frames import (
    ReferenceFrameTransform,
    origin_for_body,
    origin_for_group,
    origin_for_system,
    rotation_matrix_from_xyz_degrees,
    transform_system_reference_frame,
    transform_system_to_standard_frame,
    validate_rotation_matrix,
)
from .standard_frames import parse_epoch, rotation_between_frames, shift_epoch
from .scales import (
    DISTANCE_UNITS,
    FocusState,
    distance_between_bodies_m,
    effective_focus_settings,
    focused_visible_step_s,
    focus_overview_entity,
    focus_target_contains_body,
    focus_target_contains_group,
    focus_target_body_indices,
    focused_trail_reference_position,
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
    add_flyby_from_state,
    add_body_from_state,
    add_star_system,
    create_system,
    delete_body_cascade,
    delete_group_cascade,
    deletion_summary_for_body,
    deletion_summary_for_group,
    regenerate_flyby,
    update_body_from_state,
)
from .system_library import SystemLibraryController

MAX_AUTOMATIC_SIDEBAR_WIDTH = 520
SIDEBAR_HORIZONTAL_MARGINS = 24
WINDOW_MONITOR_MARGIN = 32
FLYBY_KIND_OPTIONS = ("star", "planet", "dwarf planet", "comet", "asteroid")


@Gtk.Template(resource_path="/io/github/jackrabbithanna/solarsystembuilder/window.ui")
class SolarSystemBuilderWindow(Adw.ApplicationWindow):
    __gtype_name__ = "SolarSystemBuilderWindow"

    canvas = Gtk.Template.Child()
    main_paned = Gtk.Template.Child()
    settings_controls_box = Gtk.Template.Child()
    settings_primary_row = Gtk.Template.Child()
    settings_physics_row = Gtk.Template.Child()
    play_button = Gtk.Template.Child()
    step_back_button = Gtk.Template.Child()
    reset_button = Gtk.Template.Child()
    step_forward_button = Gtk.Template.Child()
    view_2d_button = Gtk.Template.Child()
    view_3d_button = Gtk.Template.Child()
    zoom_out_button = Gtk.Template.Child()
    reset_zoom_button = Gtk.Template.Child()
    zoom_in_button = Gtk.Template.Child()
    path_options_button = Gtk.Template.Child()
    orbit_visibility_dropdown = Gtk.Template.Child()
    trail_visibility_dropdown = Gtk.Template.Child()
    path_style_dropdown = Gtk.Template.Child()
    playback_status_box = Gtk.Template.Child()
    playback_spinner = Gtk.Template.Child()
    playback_status_label = Gtk.Template.Child()
    new_system_button = Gtk.Template.Child()
    save_button = Gtk.Template.Child()
    duplicate_button = Gtk.Template.Child()
    system_dropdown = Gtk.Template.Child()
    body_list = Gtk.Template.Child()
    speed_spin = Gtk.Template.Child()
    time_unit_dropdown = Gtk.Template.Child()
    accuracy_dropdown = Gtk.Template.Child()
    physics_mode_dropdown = Gtk.Template.Child()
    integrator_dropdown = Gtk.Template.Child()
    view_mode_dropdown = Gtk.Template.Child()
    simulation_scope_dropdown = Gtk.Template.Child()
    trail_frame_dropdown = Gtk.Template.Child()
    time_label = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    system_name_entry = Gtk.Template.Child()
    system_description_entry = Gtk.Template.Child()
    reference_frame_label = Gtk.Template.Child()
    transform_frame_button = Gtk.Template.Child()
    horizons_refresh_button = Gtk.Template.Child()
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
    add_flyby_button = Gtk.Template.Child()
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
    edit_flyby_button = Gtk.Template.Child()
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
        self.analysis_frame = AnalysisFrameSpec()
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
        self.horizons_refresh_cancel: threading.Event | None = None
        self.horizons_refresh_in_progress = False
        self.render_mode = "3d"
        self.canvas.set_render_mode(self.render_mode)
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
            self.settings_primary_row,
            "orientation",
            Gtk.Orientation.VERTICAL,
        )
        self.compact_breakpoint.add_setter(
            self.settings_physics_row,
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
            self.physics_mode_dropdown,
            self.integrator_dropdown,
            self.view_mode_dropdown,
            self.simulation_scope_dropdown,
            self.trail_frame_dropdown,
            self.orbit_visibility_dropdown,
            self.trail_visibility_dropdown,
            self.path_style_dropdown,
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
        self.import_document_action = self._create_window_action(
            "import-document",
            self._on_import_document_action,
        )
        self.export_document_action = self._create_window_action(
            "export-document",
            self._on_export_document_action,
        )
        self.physics_diagnostics_action = self._create_window_action(
            "physics-diagnostics",
            self._on_physics_diagnostics_action,
        )
        self.analysis_frame_action = self._create_window_action(
            "analysis-frame",
            self._on_analysis_frame_action,
        )
        self.frame_diagnostics_action = self._create_window_action(
            "frame-diagnostics",
            self._on_frame_diagnostics_action,
        )
        self.reset_loaded_action = self._create_window_action(
            "reset-loaded",
            lambda *_args: self._on_reset_clicked(None),
        )
        self.reset_last_save_action = self._create_window_action(
            "reset-last-save",
            self._on_reset_last_save_action,
        )
        self.reset_bundled_action = self._create_window_action(
            "reset-bundled-preset",
            self._on_reset_bundled_action,
        )

        self.canvas.connect("body-selected", self._on_canvas_body_selected)
        self.canvas.connect("group-selected", self._on_canvas_group_selected)
        self.canvas.connect("focus-target-selected", self._on_canvas_focus_target_selected)
        self.canvas.connect("zoom-factor-changed", self._on_canvas_zoom_factor_changed)
        self.canvas.connect("view-state-changed", self._on_canvas_view_state_changed)
        self.play_button.connect("clicked", self._on_play_clicked)
        self.step_back_button.connect("clicked", self._on_step_back_clicked)
        self.reset_button.connect("clicked", self._on_reset_clicked)
        self.step_forward_button.connect("clicked", self._on_step_forward_clicked)
        self.view_3d_button.set_group(self.view_2d_button)
        self.view_2d_button.connect("toggled", self._on_render_mode_toggled, "2d")
        self.view_3d_button.connect("toggled", self._on_render_mode_toggled, "3d")
        self.zoom_out_button.connect("clicked", self._on_zoom_out_clicked)
        self.reset_zoom_button.connect("clicked", self._on_reset_zoom_clicked)
        self.zoom_in_button.connect("clicked", self._on_zoom_in_clicked)
        self.new_system_button.connect("clicked", self._on_new_system_clicked)
        self.horizons_refresh_button.connect("clicked", self._on_refresh_horizons_clicked)
        self.transform_frame_button.connect("clicked", self._on_transform_frame_clicked)
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
        self.add_flyby_button.connect("clicked", self._on_add_flyby_clicked)
        self.search_horizons_button.connect("clicked", self._on_search_horizons_clicked)
        self.edit_flyby_button.connect("clicked", self._on_edit_flyby_clicked)
        self.delete_selected_button.connect("clicked", self._on_delete_selected_clicked)
        self.body_list.connect("body-selected", self._on_hierarchy_body_selected)
        self.body_list.connect("group-selected", self._on_hierarchy_group_selected)
        self.system_panel.connect("time-step-changed", self._on_time_step_changed)
        self.system_panel.connect("accuracy-changed", self._on_accuracy_changed)
        self.system_panel.connect("physics-mode-changed", self._on_physics_mode_changed)
        self.system_panel.connect("integrator-changed", self._on_integrator_changed)
        self.system_panel.connect("view-mode-changed", self._on_view_mode_changed)
        self.system_panel.connect("simulation-scope-changed", self._on_simulation_scope_changed)
        self.system_panel.connect("trail-frame-changed", self._on_trail_frame_changed)
        self.system_panel.connect(
            "orbit-visibility-changed",
            self._on_orbit_visibility_changed,
        )
        self.system_panel.connect(
            "trail-visibility-changed",
            self._on_trail_visibility_changed,
        )
        self.system_panel.connect("path-style-changed", self._on_path_style_changed)
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
        self.trail_frame_dropdown.set_sensitive(True)
        self._populate_body_list()
        self._select_body(0)
        self._update_title()
        self._update_time_label()
        self._set_zoom_factor(1.0)
        self._sync_structural_controls()
        self._set_dirty(False)
        self._sync_playback_status()

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
        if self.horizons_refresh_cancel is not None:
            self.horizons_refresh_cancel.set()
        self.horizons_generation += 1
        self.horizons_executor.shutdown(wait=False, cancel_futures=True)
        return False

    def _create_window_action(self, name: str, callback) -> Gio.SimpleAction:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        return action

    @staticmethod
    def _json_file_dialog(title: str) -> Gtk.FileDialog:
        dialog = Gtk.FileDialog(title=title, modal=True)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("Solar System JSON")
        json_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(json_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(json_filter)
        return dialog

    @staticmethod
    def _file_operation_cancelled(error: Exception) -> bool:
        return isinstance(error, GLib.Error) and error.matches(
            Gio.io_error_quark(),
            Gio.IOErrorEnum.CANCELLED,
        )

    def _on_import_document_action(self, *_args) -> None:
        dialog = self._json_file_dialog("Import Solar System")
        dialog.open(self, None, self._finish_import_document_dialog)

    def _finish_import_document_dialog(self, dialog, result, *_args) -> None:
        if self.closed:
            return
        try:
            document_file = dialog.open_finish(result)
            loaded, contents, _etag = document_file.load_contents(None)
            if not loaded:
                raise ModelError("the selected document could not be read")
            imported = parse_document(contents)
            existing_names = [
                system.name
                for system in (
                    *load_builtin_solar_systems(),
                    *self.system_library.library.list_systems(),
                )
            ]
            imported = imported.duplicate(unique_import_name(imported.name, existing_names))
        except Exception as error:
            if not self._file_operation_cancelled(error):
                self._show_error_dialog("Cannot Import System", str(error))
            return
        self._resolve_dirty_before(lambda: self._activate_imported_document(imported))

    def _activate_imported_document(self, imported: SolarSystem) -> None:
        try:
            self.system_library.save_new_system(imported)
        except Exception as error:
            self._show_error_dialog("Cannot Import System", str(error))

    def _on_analysis_frame_action(self, *_args) -> None:
        origin_options: list[tuple[str, str | None, str]] = [
            ("fixed", None, "System / Inertial"),
            ("system_barycenter", None, "Whole-System Barycenter"),
            *[("body", body.id, f"Body: {body.name}") for body in self.system.bodies],
            *[
                ("group_barycenter", group.id, f"Group: {group.name}")
                for group in self.system.groups
            ],
        ]
        secondary_options = origin_options[1:]
        axes_options = [("Current canonical axes", "current"), *[
            (f"{system} / {plane}", axes_id)
            for axes_id, (system, plane) in REFERENCE_AXES.items()
            if axes_id != "custom"
        ]]
        origin_dropdown = Gtk.DropDown.new_from_strings(
            [label for _kind, _target_id, label in origin_options]
        )
        secondary_dropdown = Gtk.DropDown.new_from_strings(
            [label for _kind, _target_id, label in secondary_options]
        )
        axes_dropdown = Gtk.DropDown.new_from_strings([label for label, _id in axes_options])
        rotation_dropdown = Gtk.DropDown.new_from_strings(
            ["Fixed", "Standard Axes of Date", "Prescribed Rate", "Co-rotating Target Pair"]
        )
        axis_entry = Gtk.Entry(text=", ".join(str(value) for value in self.analysis_frame.rotation_axis))
        rate_spin = Gtk.SpinButton.new_with_range(-1.0e9, 1.0e9, 0.01)
        rate_spin.set_digits(6)
        rate_spin.set_value(math.degrees(self.analysis_frame.angular_rate_rad_s) * DAY)
        acceleration_spin = Gtk.SpinButton.new_with_range(-1.0e9, 1.0e9, 0.01)
        acceleration_spin.set_digits(6)
        acceleration_spin.set_value(
            math.degrees(self.analysis_frame.angular_acceleration_rad_s2) * DAY * DAY
        )

        for index, (kind, target_id, _label) in enumerate(origin_options):
            if kind == self.analysis_frame.origin_kind and target_id == self.analysis_frame.origin_id:
                origin_dropdown.set_selected(index)
                break
        for index, (_label, axes_id) in enumerate(axes_options):
            if axes_id == self.analysis_frame.axes_id:
                axes_dropdown.set_selected(index)
                break
        rotation_dropdown.set_selected(
            {"fixed": 0, "of_date": 1, "prescribed": 2, "target_pair": 3}[
                self.analysis_frame.rotation_mode
            ]
        )
        for index, (kind, target_id, _label) in enumerate(secondary_options):
            if (
                kind == self.analysis_frame.secondary_kind
                and target_id == self.analysis_frame.secondary_id
            ):
                secondary_dropdown.set_selected(index)
                break

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        for row, (label, widget) in enumerate(
            (
                ("Origin", origin_dropdown),
                ("Axes", axes_dropdown),
                ("Rotation", rotation_dropdown),
                ("Second target", secondary_dropdown),
                ("Rotation axis XYZ", axis_entry),
                ("Angular rate (deg/day)", rate_spin),
                ("Angular acceleration (deg/day²)", acceleration_spin),
            )
        ):
            self._attach_dialog_row(grid, row, label, widget)
        help_label = Gtk.Label(
            label=(
                "The main canvas and newly recorded trails use this session-only frame. "
                "The navigation inset remains inertial."
            ),
            xalign=0,
            wrap=True,
            css_classes=["dim-label"],
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(grid)
        box.append(help_label)
        dialog = Adw.AlertDialog.new("Analysis Frame", "Canonical physics remains inertial.")
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_close_response("cancel")
        dialog.set_default_response("apply")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)

        def finish(_dialog, response: str) -> None:
            if response != "apply":
                return
            try:
                origin_index = origin_dropdown.get_selected()
                secondary_index = secondary_dropdown.get_selected()
                axes_index = axes_dropdown.get_selected()
                rotation_index = rotation_dropdown.get_selected()
                origin_kind, origin_id, origin_label = origin_options[origin_index]
                secondary_kind, secondary_id, secondary_label = secondary_options[
                    secondary_index
                ]
                axes_label, axes_id = axes_options[axes_index]
                rotation_mode = ("fixed", "of_date", "prescribed", "target_pair")[
                    rotation_index
                ]
                if rotation_mode == "target_pair":
                    axes_id = "current"
                axis = self._parse_vector_entry(axis_entry.get_text(), "rotation axis")
                label = origin_label
                if axes_id != "current" and rotation_mode != "target_pair":
                    label += f" · {axes_label}"
                if rotation_mode == "prescribed":
                    label += " · rotating"
                elif rotation_mode == "of_date":
                    label += " · of date"
                elif rotation_mode == "target_pair":
                    label += f" → {secondary_label}"
                spec = AnalysisFrameSpec(
                    origin_kind=origin_kind,
                    origin_id=origin_id,
                    axes_id=axes_id,
                    rotation_mode=rotation_mode,
                    rotation_axis=axis,
                    angular_rate_rad_s=math.radians(rate_spin.get_value()) / DAY,
                    angular_acceleration_rad_s2=(
                        math.radians(acceleration_spin.get_value()) / (DAY * DAY)
                    ),
                    reference_elapsed_s=self.simulation.state.elapsed_s,
                    secondary_kind=secondary_kind if rotation_mode == "target_pair" else None,
                    secondary_id=secondary_id if rotation_mode == "target_pair" else None,
                    label=label,
                )
                self._set_analysis_frame(spec)
            except (ModelError, ValueError, IndexError) as error:
                self._show_error_dialog("Cannot Apply Analysis Frame", str(error))

        def update_rotation_controls(dropdown, _param) -> None:
            mode = ("fixed", "of_date", "prescribed", "target_pair")[
                dropdown.get_selected()
            ]
            pair = mode == "target_pair"
            prescribed = mode == "prescribed"
            secondary_dropdown.set_sensitive(pair)
            axes_dropdown.set_sensitive(not pair)
            axis_entry.set_sensitive(prescribed)
            rate_spin.set_sensitive(prescribed)
            acceleration_spin.set_sensitive(prescribed)
            if mode == "of_date" and axes_dropdown.get_selected() == 0:
                axes_dropdown.set_selected(1)

        rotation_dropdown.connect("notify::selected", update_rotation_controls)
        dialog.connect("response", finish)
        update_rotation_controls(rotation_dropdown, None)
        dialog.present(self)

    def _set_analysis_frame(self, spec: AnalysisFrameSpec) -> None:
        spec.validated(self.system)
        materialized = self.simulation.materialized_state(
            self.system.bodies,
            self.system.groups,
        )
        frame_kinematics(
            self.system,
            materialized,
            spec,
            physics_mode=self._effective_settings().physics_mode,
            include_acceleration=False,
        )
        self._set_playing(False)
        self.analysis_frame = spec
        self._clear_dynamic_simulation_state()
        self.trail_frame_dropdown.set_sensitive(not spec.active)
        self.canvas.reset_all_views()
        self._update_title()
        self._refresh_canvas()

    def _on_frame_diagnostics_action(self, *_args) -> None:
        state = self.simulation.materialized_state(self.system.bodies, self.system.groups)
        try:
            kinematics, rows = relative_diagnostics(
                self.system,
                state,
                self.analysis_frame,
            )
            selected = rows[self.selected_index]
            csv_contents = serialize_relative_csv(self.system, state, self.analysis_frame)
        except (ModelError, ValueError) as error:
            self._show_error_dialog("Frame Diagnostics Unavailable", str(error))
            return
        vector = lambda values: "(" + ", ".join(f"{value:.6e}" for value in values) + ")"
        lines = [
            f"Frame: {self.analysis_frame.label}",
            f"Origin position: {vector(kinematics.origin_position_m)} m",
            f"Origin velocity: {vector(kinematics.origin_velocity_mps)} m/s",
            f"Angular velocity: {vector(kinematics.angular_velocity_rad_s)} rad/s",
            f"Angular acceleration: {vector(kinematics.angular_acceleration_rad_s2)} rad/s²",
            "",
            f"Selected body: {selected.name}",
            f"Relative position: {vector(selected.position_m)} m",
            f"Relative velocity: {vector(selected.velocity_mps)} m/s",
            f"Gravitational: {vector(selected.gravitational_acceleration_mps2)} m/s²",
            f"Translational: {vector(selected.translational_acceleration_mps2)} m/s²",
            f"Coriolis: {vector(selected.coriolis_acceleration_mps2)} m/s²",
            f"Centrifugal: {vector(selected.centrifugal_acceleration_mps2)} m/s²",
            f"Euler: {vector(selected.euler_acceleration_mps2)} m/s²",
            f"Total apparent: {vector(selected.total_apparent_acceleration_mps2)} m/s²",
        ]
        dialog = Adw.AlertDialog.new("Frame Diagnostics", "\n".join(lines))
        dialog.add_response("close", "Close")
        dialog.add_response("export", "Export CSV…")
        dialog.set_close_response("close")

        def finish(_dialog, response: str) -> None:
            if response == "export":
                self._save_frame_csv(csv_contents)

        dialog.connect("response", finish)
        dialog.present(self)

    def _save_frame_csv(self, contents: bytes) -> None:
        dialog = Gtk.FileDialog(title="Export Frame Diagnostics", modal=True)
        csv_filter = Gtk.FileFilter()
        csv_filter.set_name("Comma-separated values")
        csv_filter.add_suffix("csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(csv_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(csv_filter)
        dialog.set_initial_name("frame-diagnostics.csv")
        dialog.save(
            self,
            None,
            lambda source, result, *_args: self._finish_export_document_dialog(
                source,
                result,
                contents,
            ),
        )

    def _on_physics_diagnostics_action(self, *_args) -> None:
        try:
            diagnostics, drift = self.simulation.conservation_snapshot(
                self.system.bodies,
                self.system.groups,
            )
        except ValueError as error:
            self._show_error_dialog("Physics Diagnostics Unavailable", str(error))
            return

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
        mode_label = (
            "Post-Newtonian (1PN)"
            if settings.physics_mode == "post_newtonian"
            else "Newtonian"
        )
        integrator_label = (
            "Runge-Kutta 4" if settings.integrator == "rk4" else "Velocity Verlet"
        )
        energy_label = (
            "Newtonian mechanical-energy proxy"
            if settings.physics_mode == "post_newtonian"
            else "Mechanical energy"
        )
        angular = diagnostics.angular_momentum_kg_m2ps
        lines = [
            f"Gravity model: {mode_label}",
            f"Integrator: {integrator_label}",
            f"Effective policy: {decision.policy.replace('_', ' ')}",
            f"Elapsed time: {format_elapsed_time(self.simulation.state.elapsed_s)}",
            "",
            f"Kinetic energy: {diagnostics.kinetic_energy_j:.6e} J",
            f"Potential energy: {diagnostics.potential_energy_j:.6e} J",
            f"{energy_label}: {diagnostics.total_energy_j:.6e} J",
            (
                "Angular momentum (x, y, z): "
                f"({angular[0]:.6e}, {angular[1]:.6e}, {angular[2]:.6e}) kg·m²/s"
            ),
            (
                "Angular-momentum magnitude: "
                f"{diagnostics.angular_momentum_magnitude_kg_m2ps:.6e} kg·m²/s"
            ),
            "",
        ]
        if drift is None:
            lines.append("Baseline drift: unavailable")
        else:
            lines.extend(
                (
                    f"Energy change from baseline: {drift.energy_delta_j:+.6e} J",
                    (
                        "Relative energy drift: "
                        f"{self._format_diagnostic_drift(drift.relative_energy_drift)}"
                    ),
                    (
                        "Angular-momentum change: "
                        f"{drift.angular_momentum_delta_magnitude_kg_m2ps:.6e} kg·m²/s"
                    ),
                    (
                        "Relative angular-momentum drift: "
                        f"{self._format_diagnostic_drift(drift.relative_angular_momentum_drift)}"
                    ),
                )
            )
        if decision.policy != "full_nbody" or decision.moons_collapsed:
            lines.extend(
                (
                    "",
                    "Approximate physics is active, so conservation drift is expected.",
                )
            )
        if settings.physics_mode == "post_newtonian":
            lines.extend(
                (
                    "",
                    "The energy value is a Newtonian diagnostic proxy for the practical 1PN force model.",
                )
            )

        dialog = Adw.AlertDialog.new("Physics Diagnostics", "\n".join(lines))
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)

    @staticmethod
    def _format_diagnostic_drift(value: float | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:+.6e} ({value * 100.0:+.6e}%)"

    def _on_export_document_action(self, *_args) -> None:
        self._stop_playback_for_structural_work()
        selected_body = (
            self.system.bodies[self.selected_index] if self.system.bodies else None
        )
        presets: list[tuple[str, str, str | None, str]] = [
            ("json", "fixed", None, "Current inertial snapshot"),
        ]
        if selected_body is not None:
            presets.append(
                ("json", "body", selected_body.id, f"Centered on {selected_body.name}")
            )
        presets.extend(
            (
                "json",
                "group_barycenter",
                group.id,
                f"Centered on subsystem: {group.name}",
            )
            for group in self.system.groups
        )
        presets.extend(
            (
                ("json", "system_barycenter", None, "Centered on whole-system barycenter"),
                ("csv", "analysis", None, f"CSV diagnostics: {self.analysis_frame.label}"),
            )
        )
        preset_dropdown = Gtk.DropDown.new_from_strings(
            [label for _format, _kind, _target_id, label in presets]
        )
        help_label = Gtk.Label(
            label=(
                "JSON presets remain importable canonical inertial snapshots. CSV exports "
                "include relative state and every explicit non-inertial acceleration term."
            ),
            xalign=0,
            wrap=True,
            css_classes=["dim-label"],
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(preset_dropdown)
        box.append(help_label)
        dialog = Adw.AlertDialog.new("Export Solar System", "Choose an export frame preset.")
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("export", "Export…")
        dialog.set_close_response("cancel")
        dialog.set_default_response("export")
        dialog.set_response_appearance("export", Adw.ResponseAppearance.SUGGESTED)

        def finish(_dialog, response: str) -> None:
            if response != "export":
                return
            try:
                export_format, origin_kind, origin_id, label = presets[
                    preset_dropdown.get_selected()
                ]
                state = self.simulation.materialized_state(
                    self.system.bodies,
                    self.system.groups,
                )
                if export_format == "csv":
                    contents = serialize_relative_csv(
                        self.system,
                        state,
                        self.analysis_frame,
                    )
                else:
                    exported = relative_system_snapshot(
                        self.system,
                        state,
                        origin_kind,
                        origin_id,
                    )
                    contents = serialize_document(exported)
            except (ModelError, ValueError, IndexError) as error:
                self._show_error_dialog("Cannot Export System", str(error))
                return
            self._save_export_document(contents, export_format, label)

        dialog.connect("response", finish)
        dialog.present(self)

    def _save_export_document(self, contents: bytes, export_format: str, label: str) -> None:
        filename = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in f"{self.system.name}-{label}".strip()
        ).strip("-")
        if export_format == "csv":
            dialog = Gtk.FileDialog(title="Export Frame Diagnostics", modal=True)
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Comma-separated values")
            file_filter.add_suffix("csv")
        else:
            dialog = Gtk.FileDialog(title="Export Solar System", modal=True)
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Solar System JSON")
            file_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        dialog.set_initial_name(f"{filename or 'solar-system'}.{export_format}")
        dialog.save(
            self,
            None,
            lambda source, result, *_args: self._finish_export_document_dialog(
                source,
                result,
                contents,
            ),
        )

    def _finish_export_document_dialog(self, dialog, result, contents: bytes) -> None:
        if self.closed:
            return
        try:
            document_file = dialog.save_finish(result)
            replaced, _etag = document_file.replace_contents(
                contents,
                None,
                False,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                None,
            )
            if not replaced:
                raise ModelError("the document could not be written")
        except Exception as error:
            if not self._file_operation_cancelled(error):
                self._show_error_dialog("Cannot Export System", str(error))

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
        if self.horizons_refresh_cancel is not None:
            self.horizons_refresh_cancel.set()
            self.horizons_refresh_cancel = None
        self.horizons_refresh_in_progress = False
        self.horizons_generation += 1
        self._set_playing(False)
        self._replace_system(system, refresh_snapshot=True)

    def _replace_system(
        self,
        system: SolarSystem,
        refresh_snapshot: bool,
        selected_body_id: str | None = None,
        elapsed_s: float = 0.0,
    ) -> None:
        self.system = self._clone_system(system)
        self.analysis_frame = AnalysisFrameSpec()
        if refresh_snapshot:
            self.loaded_system_snapshot = self._clone_system(self.system)
            self._set_dirty(False)
        self.simulation.replace_bodies(self.system.bodies, elapsed_s=elapsed_s)
        self.selected_index = self._body_index_for_id(selected_body_id)
        self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.focus_target = None
        self.focus_state = None
        self.selected_group_id = None
        self.canvas.reset_all_views()
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._populate_body_list()
        self._select_body(self.selected_index)
        self._load_system_editor()
        self._load_settings_editor()
        self.trail_frame_dropdown.set_sensitive(True)
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
        selected_index = max(0, min(index, len(self.system.bodies) - 1))
        preserve_focus = self._focus_contains_body(selected_index)
        if self.focus_state is not None and not preserve_focus:
            self._exit_focus()
        self.selected_index = selected_index
        if not preserve_focus:
            self.focus_group_id = self._group_id_for_body_index(self.selected_index)
        self.selected_group_id = None
        self.body_list.select_body(self.selected_index)
        self._load_body_editor(self.system.bodies[self.selected_index])
        self._sync_structural_controls()

    def _select_group(self, group_id: str) -> None:
        if self._group_by_id(group_id) is None:
            return
        preserve_focus = self._focus_contains_group(group_id)
        if self.focus_state is not None and not preserve_focus:
            self._exit_focus()
        self.body_list.select_group(group_id)
        if self.selected_group_id != group_id:
            if not preserve_focus:
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
        focus_button_target = (
            self.focus_state.target
            if self._focus_contains_body(self.selected_index)
            else self._body_focus_target(body)
        )
        self._configure_focus_button(focus_button_target)
        self._populate_selected_distance_list(body)
        self._load_orbit_editor(body)
        self._configure_position_spins()
        self.body_inspector.set_body_values(body, distance_factor)
        self.body_inspector.set_editing(False)

    def _load_group_focus(self, group_id: str) -> None:
        group = self._group_by_id(group_id)
        if group is None:
            return
        if not self._focus_contains_group(group.id):
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
        focus_button_target = (
            self.focus_state.target
            if self._focus_contains_group(group.id)
            else f"group:{group.id}"
        )
        self._configure_focus_button(focus_button_target)
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

    def _focus_contains_body(self, body_index: int) -> bool:
        return self.focus_state is not None and focus_target_contains_body(
            self.system.bodies,
            self.system.groups,
            self.focus_state.target,
            body_index,
        )

    def _focus_contains_group(self, group_id: str) -> bool:
        return self.focus_state is not None and focus_target_contains_group(
            self.system.bodies,
            self.system.groups,
            self.focus_state.target,
            group_id,
        )

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
        self.canvas.reset_all_views()
        self.zoom_factor = self.canvas.get_zoom_factor()
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

    def _on_transform_frame_clicked(self, _button) -> None:
        if not self._current_system_editable() or self.horizons_refresh_in_progress:
            return
        self._stop_playback_for_structural_work()
        source_system = self._clone_system(self.system)
        self.simulation.apply_to_bodies(source_system.bodies, source_system.groups)
        frame = source_system.reference_frame or SystemReferenceFrame(epoch=source_system.epoch)
        origin_options: list[tuple[str, str | None, str]] = [
            ("keep", None, "Keep Current Origin"),
            ("system", None, "Whole-System Mass Barycenter"),
            *[("body", body.id, f"Body: {body.name}") for body in source_system.bodies],
            *[("group", group.id, f"Group: {group.name}") for group in source_system.groups],
            ("jpl", "500@0", "JPL: Solar System Barycenter (500@0)"),
            ("jpl", "500@10", "JPL: Sun Center (500@10)"),
        ]
        known_jpl_origins = {"500@0", "500@10"}
        for body in source_system.bodies:
            if body.data_source is None:
                continue
            catalog_id = body.data_source.catalog_id.strip().removesuffix(";")
            center_id = f"500@{catalog_id}"
            if catalog_id and center_id not in known_jpl_origins:
                origin_options.append(("jpl", center_id, f"JPL: {body.name}"))
                known_jpl_origins.add(center_id)
        origin_options.append(("custom", None, "Custom Position and Velocity"))
        origin_dropdown = Gtk.DropDown.new_from_strings(
            [label for _kind, _target_id, label in origin_options]
        )

        custom_position_entry = Gtk.Entry(text="0, 0, 0")
        custom_velocity_entry = Gtk.Entry(text="0, 0, 0")
        custom_position_entry.set_sensitive(False)
        custom_velocity_entry.set_sensitive(False)

        rotation_mode_dropdown = Gtk.DropDown.new_from_strings(
            ["Guided X/Y/Z Angles", "Expert 3 × 3 Matrix"]
        )
        angle_spins = []
        angle_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        for row, axis in enumerate(("X", "Y", "Z")):
            spin = Gtk.SpinButton.new_with_range(-360.0, 360.0, 0.1)
            spin.set_digits(4)
            angle_spins.append(spin)
            self._attach_dialog_row(angle_grid, row, f"{axis} rotation (degrees)", spin)

        matrix_grid = Gtk.Grid(column_spacing=6, row_spacing=6)
        matrix_entries: list[list[Gtk.Entry]] = []
        for row in range(3):
            matrix_row = []
            for column in range(3):
                entry = Gtk.Entry(text="1" if row == column else "0")
                entry.set_width_chars(8)
                matrix_grid.attach(entry, column, row, 1, 1)
                matrix_row.append(entry)
            matrix_entries.append(matrix_row)
        rotation_stack = Gtk.Stack()
        rotation_stack.add_named(angle_grid, "guided")
        rotation_stack.add_named(matrix_grid, "matrix")
        rotation_stack.set_visible_child_name("guided")

        transform_type_dropdown = Gtk.DropDown.new_from_strings(
            ["Verified Standard Frame", "Custom Rigid Transform"]
        )
        transform_type_dropdown.set_selected(0 if frame.axes_id != "custom" else 1)
        axes_options = [
            (axes_id, f"{system} / {plane}")
            for axes_id, (system, plane) in REFERENCE_AXES.items()
            if axes_id != "custom"
        ]
        axes_dropdown = Gtk.DropDown.new_from_strings(
            [label for _axes_id, label in axes_options]
        )
        if frame.axes_id != "custom":
            for index, (axes_id, _label) in enumerate(axes_options):
                if axes_id == frame.axes_id:
                    axes_dropdown.set_selected(index)
                    break
        time_scales = ("UTC", "UT1", "TAI", "TT", "TDB", "TCB", "TCG")
        time_scale_dropdown = Gtk.DropDown.new_from_strings(list(time_scales))
        if frame.time_scale in time_scales:
            time_scale_dropdown.set_selected(time_scales.index(frame.time_scale))
        center_entry = Gtk.Entry(text=frame.center_id)
        plane_entry = Gtk.Entry(text=frame.reference_plane)
        system_entry = Gtk.Entry(text=frame.reference_system)
        try:
            current_epoch = shift_epoch(
                frame.epoch,
                frame.time_scale,
                self.simulation.state.elapsed_s,
            )
        except ModelError:
            current_epoch = frame.epoch
        epoch_entry = Gtk.Entry(text=current_epoch)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.append(Gtk.Label(label="Transform Type", xalign=0, css_classes=["heading"]))
        content.append(transform_type_dropdown)
        content.append(Gtk.Label(label="New Origin", xalign=0, css_classes=["heading"]))
        content.append(origin_dropdown)
        custom_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        self._attach_dialog_row(custom_grid, 0, "Origin position XYZ (m)", custom_position_entry)
        self._attach_dialog_row(custom_grid, 1, "Origin velocity XYZ (m/s)", custom_velocity_entry)
        content.append(custom_grid)
        content.append(Gtk.Label(label="Axis Rotation", xalign=0, css_classes=["heading"]))
        content.append(rotation_mode_dropdown)
        rotation_help = Gtk.Label(
            label=(
                "Guided rotations apply fixed X, then Y, then Z axes. Expert rows map "
                "old components into new components."
            ),
            xalign=0,
            wrap=True,
            css_classes=["dim-label"],
        )
        content.append(rotation_help)
        content.append(rotation_stack)
        content.append(Gtk.Label(label="Target Metadata", xalign=0, css_classes=["heading"]))
        metadata_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        for row, (label, widget) in enumerate(
            (
                ("Verified axes", axes_dropdown),
                ("Center ID", center_entry),
                ("Reference plane", plane_entry),
                ("Reference system", system_entry),
                ("Target epoch", epoch_entry),
                ("Time scale", time_scale_dropdown),
            )
        ):
            self._attach_dialog_row(metadata_grid, row, label, widget)
        content.append(metadata_grid)
        preview_label = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        content.append(preview_label)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(420)
        scroller.set_max_content_height(560)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(content)

        dialog = Adw.AlertDialog.new(
            "Transform Reference Frame",
            "Every canonical body position and velocity will be transformed atomically "
            "at the current simulation instant.",
        )
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("transform", "Transform")
        dialog.set_close_response("cancel")
        dialog.set_default_response("transform")
        dialog.set_response_appearance("transform", Adw.ResponseAppearance.SUGGESTED)

        controls = {
            "origin_options": origin_options,
            "source_system": source_system,
            "origin": origin_dropdown,
            "custom_position": custom_position_entry,
            "custom_velocity": custom_velocity_entry,
            "rotation_mode": rotation_mode_dropdown,
            "angles": angle_spins,
            "matrix": matrix_entries,
            "transform_type": transform_type_dropdown,
            "axes_options": axes_options,
            "axes": axes_dropdown,
            "center": center_entry,
            "plane": plane_entry,
            "system": system_entry,
            "epoch": epoch_entry,
            "time_scales": time_scales,
            "time_scale": time_scale_dropdown,
            "frame": frame,
        }

        def update_preview(*_args) -> None:
            try:
                if transform_type_dropdown.get_selected() == 0:
                    target = self._standard_reference_frame_from_controls(controls)
                    rotation_between_frames(frame, target)
                    preview_label.set_label(
                        "The complete N-body state will be propagated to the target epoch, "
                        "then translated and rotated atomically.\n"
                        f"Target: {target.center_id}, {target.reference_system}/"
                        f"{target.reference_plane}, {target.epoch} {target.time_scale}"
                    )
                else:
                    operation, target = self._reference_transform_from_controls(controls)
                    position = ", ".join(
                        f"{value:.6g}" for value in operation.origin_position_m
                    )
                    velocity = ", ".join(
                        f"{value:.6g}" for value in operation.origin_velocity_mps
                    )
                    preview_label.set_label(
                        f"r′ = R(r − [{position}])\n"
                        f"v′ = R(v − [{velocity}])\n"
                        f"Target: custom axes, {target.center_id}"
                    )
                dialog.set_response_enabled("transform", True)
            except (ModelError, ValueError) as error:
                preview_label.set_label(str(error))
                dialog.set_response_enabled("transform", False)

        def update_origin(dropdown, _param) -> None:
            selected = dropdown.get_selected()
            kind, target_id, _label = origin_options[
                selected if selected < len(origin_options) else 0
            ]
            custom = (
                kind == "custom" and transform_type_dropdown.get_selected() == 1
            )
            custom_position_entry.set_sensitive(custom)
            custom_velocity_entry.set_sensitive(custom)
            if kind == "keep":
                center_entry.set_text(frame.center_id)
            elif kind == "system":
                center_entry.set_text("system-barycenter")
            elif kind == "body" and target_id is not None:
                body = next(item for item in source_system.bodies if item.id == target_id)
                catalog_id = body.data_source.catalog_id if body.data_source is not None else ""
                center_entry.set_text(f"500@{catalog_id}" if catalog_id else f"body:{body.id}")
            elif kind == "group" and target_id is not None:
                center_entry.set_text(f"group:{target_id}")
            elif kind == "jpl" and target_id is not None:
                center_entry.set_text(target_id)
            update_preview()

        def update_transform_type(dropdown, _param) -> None:
            standard = dropdown.get_selected() == 0
            axes_dropdown.set_sensitive(standard)
            epoch_entry.set_sensitive(standard)
            time_scale_dropdown.set_sensitive(standard)
            center_entry.set_sensitive(False)
            plane_entry.set_sensitive(not standard)
            system_entry.set_sensitive(not standard)
            rotation_mode_dropdown.set_sensitive(not standard)
            rotation_stack.set_sensitive(not standard)
            custom_selected = origin_options[origin_dropdown.get_selected()][0] == "custom"
            if standard and custom_selected:
                origin_dropdown.set_selected(0)
            custom_position_entry.set_sensitive(not standard and custom_selected)
            custom_velocity_entry.set_sensitive(not standard and custom_selected)
            update_preview()

        def update_axes(dropdown, _param) -> None:
            selected = dropdown.get_selected()
            axes_id = axes_options[selected if selected < len(axes_options) else 0][0]
            reference_system, reference_plane = REFERENCE_AXES[axes_id]
            system_entry.set_text(reference_system)
            plane_entry.set_text(reference_plane)
            update_preview()

        def update_rotation_mode(dropdown, _param) -> None:
            if dropdown.get_selected() == 1:
                rotation = rotation_matrix_from_xyz_degrees(
                    *(spin.get_value() for spin in angle_spins)
                )
                for row in range(3):
                    for column in range(3):
                        matrix_entries[row][column].set_text(
                            f"{rotation[row][column]:.15g}"
                        )
            rotation_stack.set_visible_child_name(
                "matrix" if dropdown.get_selected() == 1 else "guided"
            )
            update_preview()

        origin_dropdown.connect("notify::selected", update_origin)
        transform_type_dropdown.connect("notify::selected", update_transform_type)
        rotation_mode_dropdown.connect("notify::selected", update_rotation_mode)
        axes_dropdown.connect("notify::selected", update_axes)
        time_scale_dropdown.connect("notify::selected", update_preview)
        for entry in (
            custom_position_entry,
            custom_velocity_entry,
            center_entry,
            plane_entry,
            system_entry,
            epoch_entry,
            *[entry for row in matrix_entries for entry in row],
        ):
            entry.connect("changed", update_preview)
        for spin in angle_spins:
            spin.connect("value-changed", update_preview)
        dialog.connect(
            "response",
            lambda source, response: self._finish_reference_frame_dialog(
                source,
                response,
                controls,
            ),
        )
        if transform_type_dropdown.get_selected() == 0:
            update_axes(axes_dropdown, None)
        update_transform_type(transform_type_dropdown, None)
        dialog.present(self)

    def _reference_transform_from_controls(
        self,
        controls: dict,
    ) -> tuple[ReferenceFrameTransform, SystemReferenceFrame]:
        origin_options = controls["origin_options"]
        source_system = controls["source_system"]
        selected = controls["origin"].get_selected()
        kind, target_id, _label = origin_options[
            selected if selected < len(origin_options) else 0
        ]
        if kind == "keep":
            position = velocity = (0.0, 0.0, 0.0)
        elif kind == "system":
            position, velocity = origin_for_system(source_system)
        elif kind == "body" and target_id is not None:
            position, velocity = origin_for_body(source_system, target_id)
        elif kind == "group" and target_id is not None:
            position, velocity = origin_for_group(source_system, target_id)
        elif kind == "jpl" and target_id is not None:
            frame = controls["frame"]
            if (
                frame.origin is None
                or frame.origin.kind != "jpl"
                or frame.origin.id != target_id
            ):
                raise ModelError(
                    "JPL origin changes require the verified standard-frame transform"
                )
            position = velocity = (0.0, 0.0, 0.0)
        elif kind == "custom":
            position = self._parse_vector_entry(
                controls["custom_position"].get_text(),
                "origin position",
            )
            velocity = self._parse_vector_entry(
                controls["custom_velocity"].get_text(),
                "origin velocity",
            )
        else:
            raise ModelError("select a valid reference-frame origin")

        if controls["rotation_mode"].get_selected() == 1:
            rotation = validate_rotation_matrix(
                [
                    [float(entry.get_text()) for entry in row]
                    for row in controls["matrix"]
                ]
            )
        else:
            rotation = rotation_matrix_from_xyz_degrees(
                *(spin.get_value() for spin in controls["angles"])
            )
        operation = ReferenceFrameTransform(position, velocity, rotation).validated()
        scale_index = controls["time_scale"].get_selected()
        target = SystemReferenceFrame(
            epoch=controls["epoch"].get_text().strip(),
            time_scale=controls["time_scales"][scale_index],
            reference_plane=controls["plane"].get_text().strip(),
            reference_system=controls["system"].get_text().strip(),
            axes_id="custom",
            origin=self._reference_origin_from_controls(controls),
        )
        target.validate()
        return operation, target

    def _reference_origin_from_controls(self, controls: dict) -> ReferenceOrigin:
        options = controls["origin_options"]
        selected = controls["origin"].get_selected()
        kind, target_id, _label = options[selected if selected < len(options) else 0]
        if kind == "keep":
            current = controls["frame"].origin
            if current is None:
                raise ModelError("current reference origin is unavailable")
            return ReferenceOrigin.from_dict(current.to_dict())
        if kind == "system":
            return ReferenceOrigin("system_barycenter", None)
        if kind == "body":
            return ReferenceOrigin("body", target_id)
        if kind == "group":
            return ReferenceOrigin("group_barycenter", target_id)
        if kind == "jpl":
            return ReferenceOrigin("jpl", target_id)
        if kind == "custom":
            return ReferenceOrigin("custom", "user-defined")
        raise ModelError("select a valid reference-frame origin")

    def _standard_reference_frame_from_controls(
        self,
        controls: dict,
    ) -> SystemReferenceFrame:
        source = controls["frame"]
        if source.axes_id == "custom":
            raise ModelError(
                "the current custom axes cannot be converted automatically; first use a "
                "verified import or metadata source"
            )
        axes_options = controls["axes_options"]
        axes_index = controls["axes"].get_selected()
        axes_id = axes_options[axes_index][0]
        scale_index = controls["time_scale"].get_selected()
        time_scale = controls["time_scales"][scale_index]
        epoch = controls["epoch"].get_text().strip()
        parse_epoch(epoch, time_scale)
        origin = self._reference_origin_from_controls(controls)
        selected_origin = controls["origin_options"][
            controls["origin"].get_selected()
        ][0]
        if selected_origin == "custom":
            raise ModelError("verified transforms require a defined origin")
        if origin.kind == "jpl" and (
            source.origin is None or source.origin.kind != "jpl"
        ):
            raise ModelError(
                "a JPL origin change requires a current JPL-addressable origin"
            )
        target = SystemReferenceFrame(
            epoch=epoch,
            time_scale=time_scale,
            axes_id=axes_id,
            origin=origin,
        )
        target.validate()
        return target

    def _finish_reference_frame_dialog(self, _dialog, response: str, controls: dict) -> None:
        if response != "transform":
            return
        selected_body_id = (
            self.system.bodies[self.selected_index].id if self.system.bodies else None
        )
        elapsed_s = self.simulation.state.elapsed_s
        if controls["transform_type"].get_selected() == 0:
            try:
                target = self._standard_reference_frame_from_controls(controls)
            except (ModelError, ValueError, IndexError) as error:
                self._show_error_dialog("Cannot Transform Reference Frame", str(error))
                return
            self._start_standard_reference_transform(
                controls["source_system"],
                target,
                elapsed_s,
                selected_body_id,
            )
            return
        try:
            operation, target = self._reference_transform_from_controls(controls)
            transformed = transform_system_reference_frame(
                controls["source_system"],
                target,
                operation,
            )
            transformed.epoch = (
                f"{target.epoch} {target.time_scale}, custom axes, {target.center_id}"
            )
            transformed.validate()
        except (ModelError, ValueError) as error:
            self._show_error_dialog("Cannot Transform Reference Frame", str(error))
            return
        self._replace_system(
            transformed,
            refresh_snapshot=False,
            selected_body_id=selected_body_id,
            elapsed_s=0.0,
        )
        self.system_library.refresh(self.system)
        self._mark_dirty()
        self._sync_structural_controls()

    def _start_standard_reference_transform(
        self,
        source_system: SolarSystem,
        target: SystemReferenceFrame,
        elapsed_s: float,
        selected_body_id: str | None,
    ) -> None:
        source_frame = source_system.reference_frame
        if source_frame is None:
            self._show_error_dialog(
                "Cannot Transform Reference Frame",
                "the current system has no reference-frame metadata",
            )
            return
        self.horizons_generation += 1
        generation = self.horizons_generation
        cancel_event = threading.Event()
        self.horizons_refresh_cancel = cancel_event
        self.horizons_refresh_in_progress = True
        self._sync_structural_controls()

        progress_dialog = Adw.AlertDialog.new(
            "Transforming Reference Frame",
            "Preparing epoch propagation and coordinate transform…",
        )
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        progress_box.set_margin_top(12)
        progress_box.set_margin_bottom(12)
        progress_box.append(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER))
        progress_label = Gtk.Label(label="Starting", xalign=0.5, wrap=True)
        progress_box.append(progress_label)
        progress_dialog.set_extra_child(progress_box)
        progress_dialog.add_response("cancel", "Cancel")
        progress_dialog.set_close_response("cancel")
        dialog_state = {"finishing": False}

        def cancel_transform(_dialog, _response: str) -> None:
            if dialog_state["finishing"]:
                return
            cancel_event.set()
            if generation == self.horizons_generation:
                self.horizons_generation += 1
            if self.horizons_refresh_cancel is cancel_event:
                self.horizons_refresh_cancel = None
            self.horizons_refresh_in_progress = False
            self._sync_structural_controls()

        progress_dialog.connect("response", cancel_transform)
        progress_dialog.present(self)

        def report_progress(completed: int, total: int) -> None:
            GLib.idle_add(
                self._update_reference_transform_progress,
                generation,
                progress_label,
                completed,
                total,
            )

        def perform_transform() -> SolarSystem:
            external_origin = None
            if target.origin is not None and target.origin.kind == "jpl":
                change = self.horizons_client.fetch_origin_change(
                    source_frame,
                    target.origin.id or "",
                    target_epoch=target.epoch,
                    target_time_scale=target.time_scale,
                )
                external_origin = (change.position_m, change.velocity_mps)
            return transform_system_to_standard_frame(
                source_system,
                target,
                elapsed_s=elapsed_s,
                external_origin=external_origin,
                cancel_event=cancel_event,
                progress=report_progress,
            )

        future = self.horizons_executor.submit(perform_transform)
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._finish_standard_reference_transform,
                completed,
                generation,
                progress_dialog,
                dialog_state,
                cancel_event,
                selected_body_id,
            )
        )

    def _update_reference_transform_progress(
        self,
        generation: int,
        label,
        completed: int,
        total: int,
    ) -> bool:
        if self.closed or generation != self.horizons_generation:
            return GLib.SOURCE_REMOVE
        label.set_label(f"Propagating full N-body state: {completed} of {total} steps")
        return GLib.SOURCE_REMOVE

    def _finish_standard_reference_transform(
        self,
        future: Future,
        generation: int,
        progress_dialog,
        dialog_state: dict,
        cancel_event: threading.Event,
        selected_body_id: str | None,
    ) -> bool:
        if self.closed or generation != self.horizons_generation:
            return GLib.SOURCE_REMOVE
        dialog_state["finishing"] = True
        progress_dialog.close()
        if self.horizons_refresh_cancel is cancel_event:
            self.horizons_refresh_cancel = None
        self.horizons_refresh_in_progress = False
        try:
            transformed = future.result()
        except Exception as error:
            if not cancel_event.is_set():
                self._show_error_dialog("Cannot Transform Reference Frame", str(error))
            self._sync_structural_controls()
            return GLib.SOURCE_REMOVE
        self._replace_system(
            transformed,
            refresh_snapshot=False,
            selected_body_id=selected_body_id,
            elapsed_s=0.0,
        )
        self.system_library.refresh(self.system)
        self._mark_dirty()
        self._sync_structural_controls()
        return GLib.SOURCE_REMOVE

    def _orbit_editor_widgets(self):
        return self.body_inspector.orbit_editor_widgets()

    def _on_refresh_horizons_clicked(self, _button) -> None:
        self._request_horizons_refresh(after_add=False)

    def _request_horizons_refresh(self, *, after_add: bool) -> None:
        if self.horizons_refresh_in_progress:
            return
        bodies = horizons_refreshable_bodies(self.system)
        if not bodies:
            self._show_error_dialog(
                "Horizons Refresh Unavailable",
                "This system does not contain refreshable JPL Horizons bodies in a compatible frame.",
            )
            return
        preset = not self.system_library.is_user_saved(self.system)
        manual_count = len(self.system.bodies) - len(bodies)
        body_word = "body" if len(bodies) == 1 else "bodies"
        if after_add:
            title = "Refresh Horizons Bodies?"
            message = (
                f"Update {len(bodies)} JPL Horizons {body_word} to the current instant?"
            )
        else:
            title = "Refresh from JPL Horizons?"
            message = (
                f"Fetch current positions, velocities, and orbital elements for "
                f"{len(bodies)} JPL Horizons {body_word}."
            )
        if manual_count:
            if manual_count == 1:
                message += "\n\n1 non-Horizons body keeps its entered Cartesian state."
            else:
                message += (
                    f"\n\n{manual_count} non-Horizons bodies keep their entered "
                    "Cartesian states."
                )
        if preset:
            message += "\n\nA saved editable copy of this bundled preset will be created."

        dialog = Adw.AlertDialog.new(title, message)
        dialog.add_response("cancel", "Not Now" if after_add else "Cancel")
        dialog.add_response("refresh", "Refresh All")
        dialog.set_close_response("cancel")
        dialog.set_default_response("refresh")
        dialog.set_response_appearance("refresh", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _dialog, response: (
                self._start_horizons_refresh() if response == "refresh" else None
            ),
        )
        dialog.present(self)

    def _start_horizons_refresh(self) -> None:
        if self.horizons_refresh_in_progress or not horizons_refresh_available(self.system):
            return
        self._stop_playback_for_structural_work()
        snapshot = self._clone_system(self.system)
        selected_body_id = (
            self.system.bodies[self.selected_index].id if self.system.bodies else None
        )
        preset = not self.system_library.is_user_saved(self.system)
        source_name = self.system.name
        captured_utc = datetime.now(timezone.utc)

        self.horizons_generation += 1
        generation = self.horizons_generation
        cancel_event = threading.Event()
        self.horizons_refresh_cancel = cancel_event
        self.horizons_refresh_in_progress = True
        self._sync_structural_controls()

        progress_dialog = Adw.AlertDialog.new(
            "Refreshing JPL Horizons Bodies",
            "Preparing requests...",
        )
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        progress_box.set_margin_top(12)
        progress_box.set_margin_bottom(12)
        spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        progress_label = Gtk.Label(label="Starting refresh", xalign=0.5, wrap=True)
        progress_box.append(spinner)
        progress_box.append(progress_label)
        progress_dialog.set_extra_child(progress_box)
        progress_dialog.add_response("cancel", "Cancel")
        progress_dialog.set_close_response("cancel")
        dialog_state = {"finishing": False}

        def cancel_refresh(_dialog, _response: str) -> None:
            if dialog_state["finishing"]:
                return
            cancel_event.set()
            if generation == self.horizons_generation:
                self.horizons_generation += 1
            if self.horizons_refresh_cancel is cancel_event:
                self.horizons_refresh_cancel = None
            self.horizons_refresh_in_progress = False
            self._sync_structural_controls()

        progress_dialog.connect("response", cancel_refresh)
        progress_dialog.present(self)

        def report_progress(
            completed: int,
            total: int,
            body_name: str,
            stage: str,
        ) -> None:
            GLib.idle_add(
                self._update_horizons_refresh_progress,
                generation,
                progress_label,
                completed,
                total,
                body_name,
                stage,
            )

        future = self.horizons_executor.submit(
            self.horizons_client.fetch_system_refresh,
            snapshot,
            captured_utc,
            cancel_event=cancel_event,
            progress=report_progress,
        )
        future.add_done_callback(
            lambda completed: GLib.idle_add(
                self._finish_horizons_refresh,
                completed,
                generation,
                progress_dialog,
                dialog_state,
                cancel_event,
                preset,
                source_name,
                selected_body_id,
            )
        )

    def _update_horizons_refresh_progress(
        self,
        generation: int,
        label,
        completed: int,
        total: int,
        body_name: str,
        stage: str,
    ) -> bool:
        if self.closed or generation != self.horizons_generation:
            return GLib.SOURCE_REMOVE
        request_number = total if completed >= total else completed + 1
        label.set_label(f"{stage}\n{body_name}\nRequest {request_number} of {total}")
        return GLib.SOURCE_REMOVE

    def _finish_horizons_refresh(
        self,
        future: Future,
        generation: int,
        progress_dialog,
        dialog_state: dict,
        cancel_event: threading.Event,
        preset: bool,
        source_name: str,
        selected_body_id: str | None,
    ) -> bool:
        if self.closed or generation != self.horizons_generation:
            return GLib.SOURCE_REMOVE
        dialog_state["finishing"] = True
        progress_dialog.close()
        if self.horizons_refresh_cancel is cancel_event:
            self.horizons_refresh_cancel = None
        self.horizons_refresh_in_progress = False
        try:
            refresh = future.result()
            updated = apply_system_refresh(self.system, refresh)
        except Exception as error:
            self._sync_structural_controls()
            self._show_error_dialog("Horizons Refresh Failed", str(error))
            return GLib.SOURCE_REMOVE

        if preset:
            updated = updated.duplicate(f"{source_name} Updated")
            self.system_library.save_new_system(updated)
            result_message = (
                f"Created and selected the editable saved system '{updated.name}'."
            )
        else:
            self._replace_system(
                updated,
                refresh_snapshot=False,
                selected_body_id=selected_body_id,
            )
            self.system_library.refresh(self.system)
            self._mark_dirty()
            result_message = "The refreshed system has unsaved changes."
        self._sync_structural_controls()
        self._show_information_dialog(
            "Horizons Refresh Complete",
            f"Updated {len(refresh.bodies)} bodies at {refresh.tdb_epoch} TDB.\n\n{result_message}",
        )
        return GLib.SOURCE_REMOVE

    def _show_information_dialog(self, title: str, message: str) -> None:
        dialog = Adw.AlertDialog.new(title, message)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(self)

    def _set_orbit_editor_sensitive(self, sensitive: bool) -> None:
        self.body_inspector.set_orbit_editor_sensitive(sensitive)

    def _load_orbit_editor(self, body: Body | None) -> None:
        parent = None
        if body is not None and body.parent_id is not None:
            parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)

        self._show_group_orbit_controls(False)
        self.edit_flyby_button.set_visible(False)
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
            elif body.flyby is not None:
                anchor = next(
                    (
                        candidate
                        for candidate in self.system.bodies
                        if candidate.id == body.flyby.anchor_body_id
                    ),
                    None,
                )
                self._load_orbit_values(body.orbit, None, 0.0)
                self._set_orbit_editor_sensitive(False)
                self.body_inspector.set_generate_body_orbit_sensitive(False)
                self.edit_flyby_button.set_visible(True)
                self.edit_flyby_button.set_sensitive(editable)
                anchor_name = anchor.name if anchor is not None else "its anchor"
                phase = (
                    "currently inbound"
                    if anchor is not None and radial_velocity_mps(anchor, body) < 0.0
                    else "currently outbound"
                )
                self.body_inspector.set_orbit_status(
                    f"Configured hyperbolic flyby around {anchor_name}; {phase}. "
                    "Use Edit Flyby to regenerate its initial state."
                )
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
        self.edit_flyby_button.set_visible(False)
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
                preserve_metadata=not physics_changed,
            )
        except ModelError as error:
            self._show_error_dialog("Cannot Apply Body Changes", str(error))
            self._load_body_editor(self.system.bodies[self.selected_index])
            return
        if physics_changed:
            body.orbit = None
            body.data_source = None
            body.state_origin = "cartesian"
            body.flyby = None
            self._set_playing(False)
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

    def _on_physics_mode_changed(self, _panel, physics_mode: str) -> None:
        if physics_mode == self.system.settings.physics_mode:
            return
        self.system.settings.physics_mode = physics_mode
        self._mark_dirty_if_editable()
        self._clear_dynamic_simulation_state()
        self._update_title()
        self._refresh_canvas()

    def _on_integrator_changed(self, _panel, integrator: str) -> None:
        if integrator == self.system.settings.integrator:
            return
        self.system.settings.integrator = integrator
        self._mark_dirty_if_editable()
        self._clear_dynamic_simulation_state()
        self._update_title()
        self._refresh_canvas()

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

    def _on_trail_frame_changed(self, _panel, trail_frame: str) -> None:
        if trail_frame == self.system.settings.trail_frame:
            return
        self.system.settings.trail_frame = trail_frame
        self._mark_dirty_if_editable()
        self._clear_dynamic_simulation_state()
        self._update_title()
        self._refresh_canvas()

    def _on_orbit_visibility_changed(self, _panel, visibility: str) -> None:
        if visibility == self.system.settings.orbit_visibility:
            return
        self.system.settings.orbit_visibility = visibility
        self._mark_dirty_if_editable()
        self._refresh_canvas()

    def _on_trail_visibility_changed(self, _panel, visibility: str) -> None:
        if visibility == self.system.settings.trail_visibility:
            return
        self.system.settings.trail_visibility = visibility
        self._mark_dirty_if_editable()
        self._refresh_canvas()

    def _on_path_style_changed(self, _panel, path_style: str) -> None:
        if path_style == self.system.settings.path_style:
            return
        self.system.settings.path_style = path_style
        self._mark_dirty_if_editable()
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
        self._set_playing(False)
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
            self._after_structural_edit(
                selected_body_id=body_id,
                offer_horizons_refresh=True,
            )

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
        self._set_playing(False)
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
        self._after_structural_edit(
            selected_group_id=group.id,
            offer_horizons_refresh=True,
        )

    def _on_add_star_clicked(self, _button) -> None:
        self._show_manual_body_dialog("star", "Add Star", "New Star", [])

    def _on_add_flyby_clicked(self, _button) -> None:
        self._show_flyby_dialog(None)

    def _on_edit_flyby_clicked(self, _button) -> None:
        if not self.system.bodies or self.selected_group_id is not None:
            return
        body = self.system.bodies[self.selected_index]
        if body.flyby is not None:
            self._show_flyby_dialog(body)

    def _show_flyby_dialog(self, body: Body | None) -> None:
        anchors = [
            candidate
            for candidate in self.system.bodies
            if candidate.kind == "star"
            and candidate.parent_id is None
            and candidate.flyby is None
            and (body is None or candidate.id != body.id)
        ]
        if not anchors:
            self._show_error_dialog(
                "Cannot Add Flyby",
                "A flyby requires an existing root star as its encounter anchor.",
            )
            return

        default_anchor, default_periapsis_m = self._default_flyby_anchor(anchors)
        if body is not None and body.flyby is not None:
            default_anchor = next(
                (
                    anchor
                    for anchor in anchors
                    if anchor.id == body.flyby.anchor_body_id
                ),
                default_anchor,
            )
            default_periapsis_m = body.flyby.periapsis_distance_m

        initial_kind = body.kind if body is not None else "star"
        mass_default, radius_default, color_default = BODY_DEFAULTS[initial_kind]
        template_dropdown = Gtk.DropDown.new_from_strings(["Custom", "Proxima-like"])
        kind_dropdown = Gtk.DropDown.new_from_strings(
            [kind.title() for kind in FLYBY_KIND_OPTIONS]
        )
        kind_dropdown.set_selected(FLYBY_KIND_OPTIONS.index(initial_kind))
        anchor_dropdown = Gtk.DropDown.new_from_strings([anchor.name for anchor in anchors])
        anchor_dropdown.set_selected(anchors.index(default_anchor))
        name_entry = Gtk.Entry(text=body.name if body is not None else "New Flyby")
        mass_entry = Gtk.Entry(
            text=f"{body.mass_kg if body is not None else mass_default:.12g}"
        )
        radius_entry = Gtk.Entry(
            text=f"{body.radius_m if body is not None else radius_default:.12g}"
        )
        color_entry = Gtk.Entry(text=body.color if body is not None else color_default)
        visible_switch = Gtk.Switch(
            active=body.visible if body is not None else True,
            halign=Gtk.Align.START,
        )
        trail_switch = Gtk.Switch(
            active=body.trail_enabled if body is not None else True,
            halign=Gtk.Align.START,
        )
        existing = body.flyby if body is not None else None
        periapsis_spin = self._dialog_spin(
            1.0e-9,
            1.0e9,
            default_periapsis_m / AU,
            6,
            0.1,
        )
        start_spin = self._dialog_spin(
            1.0e-9,
            1.0e9,
            (existing.start_distance_m if existing else 5.0 * default_periapsis_m) / AU,
            6,
            1.0,
        )
        velocity_spin = self._dialog_spin(
            0.001,
            1.0e6,
            (existing.velocity_at_infinity_mps / 1000.0) if existing else 20.0,
            3,
            1.0,
        )
        inclination_spin = self._dialog_spin(
            -360.0,
            360.0,
            existing.inclination_deg if existing else 0.0,
            3,
            1.0,
        )
        node_spin = self._dialog_spin(
            -360.0,
            360.0,
            existing.longitude_of_ascending_node_deg if existing else 0.0,
            3,
            1.0,
        )
        argument_spin = self._dialog_spin(
            -360.0,
            360.0,
            existing.argument_of_periapsis_deg if existing else 0.0,
            3,
            1.0,
        )
        preview_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        preview_label.add_css_class("dim-label")

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        rows = (
            ("Template", template_dropdown),
            ("Name", name_entry),
            ("Kind", kind_dropdown),
            ("Encounter Anchor", anchor_dropdown),
            ("Mass (kg)", mass_entry),
            ("Radius (m)", radius_entry),
            ("Color", color_entry),
            ("Visible", visible_switch),
            ("Trail", trail_switch),
            ("Periapsis Distance (AU)", periapsis_spin),
            ("Starting Distance (AU)", start_spin),
            ("Velocity at Infinity (km/s)", velocity_spin),
            ("Inclination (deg)", inclination_spin),
            ("Ascending Node (deg)", node_spin),
            ("Periapsis Argument (deg)", argument_spin),
            ("Derived Initial State", preview_label),
        )
        for row, (label, widget) in enumerate(rows):
            self._attach_dialog_row(grid, row, label, widget)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        grid.set_margin_start(6)
        grid.set_margin_end(6)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(560)
        scroller.set_propagate_natural_width(True)
        scroller.set_child(grid)

        title = "Edit Flyby" if body is not None else "Add Flyby"
        dialog = Adw.AlertDialog.new(
            title,
            "The body starts inbound and remains in the system after periapsis.",
        )
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Regenerate" if body is not None else "Add Flyby")
        dialog.set_close_response("cancel")
        dialog.set_default_response("create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)

        controls = {
            "name": name_entry,
            "kind": kind_dropdown,
            "anchor": anchor_dropdown,
            "mass": mass_entry,
            "radius": radius_entry,
            "color": color_entry,
            "visible": visible_switch,
            "trail": trail_switch,
            "periapsis": periapsis_spin,
            "start": start_spin,
            "velocity": velocity_spin,
            "inclination": inclination_spin,
            "node": node_spin,
            "argument": argument_spin,
            "preview": preview_label,
        }

        def current_input() -> tuple[BodyStateInput, FlybyData]:
            kind_index = kind_dropdown.get_selected()
            anchor_index = anchor_dropdown.get_selected()
            if kind_index >= len(FLYBY_KIND_OPTIONS) or anchor_index >= len(anchors):
                raise ModelError("select a flyby kind and encounter anchor")
            mass = float(mass_entry.get_text())
            radius = float(radius_entry.get_text())
            if mass <= 0.0 or radius <= 0.0 or not all(
                math.isfinite(value) for value in (mass, radius)
            ):
                raise ModelError("mass and radius must be finite and positive")
            state = BodyStateInput(
                name=name_entry.get_text(),
                kind=FLYBY_KIND_OPTIONS[kind_index],
                mass_kg=mass,
                radius_m=radius,
                position_m=(0.0, 0.0, 0.0),
                velocity_mps=(0.0, 0.0, 0.0),
                color=color_entry.get_text().strip() or BODY_DEFAULTS[FLYBY_KIND_OPTIONS[kind_index]][2],
                visible=visible_switch.get_active(),
                trail_enabled=trail_switch.get_active(),
            )
            flyby = FlybyData(
                anchor_body_id=anchors[anchor_index].id,
                periapsis_distance_m=periapsis_spin.get_value() * AU,
                velocity_at_infinity_mps=velocity_spin.get_value() * 1000.0,
                start_distance_m=start_spin.get_value() * AU,
                inclination_deg=inclination_spin.get_value(),
                longitude_of_ascending_node_deg=node_spin.get_value(),
                argument_of_periapsis_deg=argument_spin.get_value(),
            )
            flyby.validate()
            return state, flyby

        def update_preview(*_args) -> None:
            try:
                state, flyby = current_input()
                anchor = next(item for item in anchors if item.id == flyby.anchor_body_id)
                preview_body = Body(
                    name=state.name.strip() or "Flyby",
                    kind=state.kind,
                    mass_kg=state.mass_kg,
                    radius_m=state.radius_m,
                    position_m=[0.0, 0.0, 0.0],
                    velocity_mps=[0.0, 0.0, 0.0],
                    color=state.color,
                    id=body.id if body is not None else "flyby-preview",
                )
                frame = self.system.reference_frame
                solution = solve_flyby(
                    anchor,
                    preview_body,
                    flyby,
                    epoch=frame.epoch if frame is not None else self.system.epoch,
                    reference_plane=frame.reference_plane if frame is not None else "app-local XY",
                )
                position = ", ".join(f"{value / AU:.4f}" for value in solution.position_m)
                velocity = ", ".join(f"{value / 1000.0:.4f}" for value in solution.velocity_mps)
                preview_label.set_label(
                    f"e = {solution.orbit.eccentricity:.6g}; "
                    f"a = {solution.orbit.semi_major_axis_m / AU:.6g} AU\n"
                    f"XYZ = ({position}) AU\nV = ({velocity}) km/s"
                )
                dialog.set_response_enabled("create", bool(state.name.strip()))
            except (ModelError, ValueError, StopIteration) as error:
                preview_label.set_label(str(error))
                dialog.set_response_enabled("create", False)

        def apply_template(dropdown, _param) -> None:
            if dropdown.get_selected() != 1:
                return
            kind_dropdown.set_selected(FLYBY_KIND_OPTIONS.index("star"))
            name_entry.set_text("Proxima-like Flyby")
            mass_entry.set_text(f"{0.1221 * BODY_DEFAULTS['star'][0]:.12g}")
            radius_entry.set_text(f"{0.1542 * BODY_DEFAULTS['star'][1]:.12g}")
            color_entry.set_text("#ff7b54")
            update_preview()

        def apply_kind_defaults(dropdown, _param) -> None:
            if template_dropdown.get_selected() != 0:
                return
            selected = dropdown.get_selected()
            if selected >= len(FLYBY_KIND_OPTIONS):
                return
            kind = FLYBY_KIND_OPTIONS[selected]
            mass, radius, color = BODY_DEFAULTS[kind]
            mass_entry.set_text(f"{mass:.12g}")
            radius_entry.set_text(f"{radius:.12g}")
            color_entry.set_text(color)
            if body is None:
                name_entry.set_text(f"New {kind.title()} Flyby")
            update_preview()

        template_dropdown.connect("notify::selected", apply_template)
        kind_dropdown.connect("notify::selected", apply_kind_defaults)
        anchor_dropdown.connect("notify::selected", update_preview)
        mass_entry.connect("changed", update_preview)
        radius_entry.connect("changed", update_preview)
        name_entry.connect("changed", update_preview)
        for spin in (
            periapsis_spin,
            start_spin,
            velocity_spin,
            inclination_spin,
            node_spin,
            argument_spin,
        ):
            spin.connect("value-changed", update_preview)
        dialog.connect(
            "response",
            lambda source, response: self._finish_flyby_dialog(
                source,
                response,
                body.id if body is not None else None,
                anchors,
                controls,
            ),
        )
        update_preview()
        dialog.present(self)

    def _default_flyby_anchor(self, anchors: list[Body]) -> tuple[Body, float]:
        selected = None
        if self.selected_group_id is None and self.system.bodies:
            selected = self.system.bodies[self.selected_index]
        elif self.selected_group_id is not None:
            anchor_ids = {anchor.id for anchor in anchors}
            candidates = [
                self.system.bodies[index]
                for index in self._body_indices_for_group(self.selected_group_id)
                if self.system.bodies[index].id in anchor_ids
            ]
            if candidates:
                selected = max(candidates, key=lambda item: item.mass_kg)

        bodies_by_id = {candidate.id: candidate for candidate in self.system.bodies}
        root = selected
        while root is not None and root.parent_id is not None:
            root = bodies_by_id.get(root.parent_id)
        anchor = (
            next((item for item in anchors if root is not None and item.id == root.id), None)
            or max(anchors, key=lambda item: item.mass_kg)
        )
        periapsis_m = 20.0 * AU
        if selected is not None and selected.id != anchor.id:
            selected_root = selected
            while selected_root.parent_id is not None:
                parent = bodies_by_id.get(selected_root.parent_id)
                if parent is None:
                    break
                selected_root = parent
            if selected_root.id == anchor.id:
                periapsis_m = distance_between_bodies_m(selected, anchor)
        return anchor, periapsis_m

    def _finish_flyby_dialog(
        self,
        _dialog,
        response: str,
        body_id: str | None,
        anchors: list[Body],
        controls: dict,
    ) -> None:
        if response != "create":
            return
        try:
            self._ensure_editable_for_structural_edit()
            kind_index = controls["kind"].get_selected()
            anchor_index = controls["anchor"].get_selected()
            if kind_index >= len(FLYBY_KIND_OPTIONS) or anchor_index >= len(anchors):
                raise ModelError("select a flyby kind and encounter anchor")
            kind = FLYBY_KIND_OPTIONS[kind_index]
            mass = float(controls["mass"].get_text())
            radius = float(controls["radius"].get_text())
            state = BodyStateInput(
                name=controls["name"].get_text(),
                kind=kind,
                mass_kg=mass,
                radius_m=radius,
                position_m=(0.0, 0.0, 0.0),
                velocity_mps=(0.0, 0.0, 0.0),
                color=controls["color"].get_text().strip() or BODY_DEFAULTS[kind][2],
                visible=controls["visible"].get_active(),
                trail_enabled=controls["trail"].get_active(),
            )
            flyby = FlybyData(
                anchor_body_id=anchors[anchor_index].id,
                periapsis_distance_m=controls["periapsis"].get_value() * AU,
                velocity_at_infinity_mps=controls["velocity"].get_value() * 1000.0,
                start_distance_m=controls["start"].get_value() * AU,
                inclination_deg=controls["inclination"].get_value(),
                longitude_of_ascending_node_deg=controls["node"].get_value(),
                argument_of_periapsis_deg=controls["argument"].get_value(),
            )
            candidate = self._clone_system(self.system)
            if body_id is None:
                result = add_flyby_from_state(candidate, state, flyby)
            else:
                update_body_from_state(
                    candidate,
                    body_id,
                    state,
                    preserve_metadata=False,
                )
                result = regenerate_flyby(candidate, body_id, flyby)
            candidate.settings.simulation_scope = "full_nbody"
            candidate.validate()
        except (ModelError, ValueError) as error:
            self._show_error_dialog("Cannot Save Flyby", str(error))
            return

        self.system = candidate
        self._after_structural_edit(selected_body_id=result.id)
        self._show_information_dialog(
            "Flyby Ready",
            "Full N-body physics is selected so the encounter can perturb every body. "
            "The flyby will remain in the saved system after periapsis.",
        )

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
        self._after_structural_edit(
            selected_body_id=body.id,
            offer_horizons_refresh=True,
        )

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
        self._set_playing(False)
        self.simulation.increment_generation()

    def _after_structural_edit(
        self,
        selected_body_id: str | None = None,
        selected_group_id: str | None = None,
        offer_horizons_refresh: bool = False,
    ) -> None:
        self._set_playing(False)
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
        if offer_horizons_refresh and horizons_refresh_available(self.system):
            GLib.idle_add(self._offer_horizons_refresh_after_add)

    def _offer_horizons_refresh_after_add(self) -> bool:
        if not self.closed and horizons_refresh_available(self.system):
            self._request_horizons_refresh(after_add=True)
        return GLib.SOURCE_REMOVE

    def _sync_structural_controls(self) -> None:
        has_bodies = bool(self.system.bodies)
        editable = self._current_system_editable()
        available = not self.horizons_refresh_in_progress
        self.reset_last_save_action.set_enabled(editable and available)
        self.reset_bundled_action.set_enabled(not editable and available)
        self.reset_loaded_action.set_enabled(available)
        self.import_document_action.set_enabled(available)
        self.export_document_action.set_enabled(available)
        self.transform_frame_button.set_sensitive(editable and available)
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
        self.add_flyby_button.set_sensitive(
            editable
            and any(body.kind == "star" and body.parent_id is None for body in self.system.bodies)
        )
        self.search_horizons_button.set_sensitive(editable and horizons_import_available(self.system))
        refresh_available = horizons_refresh_available(self.system)
        self.horizons_refresh_button.set_sensitive(
            refresh_available and not self.horizons_refresh_in_progress
        )
        if refresh_available:
            self.horizons_refresh_button.set_tooltip_text(
                "Update all JPL Horizons bodies to the current instant"
            )
        else:
            self.horizons_refresh_button.set_tooltip_text(
                "Requires JPL Horizons bodies in a compatible Sol reference frame"
            )
        self.delete_selected_button.set_sensitive(editable and has_bodies)

    def _on_focus_clicked(self, _button) -> None:
        if self.focus_state is not None:
            self._exit_focus()
            target = f"group:{self.selected_group_id}" if self.selected_group_id is not None else None
            if target is None and self.system.bodies:
                target = self._body_focus_target(self.system.bodies[self.selected_index])
            self._configure_focus_button(target)
            self._update_title()
            self._refresh_canvas()
            return

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
        self.canvas.reset_all_views()
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._clear_dynamic_simulation_state()
        self._load_settings_editor()
        self._configure_focus_button(target)
        self._update_title()
        self._refresh_canvas()

    def _on_hierarchy_body_selected(self, _list_box: BodyHierarchyList, body_index: int) -> None:
        if body_index != self.selected_index or self.selected_group_id is not None:
            self._select_body(body_index)
            self._update_title()
            self._refresh_canvas()

    def _on_hierarchy_group_selected(self, _list_box: BodyHierarchyList, group_id: str) -> None:
        if group_id != self.selected_group_id:
            self._select_group(group_id)

    def _on_play_clicked(self, _button) -> None:
        self._set_playing(not self.playing)
        if self.playing:
            self._queue_simulation_job()

    def _on_step_back_clicked(self, _button) -> None:
        self._advance(-self._step_seconds())

    def _on_step_forward_clicked(self, _button) -> None:
        self._advance(self._step_seconds())

    def _on_zoom_out_clicked(self, _button) -> None:
        self._set_zoom_factor(self.zoom_factor / 1.5)

    def _on_reset_zoom_clicked(self, _button) -> None:
        self.canvas.reset_view()
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._sync_zoom_controls()

    def _on_render_mode_toggled(self, button, render_mode: str) -> None:
        if not button.get_active():
            return
        self.render_mode = render_mode
        self.canvas.set_render_mode(render_mode)
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._sync_zoom_controls()

    def _on_zoom_in_clicked(self, _button) -> None:
        self._set_zoom_factor(self.zoom_factor * 1.5)

    def _on_canvas_body_selected(self, _canvas: SolarSystemCanvas, body_index: int) -> None:
        self._select_body(body_index)
        self._update_title()
        self._refresh_canvas()

    def _on_canvas_group_selected(self, _canvas: SolarSystemCanvas, group_id: str) -> None:
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

    def _on_canvas_view_state_changed(self, _canvas: SolarSystemCanvas) -> None:
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._sync_zoom_controls()

    def _set_zoom_factor(self, zoom_factor: float) -> None:
        self.canvas.set_zoom_factor(zoom_factor)
        self.zoom_factor = self.canvas.get_zoom_factor()
        self._sync_zoom_controls()
        self._refresh_canvas()

    def _sync_zoom_controls(self) -> None:
        self.zoom_out_button.set_sensitive(self.zoom_factor > 1.0)
        self.reset_zoom_button.set_sensitive(not self.canvas.view_is_default())
        self.zoom_in_button.set_sensitive(self.zoom_factor < 64.0)

    def _on_reset_clicked(self, _button) -> None:
        self._resolve_dirty_before(self._reset_to_loaded_system)

    def _on_reset_last_save_action(self, *_args) -> None:
        if not self._current_system_editable():
            return
        self._resolve_dirty_before(self._reset_to_last_save)

    def _reset_to_last_save(self) -> None:
        try:
            saved = self.system_library.library.load(self.system.id)
        except Exception as error:
            self._show_error_dialog("Cannot Reset to Last Save", str(error))
            return
        self._load_system(saved)

    def _on_reset_bundled_action(self, *_args) -> None:
        if self._current_system_editable():
            return
        self._resolve_dirty_before(self._reset_to_bundled_preset)

    def _reset_to_bundled_preset(self) -> None:
        try:
            bundled = load_builtin_solar_system_by_id(self.system.id)
        except Exception as error:
            self._show_error_dialog("Cannot Reset Bundled Preset", str(error))
            return
        self._load_system(bundled)

    def _reset_to_loaded_system(self) -> None:
        selected_body_id = None
        if self.system.bodies:
            selected_body_id = self.system.bodies[self.selected_index].id
        self._set_playing(False)
        self._replace_system(
            self.loaded_system_snapshot,
            refresh_snapshot=False,
            selected_body_id=selected_body_id,
        )
        self._set_dirty(False)

    def _prepare_system_for_save(self, system: SolarSystem) -> None:
        self.simulation.materialize_to_bodies(system.bodies, system.groups)

    def _on_system_saved(self, system: SolarSystem) -> None:
        self.system = system
        self.loaded_system_snapshot = self._clone_system(self.system)
        self.simulation.rebase_diagnostics(self.system.bodies, self.system.groups)
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

    def _set_playing(self, playing: bool) -> None:
        self.playing = playing
        icon = (
            "media-playback-pause-symbolic"
            if playing
            else "media-playback-start-symbolic"
        )
        self.play_button.set_icon_name(icon)
        self._sync_playback_status()

    def _sync_playback_status(self) -> None:
        worker_active = self.simulation_future is not None
        active = self.playing or worker_active
        if self.playing:
            label = "Simulating"
        elif worker_active:
            label = "Finishing current step…"
        else:
            label = ""
        self.playback_status_label.set_label(label)
        self.playback_spinner.set_spinning(active)
        self.playback_status_box.set_visible(active)

    def _tick(self) -> bool:
        if self.playing:
            self._queue_simulation_job()
        return True

    def _advance(self, dt_s: float) -> None:
        settings = self._effective_settings()
        job = self._simulation_job(dt_s)
        result = playback.run_simulation_job(job)
        if self.simulation.apply_result(
            result,
            self.system.bodies,
            self.system.groups,
            settings,
        ):
            self._after_simulation_applied()

    def _queue_simulation_job(self) -> None:
        if self.closed or self.simulation_future is not None:
            return

        job = self._simulation_job(self._step_seconds())
        self.simulation_future = self.simulation_executor.submit(playback.run_simulation_job, job)
        self._sync_playback_status()
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
            self.analysis_frame,
            self.system,
        )

    def _finish_simulation_job(self, future: Future) -> bool:
        if future is self.simulation_future:
            self.simulation_future = None
            self._sync_playback_status()

        if self.closed:
            return False

        try:
            result = future.result()
        except Exception:
            traceback.print_exc()
            self._set_playing(False)
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
        mode = "1PN" if settings.physics_mode == "post_newtonian" else "Newtonian"
        integrator = "RK4" if settings.integrator == "rk4" else "Verlet"
        self.window_title.set_subtitle(
            f"{self.system.epoch} - {policy}, {mode}/{integrator}, "
            f"max step {max_step_days:,.2f} days"
            + (f" - frame: {self.analysis_frame.label}" if self.analysis_frame.active else "")
        )

    def _update_time_label(self) -> None:
        self.time_label.set_label(f"Simulation time: {format_elapsed_time(self.simulation.state.elapsed_s)}")

    def _refresh_canvas(self) -> None:
        self.canvas.set_scene(self._canvas_scene())

    def _canvas_scene(self) -> CanvasScene:
        settings = self._effective_settings()
        active_indices = self._active_body_indices()
        using_hybrid_focus = self._using_hybrid_focus()
        canvas_bodies = self.system.bodies
        orbit_guides = configured_orbit_guides(self.system.bodies, self.system.groups)
        overview_positions = self._overview_positions()
        selected_group_center = (
            self._group_center(self.selected_group_id)
            if settings.view_mode == "follow_selected" and self.selected_group_id is not None
            else None
        )
        if self.analysis_frame.active:
            materialized = self.simulation.materialized_state(
                self.system.bodies,
                self.system.groups,
            )
            try:
                kinematics = frame_kinematics(
                    self.system,
                    materialized,
                    self.analysis_frame,
                    physics_mode=settings.physics_mode,
                    include_acceleration=False,
                )
                transformed = transform_state(materialized, kinematics)
                canvas_bodies = [
                    replace(
                        body,
                        position_m=transformed.positions_m[index].tolist(),
                        velocity_mps=transformed.velocities_mps[index].tolist(),
                    )
                    for index, body in enumerate(self.system.bodies)
                ]
                orbit_guides = [
                    replace(
                        guide,
                        points_m=tuple(transform_points(guide.points_m, kinematics)),
                    )
                    for guide in orbit_guides
                ]
                overview_positions = transform_points(overview_positions, kinematics)
                if selected_group_center is not None:
                    selected_group_center = transform_points(
                        [selected_group_center], kinematics
                    )[0]
            except ModelError:
                canvas_bodies = self.system.bodies
                orbit_guides = configured_orbit_guides(
                    self.system.bodies,
                    self.system.groups,
                )
                overview_positions = self._overview_positions()
        trail_reference_position = (
            focused_trail_reference_position(
                self.system.bodies,
                self.system.groups,
                self.focus_state.target,
            )
            if (
                self.focus_state is not None
                and settings.trail_frame == "focused_parent"
                and not self.analysis_frame.active
            )
            else None
        )
        inset_entities, inset_positions, inset_targets, focused_inset_id = self._inset_overview_data()
        return CanvasScene(
            bodies=canvas_bodies,
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
            trail_reference_position=trail_reference_position,
            orbit_guides=orbit_guides,
            orbit_visibility=settings.orbit_visibility,
            trail_visibility=settings.trail_visibility,
            path_style=settings.path_style,
            trails=list(self.simulation.trails),
            overview_entities=self._overview_entities(),
            overview_positions=overview_positions,
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

    def _group_center(self, group_id: str) -> tuple[float, float, float] | None:
        return hierarchy.group_center(self.system.bodies, self.system.groups, group_id)

    def _distance_factor(self) -> float:
        return unit_factor(DISTANCE_UNITS, self.system.settings.distance_unit)

    def _configure_position_spins(self) -> None:
        unit = self.system.settings.distance_unit
        factor = self._distance_factor()
        self.body_inspector.configure_position_spins(self.system.bodies, unit, factor)
