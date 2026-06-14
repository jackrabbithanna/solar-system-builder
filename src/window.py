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
from .physics import SimulationState, advance
from .presets import load_builtin_solar_system, load_builtin_solar_systems
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
    time_label = Gtk.Template.Child()
    window_title = Gtk.Template.Child()
    selected_name_label = Gtk.Template.Child()
    mass_entry = Gtk.Template.Child()
    x_spin = Gtk.Template.Child()
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
        self.closed = False
        self.simulation_generation = 0
        self.simulation_future: Future | None = None
        self.simulation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="simulation")
        self.trails: list[list[tuple[float, float]]] = [[] for _ in self.system.bodies]
        self.zoom_factor = 1.0
        self.timer_id = GLib.timeout_add(33, self._tick)

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

        self.mass_entry.connect("activate", self._on_body_edit)
        self.mass_entry.connect("notify::has-focus", self._on_mass_focus_changed)
        for spin in (self.x_spin, self.y_spin, self.vx_spin, self.vy_spin):
            spin.connect("value-changed", self._on_body_edit)

        self._reload_system_list()
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
        names = [system.name for system in self.systems]
        self.system_dropdown.set_model(Gtk.StringList.new(names))
        active = next((index for index, system in enumerate(self.systems) if system.id == self.system.id), 0)
        self.system_dropdown.set_selected(active)

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
        self._populate_body_list()
        self._select_body(self.selected_index)
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
            box.set_margin_start(10)
            box.set_margin_end(10)
            swatch = Gtk.DrawingArea()
            swatch.set_content_width(14)
            swatch.set_content_height(14)
            swatch.set_draw_func(self._draw_swatch, body.color)
            name = Gtk.Label(label=body.name, xalign=0)
            name.set_hexpand(True)
            kind = Gtk.Label(label=body.kind)
            kind.add_css_class("dim-label")
            box.append(swatch)
            box.append(name)
            box.append(kind)
            row.set_child(box)
            self.body_list.append(row)

    def _body_list_order(self) -> list[int]:
        if not self.system.bodies:
            return []
        sun = next(
            (
                body
                for body in self.system.bodies
                if body.id == "sun" or body.kind == "star"
            ),
            self.system.bodies[0],
        )

        def sort_key(index: int) -> tuple[float, str]:
            body = self.system.bodies[index]
            distance_m = math.dist(body.position_m, sun.position_m)
            return (distance_m, body.name.casefold())

        return sorted(range(len(self.system.bodies)), key=sort_key)

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
        self.selected_name_label.set_label(body.name)
        self.mass_entry.set_text(f"{body.mass_kg:.12g}")
        self.x_spin.set_value(body.position_m[0] / AU)
        self.y_spin.set_value(body.position_m[1] / AU)
        self.vx_spin.set_value(body.velocity_mps[0])
        self.vy_spin.set_value(body.velocity_mps[1])
        self.editing = False

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
        body.mass_kg = mass
        body.position_m[0] = self.x_spin.get_value() * AU
        body.position_m[1] = self.y_spin.get_value() * AU
        body.velocity_mps[0] = self.vx_spin.get_value()
        body.velocity_mps[1] = self.vy_spin.get_value()
        self.state = SimulationState.from_bodies(self.system.bodies)
        self.simulation_generation += 1
        self.trails = [[] for _ in self.system.bodies]
        self._populate_body_list()
        self._select_body(self._body_index_for_id(selected_body_id))
        self._update_title()
        self.canvas.queue_draw()

    def _on_mass_focus_changed(self, entry, _param) -> None:
        if not entry.has_focus():
            self._on_body_edit()

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
        self.state = advance(self.state, dt_s, "post_newtonian")
        self._apply_simulation_state(self.state)

    def _queue_simulation_job(self) -> None:
        if self.closed or self.simulation_future is not None:
            return

        state = self.state.copy()
        dt_s = self._step_seconds()
        generation = self.simulation_generation
        self.simulation_future = self.simulation_executor.submit(advance, state, dt_s, "post_newtonian")
        self.simulation_future.add_done_callback(
            lambda future: GLib.idle_add(self._finish_simulation_job, future, generation)
        )

    def _finish_simulation_job(self, future: Future, generation: int) -> bool:
        if future is self.simulation_future:
            self.simulation_future = None

        if self.closed:
            return False

        try:
            state = future.result()
        except Exception:
            traceback.print_exc()
            self.playing = False
            self.play_button.set_icon_name("media-playback-start-symbolic")
            return False

        if generation == self.simulation_generation:
            self.state = state
            self._apply_simulation_state(state)

        if self.playing:
            self._queue_simulation_job()

        return False

    def _apply_simulation_state(self, state: SimulationState) -> None:
        self.state = state
        self.state.apply_to_bodies(self.system.bodies)
        self._append_trails()
        self._load_body_editor(self.system.bodies[self.selected_index])
        self._update_time_label()
        self.canvas.queue_draw()

    def _step_seconds(self) -> float:
        return self.speed_spin.get_value() * DAY

    def _append_trails(self) -> None:
        for index, body in enumerate(self.system.bodies):
            if not body.trail_enabled:
                continue
            trail = self.trails[index]
            trail.append((body.position_m[0], body.position_m[1]))
            if len(trail) > 500:
                del trail[0]

    def _update_title(self) -> None:
        self.window_title.set_title(self.system.name)
        self.window_title.set_subtitle(self.system.epoch)

    def _update_time_label(self) -> None:
        days = self.state.elapsed_s / DAY
        self.time_label.set_label(f"Simulation time: {days:,.2f} days")

    def _draw(self, _area, cr, width: int, height: int) -> None:
        cr.set_source_rgb(0.02, 0.025, 0.032)
        cr.paint()
        if not self.system.bodies:
            return

        scale = self._canvas_scale(width, height)
        origin_x = width / 2.0
        origin_y = height / 2.0

        cr.set_line_width(1.0)
        for index, trail in enumerate(self.trails):
            if len(trail) < 2:
                continue
            rgba = self._rgba(self.system.bodies[index].color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.35)
            first_x, first_y = self._project(trail[0][0], trail[0][1], origin_x, origin_y, scale)
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                x, y = self._project(point_x, point_y, origin_x, origin_y, scale)
                cr.line_to(x, y)
            cr.stroke()

        for index, body in enumerate(self.system.bodies):
            if not body.visible:
                continue
            x, y = self._project(body.position_m[0], body.position_m[1], origin_x, origin_y, scale)
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

    def _canvas_scale(self, width: int, height: int) -> float:
        max_distance = max(
            math.hypot(body.position_m[0], body.position_m[1])
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

        scale = self._canvas_scale(width, height)
        origin_x = width / 2.0
        origin_y = height / 2.0
        closest_index = None
        closest_distance = math.inf

        for index, body in enumerate(self.system.bodies):
            if not body.visible:
                continue
            body_x, body_y = self._project(body.position_m[0], body.position_m[1], origin_x, origin_y, scale)
            distance = math.hypot(pointer_x - body_x, pointer_y - body_y)
            hit_radius = max(self._display_radius(body) + 4.0, 8.0)
            if distance <= hit_radius and distance < closest_distance:
                closest_index = index
                closest_distance = distance

        return closest_index

    def _project(self, x_m: float, y_m: float, origin_x: float, origin_y: float, scale: float) -> tuple[float, float]:
        return origin_x + x_m * scale, origin_y - y_m * scale

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
