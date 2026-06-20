# sidebar.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sidebar widgets and panel controllers."""

from __future__ import annotations

import math
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GObject, Gtk

from . import hierarchy
from .constants import AU, DAY
from .models import Body, DataSource, ModelError, OrbitData, SolarSystem, SystemGroup, SystemSettings
from .scales import (
    ACCURACY_LABELS,
    DISTANCE_UNITS,
    TIME_UNITS,
    VIEW_MODE_LABELS,
    SIMULATION_SCOPE_LABELS,
    time_unit_for_seconds,
    unit_factor,
    unit_index,
)


class BodyHierarchyList(Gtk.ListBox):
    __gtype_name__ = "BodyHierarchyList"

    __gsignals__ = {
        "body-selected": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "group-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bodies: list[Body] = []
        self.groups: list[SystemGroup] = []
        self.rows: list[tuple[str, str | int]] = []
        self.relationship_labels: dict[int, Gtk.Label] = {}
        self._selecting = False
        self.connect("row-selected", self._on_row_selected)

    def set_system(self, bodies: list[Body], groups: list[SystemGroup]) -> None:
        self.bodies = bodies
        self.groups = groups
        self._populate()

    def select_body(self, body_index: int) -> None:
        row_index = next(
            (
                list_index
                for list_index, (row_type, row_id) in enumerate(self.rows)
                if row_type == "body" and int(row_id) == body_index
            ),
            body_index,
        )
        self._select_row_at(row_index)

    def select_group(self, group_id: str) -> None:
        row_index = next(
            (
                list_index
                for list_index, (row_type, row_id) in enumerate(self.rows)
                if row_type == "group" and str(row_id) == group_id
            ),
            None,
        )
        if row_index is not None:
            self._select_row_at(row_index)

    def refresh_relationship_labels(self) -> None:
        for body_index, label in self.relationship_labels.items():
            if body_index < len(self.bodies):
                label.set_label(hierarchy.body_relationship_label(self.bodies, self.bodies[body_index]))

    def _populate(self) -> None:
        while child := self.get_first_child():
            self.remove(child)
        self.relationship_labels = {}
        self.rows = hierarchy.body_list_rows(self.bodies, self.groups)
        for row_type, row_id in self.rows:
            if row_type == "group":
                self._append_group_row(str(row_id))
            else:
                body_index = int(row_id)
                self._append_body_row(body_index, hierarchy.body_row_depth(self.bodies, self.groups, body_index))

    def _append_group_row(self, group_id: str) -> None:
        group = hierarchy.group_by_id(self.groups, group_id)
        if group is None:
            return
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10 + hierarchy.group_depth(self.groups, group) * 18)
        box.set_margin_end(10)
        name = Gtk.Label(label=group.name, xalign=0)
        name.set_hexpand(True)
        name.add_css_class("heading")
        kind = Gtk.Label(label=hierarchy.group_label(self.bodies, self.groups, group))
        kind.add_css_class("dim-label")
        box.append(name)
        box.append(kind)
        row.set_child(box)
        self.append(row)

    def _append_body_row(self, body_index: int, depth: int) -> None:
        body = self.bodies[body_index]
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
        kind = Gtk.Label(label=hierarchy.body_relationship_label(self.bodies, body))
        kind.add_css_class("dim-label")
        self.relationship_labels[body_index] = kind
        box.append(swatch)
        box.append(name)
        box.append(kind)
        row.set_child(box)
        self.append(row)

    def _select_row_at(self, row_index: int) -> None:
        row = self.get_row_at_index(row_index)
        if row is None:
            return
        self._selecting = True
        try:
            self.select_row(row)
        finally:
            self._selecting = False

    def _on_row_selected(self, _list_box, row) -> None:
        if self._selecting or row is None:
            return
        row_index = row.get_index()
        if row_index < 0 or row_index >= len(self.rows):
            return
        row_type, row_id = self.rows[row_index]
        if row_type == "group":
            self.emit("group-selected", str(row_id))
        else:
            self.emit("body-selected", int(row_id))

    def _draw_swatch(self, _area, cr, width: int, height: int, color: str) -> None:
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            rgba.parse("#ffffff")
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
        cr.arc(width / 2.0, height / 2.0, min(width, height) / 2.5, 0.0, math.tau)
        cr.fill()


