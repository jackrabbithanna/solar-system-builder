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

from .constants import AU, DAY
from .models import Body, SolarSystem
from .physics import SimulationState, advance_with_samples
from .presets import load_builtin_solar_system, load_builtin_solar_systems
from .scales import (
    ACCURACY_LABELS,
    DISTANCE_UNITS,
    TIME_UNITS,
    VIEW_MODE_LABELS,
    derived_max_step_s,
    format_elapsed_time,
    recommended_trail_sample_interval_s,
    time_unit_for_seconds,
    unit_factor,
    unit_index,
)
from .storage import Library

TRAIL_POINT_LIMIT = 2000


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
    time_label = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    system_name_entry = Gtk.Template.Child()
    delete_system_button = Gtk.Template.Child()
    selected_name_label = Gtk.Template.Child()
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
        self.body_list_indices: list[int] = []
        self.playing = False
        self.editing = False
        self.updating_system_dropdown = False
        self.closed = False
        self.simulation_generation = 0
        self.simulation_future: Future | None = None
        self.simulation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation")
        self.trails: list[list[tuple[float, float]]] = [[] for _ in self.system.bodies]
        self.last_trail_sample_elapsed_s = self.state.elapsed_s
        self.zoom_factor = 1.0
        self.timer_id = GLib.timeout_add(33, self._tick)

        self._setup_dropdown(self.time_unit_dropdown, TIME_UNITS)
        self._setup_dropdown(self.accuracy_dropdown, ACCURACY_LABELS)
        self._setup_dropdown(self.view_mode_dropdown, VIEW_MODE_LABELS)
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
        self.system_dropdown.connect("notify::selected", self._on_system_selected)
        self.body_list.connect("row-selected", self._on_body_selected)
        self.speed_spin.connect("value-changed", self._on_time_step_changed)
        self.time_unit_dropdown.connect("notify::selected", self._on_time_step_changed)
        self.accuracy_dropdown.connect("notify::selected", self._on_accuracy_changed)
        self.view_mode_dropdown.connect("notify::selected", self._on_view_mode_changed)
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
        self.trails = [[] for _ in self.system.bodies]
        self.last_trail_sample_elapsed_s = self.state.elapsed_s
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
        self.body_list_indices = self._body_list_order()
        for body_index in self.body_list_indices:
            body = self.system.bodies[body_index]
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(10 + self._body_depth(body) * 18)
            box.set_margin_end(10)
            swatch = Gtk.DrawingArea()
            swatch.set_content_width(14)
            swatch.set_content_height(14)
            swatch.set_draw_func(self._draw_swatch, body.color)
            name = Gtk.Label(label=body.name, xalign=0)
            name.set_hexpand(True)
            kind = Gtk.Label(label=self._body_relationship_label(body))
            kind.add_css_class("dim-label")
            box.append(swatch)
            box.append(name)
            box.append(kind)
            row.set_child(box)
            self.body_list.append(row)

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

        def sort_key(parent_index: int | None, child_index: int) -> tuple[int, float, str]:
            body = self.system.bodies[child_index]
            parent = self.system.bodies[parent_index] if parent_index is not None else None
            distance_m = math.dist(body.position_m, parent.position_m) if parent is not None else 0.0
            root_rank = 0 if body.kind == "star" else 1
            return (root_rank, distance_m, body.name.casefold())

        ordered: list[int] = []

        def append_children(parent_id: str | None) -> None:
            parent_index = body_index_by_id.get(parent_id) if parent_id is not None else None
            for child_index in sorted(
                children_by_parent_id.get(parent_id, []),
                key=lambda index: sort_key(parent_index, index),
            ):
                ordered.append(child_index)
                append_children(self.system.bodies[child_index].id)

        append_children(None)
        return ordered

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
            return body.kind
        parent = next((item for item in self.system.bodies if item.id == body.parent_id), None)
        if parent is None:
            return body.kind
        return f"orbits {parent.name}"

    def _select_body(self, index: int) -> None:
        if not self.system.bodies:
            return
        self.selected_index = max(0, min(index, len(self.system.bodies) - 1))
        row_index = next(
            (
                list_index
                for list_index, body_index in enumerate(self.body_list_indices)
                if body_index == self.selected_index
            ),
            self.selected_index,
        )
        row = self.body_list.get_row_at_index(row_index)
        if row:
            self.body_list.select_row(row)
        self._load_body_editor(self.system.bodies[self.selected_index])

    def _load_body_editor(self, body: Body) -> None:
        self.editing = True
        distance_factor = self._distance_factor()
        self.selected_name_label.set_label(body.name)
        self.mass_entry.set_text(f"{body.mass_kg:.12g}")
        self._configure_position_spins()
        self.x_spin.set_value(body.position_m[0] / distance_factor)
        self.y_spin.set_value(body.position_m[1] / distance_factor)
        self.vx_spin.set_value(body.velocity_mps[0])
        self.vy_spin.set_value(body.velocity_mps[1])
        self.editing = False

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
        self.trails = [[] for _ in self.system.bodies]
        self.last_trail_sample_elapsed_s = self.state.elapsed_s
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
            self.canvas.queue_draw()

    def _on_distance_unit_changed(self, dropdown, _param) -> None:
        if self.editing:
            return
        selected = dropdown.get_selected()
        if selected < len(DISTANCE_UNITS):
            self.system.settings.distance_unit = DISTANCE_UNITS[selected][1]
            if self.system.bodies:
                self._load_body_editor(self.system.bodies[self.selected_index])

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
        if row_index < 0 or row_index >= len(self.body_list_indices):
            return
        body_index = self.body_list_indices[row_index]
        if body_index != self.selected_index:
            self.selected_index = body_index
            self._load_body_editor(self.system.bodies[body_index])
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
        if body_index is None:
            return False
        tooltip.set_text(self.system.bodies[body_index].name)
        return True

    def _on_canvas_pressed(self, _gesture, _n_press: int, x: float, y: float) -> None:
        body_index = self._body_index_at_canvas_point(x, y)
        if body_index is None:
            return
        self._select_body(body_index)
        self.canvas.queue_draw()

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
        state, position_samples = advance_with_samples(
            self.state,
            dt_s,
            "post_newtonian",
            self._max_step_seconds(),
        )
        self._apply_simulation_state(state, position_samples, self.state.elapsed_s)

    def _queue_simulation_job(self) -> None:
        if self.closed or self.simulation_future is not None:
            return

        state = self.state.copy()
        dt_s = self._step_seconds()
        generation = self.simulation_generation
        max_step_s = self._max_step_seconds()
        self.simulation_future = self.simulation_executor.submit(
            advance_with_samples,
            state,
            dt_s,
            "post_newtonian",
            max_step_s,
        )
        self.simulation_future.add_done_callback(
            lambda future: GLib.idle_add(self._finish_simulation_job, future, generation, state.elapsed_s)
        )

    def _finish_simulation_job(self, future: Future, generation: int, start_elapsed_s: float) -> bool:
        if future is self.simulation_future:
            self.simulation_future = None

        if self.closed:
            return False

        try:
            state, position_samples = future.result()
        except Exception:
            traceback.print_exc()
            self.playing = False
            self.play_button.set_icon_name("media-playback-start-symbolic")
            return False

        if generation == self.simulation_generation:
            self.state = state
            self._apply_simulation_state(state, position_samples, start_elapsed_s)

        if self.playing:
            self._queue_simulation_job()

        return False

    def _apply_simulation_state(self, state: SimulationState, position_samples, start_elapsed_s: float) -> None:
        self.state = state
        self.state.apply_to_bodies(self.system.bodies)
        self._append_trails(position_samples, start_elapsed_s, state.elapsed_s)
        self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_time_label()
        self.canvas.queue_draw()

    def _step_seconds(self) -> float:
        selected = self.time_unit_dropdown.get_selected()
        unit = TIME_UNITS[selected][1] if selected < len(TIME_UNITS) else "days"
        return self.speed_spin.get_value() * unit_factor(TIME_UNITS, unit)

    def _max_step_seconds(self) -> float:
        return derived_max_step_s(self.system.bodies, self.system.settings.accuracy_profile)

    def _append_trails(self, position_samples, start_elapsed_s: float, end_elapsed_s: float) -> None:
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
        for index, body in enumerate(self.system.bodies):
            if not body.trail_enabled:
                continue
            trail = self.trails[index]
            for positions_m in selected_samples:
                trail.append((float(positions_m[index][0]), float(positions_m[index][1])))
            if len(trail) > TRAIL_POINT_LIMIT:
                del trail[: len(trail) - TRAIL_POINT_LIMIT]

    def _update_title(self) -> None:
        self.window_title.set_title(self.system.name)
        max_step_days = self._max_step_seconds() / DAY
        self.window_title.set_subtitle(f"{self.system.epoch} - max step {max_step_days:,.2f} days")

    def _update_time_label(self) -> None:
        self.time_label.set_label(f"Simulation time: {format_elapsed_time(self.state.elapsed_s)}")

    def _draw(self, _area, cr, width: int, height: int) -> None:
        cr.set_source_rgb(0.02, 0.025, 0.032)
        cr.paint()
        if not self.system.bodies:
            return

        center_x_m, center_y_m = self._view_center()
        scale = self._canvas_scale(width, height, center_x_m, center_y_m)
        origin_x = width / 2.0
        origin_y = height / 2.0

        cr.set_line_width(1.0)
        for index, trail in enumerate(self.trails):
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

    def _canvas_scale(self, width: int, height: int, center_x_m: float, center_y_m: float) -> float:
        max_distance = max(
            self._view_distance(body.position_m[0], body.position_m[1], center_x_m, center_y_m)
            for body in self.system.bodies
        )
        return min(width, height) * 0.45 / max(max_distance, AU) * self.zoom_factor

    def _body_index_at_canvas_point(self, pointer_x: float, pointer_y: float) -> int | None:
        if not self.system.bodies:
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

        for index, body in enumerate(self.system.bodies):
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
        if self.system.settings.view_mode == "follow_selected":
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
