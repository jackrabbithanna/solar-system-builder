# canvas.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Canvas widget for drawing and selecting solar system entities."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from collections.abc import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GObject, Gtk

from . import viewport
from .models import Body
from .scales import OverviewEntity, collapsed_child_counts

Trail = list[tuple[float, float]]
Positions = Sequence[Sequence[float]]


@dataclass
class CanvasScene:
    bodies: list[Body] = field(default_factory=list)
    active_indices: list[int] = field(default_factory=list)
    selected_body_index: int = 0
    selected_group_id: str | None = None
    selectable_group_ids: set[str] = field(default_factory=set)
    view_mode: str = "fit_system"
    using_system_overview: bool = False
    using_hybrid_focus: bool = False
    selected_group_center: tuple[float, float] | None = None
    hybrid_bounds: tuple[tuple[float, float], float] | None = None
    trails: list[Trail] = field(default_factory=list)
    overview_entities: list[OverviewEntity] = field(default_factory=list)
    overview_positions: Positions = field(default_factory=list)
    overview_trails: dict[str, Trail] = field(default_factory=dict)
    context_entities: list[OverviewEntity] = field(default_factory=list)
    context_positions: Positions = field(default_factory=list)
    context_trails: dict[str, Trail] = field(default_factory=dict)


class SolarSystemCanvas(Gtk.DrawingArea):
    __gtype_name__ = "SolarSystemCanvas"

    __gsignals__ = {
        "body-selected": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "group-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "zoom-factor-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._scene = CanvasScene()
        self._zoom_factor = 1.0
        self.set_draw_func(self._draw)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_query_tooltip)
        click_controller = Gtk.GestureClick.new()
        click_controller.connect("pressed", self._on_pressed)
        self.add_controller(click_controller)
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect("scroll", self._on_scroll)
        self.add_controller(scroll_controller)

    def set_scene(self, scene: CanvasScene) -> None:
        self._scene = scene
        self.queue_draw()

    def set_zoom_factor(self, zoom_factor: float) -> None:
        clamped = viewport.clamp_zoom_factor(zoom_factor)
        if clamped == self._zoom_factor:
            return
        self._zoom_factor = clamped
        self.emit("zoom-factor-changed", self._zoom_factor)
        self.queue_draw()

    def get_zoom_factor(self) -> float:
        return self._zoom_factor

    def _on_scroll(self, _controller, _dx: float, dy: float) -> bool:
        if dy < 0.0:
            self.set_zoom_factor(self._zoom_factor * 1.5)
            return True
        if dy > 0.0:
            self.set_zoom_factor(self._zoom_factor / 1.5)
            return True
        return False

    def _on_query_tooltip(self, _widget, x: int, y: int, _keyboard_mode: bool, tooltip) -> bool:
        body_index = self._body_index_at_point(float(x), float(y))
        if body_index is not None:
            tooltip.set_text(self._scene.bodies[body_index].name)
            return True
        entity = self._overview_entity_at_point(float(x), float(y))
        if entity is not None:
            tooltip.set_text(entity.name)
            return True
        return False

    def _on_pressed(self, _gesture, _n_press: int, x: float, y: float) -> None:
        body_index = self._body_index_at_point(x, y)
        if body_index is not None:
            self.emit("body-selected", body_index)
            return
        entity = self._overview_entity_at_point(x, y)
        if entity is not None and entity.id in self._scene.selectable_group_ids:
            self.emit("group-selected", entity.id)

    def _draw(self, _area, cr, width: int, height: int) -> None:
        cr.set_source_rgb(0.02, 0.025, 0.032)
        cr.paint()
        if not self._scene.bodies:
            return
        if self._scene.using_system_overview:
            self._draw_system_overview(cr, width, height)
            return

        center_x_m, center_y_m = self._view_center()
        scale = self._canvas_scale(width, height, center_x_m, center_y_m)
        origin_x = width / 2.0
        origin_y = height / 2.0
        active_indices = set(self._scene.active_indices)

        cr.set_line_width(1.0)
        for index, trail in enumerate(self._scene.trails):
            if index not in active_indices or len(trail) < 2:
                continue
            rgba = self._rgba(self._scene.bodies[index].color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.35)
            first_x, first_y = self._project(trail[0][0], trail[0][1], origin_x, origin_y, scale, center_x_m, center_y_m)
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                x, y = self._project(point_x, point_y, origin_x, origin_y, scale, center_x_m, center_y_m)
                cr.line_to(x, y)
            cr.stroke()

        for index, body in enumerate(self._scene.bodies):
            if index not in active_indices or not body.visible:
                continue
            x, y = self._project(body.position_m[0], body.position_m[1], origin_x, origin_y, scale, center_x_m, center_y_m)
            radius = self._display_radius(body)
            rgba = self._rgba(body.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
            cr.arc(x, y, radius, 0.0, math.tau)
            cr.fill()
            if index == self._scene.selected_body_index:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
                cr.set_line_width(2.0)
                cr.arc(x, y, radius + 4.0, 0.0, math.tau)
                cr.stroke()
            self._draw_collapsed_child_indicator(cr, index, x, y, radius, active_indices)

        barycenter = viewport.focused_body_barycenter_point(
            self._scene.bodies,
            active_indices,
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
            self._scene.view_mode,
        )
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter[0], barycenter[1])
        if self._scene.using_hybrid_focus:
            self._draw_context_entities(cr, origin_x, origin_y, scale, center_x_m, center_y_m)

    def _draw_system_overview(self, cr, width: int, height: int) -> None:
        entities = self._scene.overview_entities
        positions = self._scene.overview_positions
        if not entities or len(positions) == 0:
            return
        center_x_m, center_y_m = viewport.overview_view_center(entities, positions)
        scale = viewport.overview_canvas_scale(
            width,
            height,
            positions,
            center_x_m,
            center_y_m,
            self._zoom_factor,
            self._scene.view_mode,
        )
        origin_x = width / 2.0
        origin_y = height / 2.0

        cr.set_line_width(1.25)
        for entity in entities:
            trail = self._scene.overview_trails.get(entity.id, [])
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
            if entity.id == self._scene.selected_group_id:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
                cr.set_line_width(2.0)
                cr.arc(x, y, 12.0, 0.0, math.tau)
                cr.stroke()

        barycenter = viewport.shared_barycenter_point(
            entities,
            positions,
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
            self._scene.view_mode,
        )
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
        entities = self._scene.context_entities
        positions = self._scene.context_positions
        if not entities or len(positions) == 0:
            return

        cr.set_line_width(1.0)
        for entity in entities:
            trail = self._scene.context_trails.get(entity.id, [])
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

        barycenter = viewport.shared_barycenter_point(
            entities,
            positions,
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
            self._scene.view_mode,
        )
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter[0], barycenter[1])

    def _body_index_at_point(self, pointer_x: float, pointer_y: float) -> int | None:
        if not self._scene.bodies or self._scene.using_system_overview:
            return None
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return None

        center_x_m, center_y_m = self._view_center()
        scale = self._canvas_scale(width, height, center_x_m, center_y_m)
        return viewport.body_index_at_point(
            self._scene.bodies,
            self._scene.active_indices,
            pointer_x,
            pointer_y,
            width,
            height,
            scale,
            center_x_m,
            center_y_m,
            self._scene.view_mode,
            self._display_radius,
        )

    def _overview_entity_at_point(self, pointer_x: float, pointer_y: float) -> OverviewEntity | None:
        if not self._scene.bodies:
            return None
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return None

        if self._scene.using_system_overview:
            entities = self._scene.overview_entities
            positions = self._scene.overview_positions
            hit_radius = 12.0
        elif self._scene.using_hybrid_focus:
            entities = self._scene.context_entities
            positions = self._scene.context_positions
            hit_radius = 10.0
        else:
            return None

        if not entities or len(positions) == 0:
            return None
        if self._scene.using_system_overview:
            center_x_m, center_y_m = viewport.overview_view_center(entities, positions)
            scale = viewport.overview_canvas_scale(
                width,
                height,
                positions,
                center_x_m,
                center_y_m,
                self._zoom_factor,
                self._scene.view_mode,
            )
        else:
            center_x_m, center_y_m = self._view_center()
            scale = self._canvas_scale(width, height, center_x_m, center_y_m)
        return viewport.entity_at_point(
            entities,
            positions,
            pointer_x,
            pointer_y,
            width,
            height,
            scale,
            center_x_m,
            center_y_m,
            self._scene.view_mode,
            hit_radius,
        )

    def _view_center(self) -> tuple[float, float]:
        return viewport.body_view_center(
            self._scene.bodies,
            self._scene.view_mode,
            self._scene.selected_body_index,
            self._scene.selected_group_center,
            self._scene.hybrid_bounds if self._scene.using_hybrid_focus else None,
        )

    def _canvas_scale(self, width: int, height: int, center_x_m: float, center_y_m: float) -> float:
        return viewport.canvas_scale(
            width,
            height,
            self._scene.bodies,
            self._scene.active_indices,
            center_x_m,
            center_y_m,
            self._zoom_factor,
            self._scene.view_mode,
            use_focused_bounds=self._scene.using_hybrid_focus,
        )

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
        return viewport.project(
            x_m,
            y_m,
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
            self._scene.view_mode,
        )

    def _draw_shared_barycenter(self, cr, x: float, y: float) -> None:
        cr.set_source_rgba(1.0, 0.08, 0.06, 0.95)
        cr.arc(x, y, 3.0, 0.0, math.tau)
        cr.fill()

    def _draw_collapsed_child_indicator(
        self,
        cr,
        body_index: int,
        x: float,
        y: float,
        radius: float,
        active_indices: set[int],
    ) -> None:
        count = collapsed_child_counts(self._scene.bodies, list(active_indices)).get(body_index, 0)
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

    def _display_radius(self, body: Body) -> float:
        if body.kind == "star":
            return 8.5
        return max(3.0, min(7.0, math.log10(body.radius_m) - 2.0))

    def _rgba(self, color: str) -> Gdk.RGBA:
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            rgba.parse("#ffffff")
        return rgba