class SystemPropertiesPanel(GObject.GObject):
    __gsignals__ = {
        "system-name-edited": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "time-step-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "accuracy-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "view-mode-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "simulation-scope-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "distance-unit-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(
        self,
        system_name_entry,
        delete_system_button,
        speed_spin,
        time_unit_dropdown,
        accuracy_dropdown,
        view_mode_dropdown,
        simulation_scope_dropdown,
        distance_unit_dropdown,
    ):
        super().__init__()
        self.system_name_entry = system_name_entry
        self.delete_system_button = delete_system_button
        self.speed_spin = speed_spin
        self.time_unit_dropdown = time_unit_dropdown
        self.accuracy_dropdown = accuracy_dropdown
        self.view_mode_dropdown = view_mode_dropdown
        self.simulation_scope_dropdown = simulation_scope_dropdown
        self.distance_unit_dropdown = distance_unit_dropdown
        self.editing = False

        self._setup_dropdown(self.time_unit_dropdown, TIME_UNITS)
        self._setup_dropdown(self.accuracy_dropdown, ACCURACY_LABELS)
        self._setup_dropdown(self.view_mode_dropdown, VIEW_MODE_LABELS)
        self._setup_dropdown(self.simulation_scope_dropdown, SIMULATION_SCOPE_LABELS)
        self._setup_dropdown(self.distance_unit_dropdown, DISTANCE_UNITS)

        self.system_name_entry.connect("activate", self._on_system_name_edit)
        self.system_name_entry.connect("notify::has-focus", self._on_system_name_focus_changed)
        self.delete_system_button.connect("clicked", lambda *_args: self.emit("delete-requested"))
        self.speed_spin.connect("value-changed", self._on_time_step_changed)
        self.time_unit_dropdown.connect("notify::selected", self._on_time_step_changed)
        self.accuracy_dropdown.connect("notify::selected", self._on_accuracy_changed)
        self.view_mode_dropdown.connect("notify::selected", self._on_view_mode_changed)
        self.simulation_scope_dropdown.connect("notify::selected", self._on_simulation_scope_changed)
        self.distance_unit_dropdown.connect("notify::selected", self._on_distance_unit_changed)

    def load_system(self, system: SolarSystem, editable: bool) -> None:
        self.editing = True
        try:
            self.system_name_entry.set_text(system.name)
            self.system_name_entry.set_sensitive(editable)
            self.delete_system_button.set_sensitive(editable)
        finally:
            self.editing = False

    def load_settings(self, settings: SystemSettings) -> None:
        self.editing = True
        try:
            time_unit = time_unit_for_seconds(settings.visible_step_s)
            time_factor = unit_factor(TIME_UNITS, time_unit)
            self.speed_spin.set_value(settings.visible_step_s / time_factor)
            self.time_unit_dropdown.set_selected(unit_index(TIME_UNITS, time_unit))
            self.accuracy_dropdown.set_selected(unit_index(ACCURACY_LABELS, settings.accuracy_profile))
            self.view_mode_dropdown.set_selected(unit_index(VIEW_MODE_LABELS, settings.view_mode))
            self.simulation_scope_dropdown.set_selected(unit_index(SIMULATION_SCOPE_LABELS, settings.simulation_scope))
            self.distance_unit_dropdown.set_selected(unit_index(DISTANCE_UNITS, settings.distance_unit))
        finally:
            self.editing = False

    def step_seconds(self) -> float:
        selected = self.time_unit_dropdown.get_selected()
        unit = TIME_UNITS[selected][1] if selected < len(TIME_UNITS) else "days"
        return self.speed_spin.get_value() * unit_factor(TIME_UNITS, unit)

    def _setup_dropdown(self, dropdown, items) -> None:
        dropdown.set_model(Gtk.StringList.new([item[0] for item in items]))

    def _on_system_name_focus_changed(self, entry, _param) -> None:
        if not entry.has_focus():
            self._on_system_name_edit()

    def _on_system_name_edit(self, *_args) -> None:
        if not self.editing:
            self.emit("system-name-edited", self.system_name_entry.get_text().strip())

    def _on_time_step_changed(self, *_args) -> None:
        if not self.editing:
            self.emit("time-step-changed", self.step_seconds())

    def _on_accuracy_changed(self, dropdown, _param) -> None:
        if not self.editing:
            selected = dropdown.get_selected()
            if selected < len(ACCURACY_LABELS):
                self.emit("accuracy-changed", ACCURACY_LABELS[selected][1])

    def _on_view_mode_changed(self, dropdown, _param) -> None:
        if not self.editing:
            selected = dropdown.get_selected()
            if selected < len(VIEW_MODE_LABELS):
                self.emit("view-mode-changed", VIEW_MODE_LABELS[selected][1])

    def _on_simulation_scope_changed(self, dropdown, _param) -> None:
        if not self.editing:
            selected = dropdown.get_selected()
            if selected < len(SIMULATION_SCOPE_LABELS):
                self.emit("simulation-scope-changed", SIMULATION_SCOPE_LABELS[selected][1])

    def _on_distance_unit_changed(self, dropdown, _param) -> None:
        if not self.editing:
            selected = dropdown.get_selected()
            if selected < len(DISTANCE_UNITS):
                self.emit("distance-unit-changed", DISTANCE_UNITS[selected][1])


class BodyInspectorPanel(GObject.GObject):
    __gsignals__ = {
        "body-edited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "focus-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "generate-body-orbit": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "generate-group-orbit": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "generate-binary-orbit": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        selected_name_label,
        focus_button,
        selected_distance_list,
        orbit_expander,
        orbit_axis_spin,
        orbit_period_spin,
        orbit_eccentricity_spin,
        orbit_inclination_spin,
        orbit_node_spin,
        orbit_periapsis_spin,
        orbit_anomaly_spin,
        orbit_epoch_entry,
        orbit_source_entry,
        orbit_source_url_entry,
        orbit_notes_entry,
        orbit_target_label,
        orbit_target_dropdown,
        generate_orbit_button,
        generate_group_orbit_button,
        generate_binary_orbit_button,
        orbit_status_label,
        mass_entry,
        x_label,
        x_spin,
        y_label,
        y_spin,
        vx_spin,
        vy_spin,
    ):
        super().__init__()
        self.selected_name_label = selected_name_label
        self.focus_button = focus_button
        self.selected_distance_list = selected_distance_list
        self.orbit_expander = orbit_expander
        self.orbit_axis_spin = orbit_axis_spin
        self.orbit_period_spin = orbit_period_spin
        self.orbit_eccentricity_spin = orbit_eccentricity_spin
        self.orbit_inclination_spin = orbit_inclination_spin
        self.orbit_node_spin = orbit_node_spin
        self.orbit_periapsis_spin = orbit_periapsis_spin
        self.orbit_anomaly_spin = orbit_anomaly_spin
        self.orbit_epoch_entry = orbit_epoch_entry
        self.orbit_source_entry = orbit_source_entry
        self.orbit_source_url_entry = orbit_source_url_entry
        self.orbit_notes_entry = orbit_notes_entry
        self.orbit_target_label = orbit_target_label
        self.orbit_target_dropdown = orbit_target_dropdown
        self.generate_orbit_button = generate_orbit_button
        self.generate_group_orbit_button = generate_group_orbit_button
        self.generate_binary_orbit_button = generate_binary_orbit_button
        self.orbit_status_label = orbit_status_label
        self.mass_entry = mass_entry
        self.x_label = x_label
        self.x_spin = x_spin
        self.y_label = y_label
        self.y_spin = y_spin
        self.vx_spin = vx_spin
        self.vy_spin = vy_spin
        self.orbit_target_options: list[tuple[str, str]] = []
        self.editing = False

        self.focus_button.connect("clicked", lambda *_args: self.emit("focus-requested"))
        self.generate_orbit_button.connect("clicked", lambda *_args: self.emit("generate-body-orbit"))
        self.generate_group_orbit_button.connect("clicked", lambda *_args: self.emit("generate-group-orbit"))
        self.generate_binary_orbit_button.connect("clicked", lambda *_args: self.emit("generate-binary-orbit"))
        self.mass_entry.connect("activate", self._on_body_edit)
        self.mass_entry.connect("notify::has-focus", self._on_mass_focus_changed)
        for spin in (self.x_spin, self.y_spin, self.vx_spin, self.vy_spin):
            spin.connect("value-changed", self._on_body_edit)

    def set_editing(self, editing: bool) -> None:
        self.editing = editing

    def set_selected_name(self, name: str) -> None:
        self.selected_name_label.set_label(name)

    def configure_focus_button(self, visible: bool, focused: bool) -> None:
        self.focus_button.set_visible(visible)
        self.focus_button.set_tooltip_text(
            "Exit focused view" if focused else "Focus and fit this system"
        )

    def set_body_editor_sensitive(self, sensitive: bool, orbit_sensitive: bool) -> None:
        for widget in (self.mass_entry, self.x_spin, self.y_spin, self.vx_spin, self.vy_spin):
            widget.set_sensitive(sensitive)
        self.set_orbit_editor_sensitive(orbit_sensitive)

    def orbit_editor_widgets(self):
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

    def set_orbit_editor_sensitive(self, sensitive: bool) -> None:
        for widget in self.orbit_editor_widgets():
            widget.set_sensitive(sensitive)

    def set_orbit_expander_sensitive(self, sensitive: bool) -> None:
        self.orbit_expander.set_sensitive(sensitive)

    def set_generate_body_orbit_sensitive(self, sensitive: bool) -> None:
        self.generate_orbit_button.set_sensitive(sensitive)

    def configure_binary_orbit_button(self, visible: bool, sensitive: bool) -> None:
        self.generate_binary_orbit_button.set_visible(visible)
        self.generate_binary_orbit_button.set_sensitive(sensitive)

    def show_group_orbit_controls(self, visible: bool) -> None:
        for widget in (
            self.orbit_target_label,
            self.orbit_target_dropdown,
            self.generate_group_orbit_button,
            self.generate_binary_orbit_button,
        ):
            widget.set_visible(visible)

    def load_orbit_values(self, orbit: OrbitData | None, source: DataSource | None, default_axis_au: float, epoch: str) -> None:
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
        self.orbit_epoch_entry.set_text(orbit.epoch if orbit and orbit.epoch else epoch)
        self.orbit_source_entry.set_text(source.source_name if source else "")
        self.orbit_source_url_entry.set_text(source.source_url if source else "")
        self.orbit_notes_entry.set_text(orbit.approximation_notes if orbit else "")

    def set_orbit_status(self, message: str) -> None:
        self.orbit_status_label.set_label(message)

    def set_orbit_target_options(self, options: list[tuple[str, str, str]], selected: int) -> None:
        self.orbit_target_options = [(target_type, target_id) for target_type, target_id, _label in options]
        self.orbit_target_dropdown.set_model(Gtk.StringList.new([label for _target_type, _target_id, label in options]))
        if self.orbit_target_options:
            self.orbit_target_dropdown.set_selected(selected)

    def selected_orbit_target(self) -> tuple[str, str] | None:
        selected = self.orbit_target_dropdown.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION or selected >= len(self.orbit_target_options):
            return None
        return self.orbit_target_options[selected]

    def set_group_orbit_target_sensitive(self, has_target: bool, binary_sensitive: bool) -> None:
        self.orbit_target_dropdown.set_sensitive(has_target)
        self.generate_group_orbit_button.set_sensitive(has_target)
        self.generate_binary_orbit_button.set_sensitive(binary_sensitive)

    def populate_distance_list(self, rows: list[tuple[str, str]]) -> None:
        while child := self.selected_distance_list.get_first_child():
            self.selected_distance_list.remove(child)
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

    def hide_distance_list(self) -> None:
        while child := self.selected_distance_list.get_first_child():
            self.selected_distance_list.remove(child)
        self.selected_distance_list.set_visible(False)

    def set_body_values(self, body: Body, distance_factor: float) -> None:
        self.mass_entry.set_text(f"{body.mass_kg:.12g}")
        self.x_spin.set_value(body.position_m[0] / distance_factor)
        self.y_spin.set_value(body.position_m[1] / distance_factor)
        self.vx_spin.set_value(body.velocity_mps[0])
        self.vy_spin.set_value(body.velocity_mps[1])

    def clear_body_values(self) -> None:
        self.mass_entry.set_text("")
        self.x_spin.set_value(0.0)
        self.y_spin.set_value(0.0)
        self.vx_spin.set_value(0.0)
        self.vy_spin.set_value(0.0)

    def configure_position_spins(self, bodies: list[Body], unit: str, factor: float) -> None:
        self.x_label.set_label(f"X ({unit})")
        self.y_label.set_label(f"Y ({unit})")
        max_distance_m = max(
            max(abs(body.position_m[0]), abs(body.position_m[1]))
            for body in bodies
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

    def edited_body_values(self, distance_factor: float) -> tuple[float, float, float, float, float] | None:
        try:
            mass = float(self.mass_entry.get_text())
        except ValueError:
            return None
        if mass <= 0.0:
            return None
        return (
            mass,
            self.x_spin.get_value() * distance_factor,
            self.y_spin.get_value() * distance_factor,
            self.vx_spin.get_value(),
            self.vy_spin.get_value(),
        )

    def orbit_from_editor(self, epoch: str) -> OrbitData:
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
            epoch=self.orbit_epoch_entry.get_text().strip() or epoch,
            reference_plane="app-local XY",
            approximation_notes=notes,
        )
        orbit.validate()
        return orbit

    def data_source_from_orbit_editor(self) -> DataSource | None:
        source = DataSource(
            source_name=self.orbit_source_entry.get_text().strip(),
            source_url=self.orbit_source_url_entry.get_text().strip(),
        )
        return source if source.to_dict() else None

    def _on_mass_focus_changed(self, entry, _param) -> None:
        if not entry.has_focus():
            self._on_body_edit()

    def _on_body_edit(self, *_args) -> None:
        if not self.editing:
            self.emit("body-edited")
