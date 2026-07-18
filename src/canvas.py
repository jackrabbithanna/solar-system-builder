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
from .scales import CanvasBounds, OverviewEntity, collapsed_child_counts

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
    using_focused_fit: bool = False
    focused_fit_session: int = 0
    selected_group_center: tuple[float, float] | None = None
    trail_reference_position: tuple[float, float] | None = None
    trails: list[Trail] = field(default_factory=list)
    overview_entities: list[OverviewEntity] = field(default_factory=list)
    overview_positions: Positions = field(default_factory=list)
    overview_trails: dict[str, Trail] = field(default_factory=dict)
    context_entities: list[OverviewEntity] = field(default_factory=list)
    context_positions: Positions = field(default_factory=list)
    context_trails: dict[str, Trail] = field(default_factory=dict)
    inset_entities: list[OverviewEntity] = field(default_factory=list)
    inset_positions: Positions = field(default_factory=list)
    inset_trails: dict[str, Trail] = field(default_factory=dict)
    inset_targets: dict[str, str] = field(default_factory=dict)
    focused_inset_entity_id: str | None = None


class SolarSystemCanvas(Gtk.DrawingArea):
    __gtype_name__ = "SolarSystemCanvas"

    __gsignals__ = {
        "body-selected": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "group-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "focus-target-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "zoom-factor-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._scene = CanvasScene()
        self._zoom_factor = 1.0
        self._focused_fit_key: tuple[int, tuple[int, ...]] | None = None
        self._focused_fit_bounds: CanvasBounds | None = None
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
        self._update_focused_fit()
        self.queue_draw()

    def _update_focused_fit(self) -> None:
        if not self._scene.using_focused_fit:
            self._focused_fit_key = None
            self._focused_fit_bounds = None
            return

        key = (self._scene.focused_fit_session, tuple(self._scene.active_indices))
        required_bounds = viewport.focused_fit_bounds(self._scene.bodies, self._scene.active_indices)
        if required_bounds is None:
            self._focused_fit_key = key
            self._focused_fit_bounds = None
            return

        previous_extent = None
        if key == self._focused_fit_key and self._focused_fit_bounds is not None:
            previous_extent = self._focused_fit_bounds.half_width_m
        extent = viewport.stabilize_focused_extent(previous_extent, required_bounds.half_width_m)
        self._focused_fit_key = key
        self._focused_fit_bounds = CanvasBounds(required_bounds.center, extent, extent)

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
        inset_entity = self._inset_entity_at_point(float(x), float(y))
        if inset_entity is not None:
            tooltip.set_text(inset_entity.name)
            return True
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
        inset_entity = self._inset_entity_at_point(x, y)
        if inset_entity is not None:
            target = self._scene.inset_targets.get(inset_entity.id)
            if target is not None and inset_entity.id != self._scene.focused_inset_entity_id:
                self.emit("focus-target-selected", target)
            return
        if self._scene.using_hybrid_focus:
            rect = viewport.overview_inset_rect(self.get_width(), self.get_height())
            if viewport.point_in_rect(x, y, rect):
                return
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
            trail_x, trail_y = viewport.trail_point_in_system_frame(
                trail[0][0], trail[0][1], self._scene.trail_reference_position
            )
            first_x, first_y = self._project(trail_x, trail_y, origin_x, origin_y, scale, center_x_m, center_y_m)
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                trail_x, trail_y = viewport.trail_point_in_system_frame(
                    point_x, point_y, self._scene.trail_reference_position
                )
                x, y = self._project(trail_x, trail_y, origin_x, origin_y, scale, center_x_m, center_y_m)
                cr.line_to(x, y)
            cr.stroke()

        for index, body in enumerate(self._scene.bodies):
            if index not in active_indices or not body.visible:
                continue
            x, y = self._project(body.position_m[0], body.position_m[1], origin_x, origin_y, scale, center_x_m, center_y_m)
            radius = self._display_radius(body)
            rgba = self._rgba(body.color)
            self._draw_body_marker(
                cr, body, x, y, radius, rgba, origin_x, origin_y, scale, center_x_m, center_y_m
            )
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
            self._draw_overview_inset(cr, width, height)

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

    def _draw_overview_inset(self, cr, width: int, height: int) -> None:
        entities = self._scene.inset_entities
        positions = self._scene.inset_positions
        if len(entities) < 2 or len(positions) != len(entities):
            return
        rect = viewport.overview_inset_rect(width, height)
        padding = 10.0
        inner_width = max(1, int(rect.width - 2.0 * padding))
        inner_height = max(1, int(rect.height - 2.0 * padding))
        center_x_m, center_y_m = viewport.overview_view_center(entities, positions)
        scale = viewport.overview_canvas_scale(
            inner_width,
            inner_height,
            positions,
            center_x_m,
            center_y_m,
            1.0,
            "fit_system",
        )
        origin_x = rect.x + rect.width / 2.0
        origin_y = rect.y + rect.height / 2.0

        cr.save()
        cr.set_source_rgba(0.015, 0.02, 0.027, 0.9)
        cr.rectangle(rect.x, rect.y, rect.width, rect.height)
        cr.fill_preserve()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.28)
        cr.set_line_width(1.0)
        cr.stroke()
        cr.rectangle(rect.x, rect.y, rect.width, rect.height)
        cr.clip()

        for entity in entities:
            trail = self._scene.inset_trails.get(entity.id, [])
            if len(trail) < 2:
                continue
            rgba = self._rgba(entity.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.35)
            first_x, first_y = viewport.project(
                trail[0][0], trail[0][1], origin_x, origin_y, scale, center_x_m, center_y_m, "fit_system"
            )
            cr.move_to(first_x, first_y)
            for point_x, point_y in trail[1:]:
                projected = viewport.project(
                    point_x, point_y, origin_x, origin_y, scale, center_x_m, center_y_m, "fit_system"
                )
                cr.line_to(*projected)
            cr.stroke()

        for index, entity in enumerate(entities):
            x, y = viewport.project(
                float(positions[index][0]),
                float(positions[index][1]),
                origin_x,
                origin_y,
                scale,
                center_x_m,
                center_y_m,
                "fit_system",
            )
            rgba = self._rgba(entity.color)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
            cr.arc(x, y, 5.0, 0.0, math.tau)
            cr.fill()
            if entity.id == self._scene.focused_inset_entity_id:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
                cr.set_line_width(2.0)
                cr.arc(x, y, 9.0, 0.0, math.tau)
                cr.stroke()
        cr.restore()

    def _inset_entity_at_point(self, pointer_x: float, pointer_y: float) -> OverviewEntity | None:
        if not self._scene.using_hybrid_focus or len(self._scene.inset_entities) < 2:
            return None
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return None
        rect = viewport.overview_inset_rect(width, height)
        if not viewport.point_in_rect(pointer_x, pointer_y, rect):
            return None
        padding = 10.0
        inner_width = max(1, int(rect.width - 2.0 * padding))
        inner_height = max(1, int(rect.height - 2.0 * padding))
        center_x_m, center_y_m = viewport.overview_view_center(
            self._scene.inset_entities,
            self._scene.inset_positions,
        )
        scale = viewport.overview_canvas_scale(
            inner_width,
            inner_height,
            self._scene.inset_positions,
            center_x_m,
            center_y_m,
            1.0,
            "fit_system",
        )
        return viewport.entity_at_point(
            self._scene.inset_entities,
            self._scene.inset_positions,
            pointer_x,
            pointer_y,
            int(rect.width),
            int(rect.height),
            scale,
            center_x_m,
            center_y_m,
            "fit_system",
            10.0,
            origin_x=rect.x + rect.width / 2.0,
            origin_y=rect.y + rect.height / 2.0,
        )

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
        else:
            return None

        if not entities or len(positions) == 0:
            return None
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
            self._focused_fit_bounds if self._scene.using_focused_fit else None,
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
            use_focused_bounds=self._scene.using_focused_fit,
            focused_bounds=self._focused_fit_bounds,
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
        if body.kind == "moon":
            return 3.0
        if body.kind == "asteroid":
            return 3.5
        if body.kind == "comet":
            return 4.0
        return max(3.0, min(7.0, math.log10(body.radius_m) - 2.0))

    def _draw_body_marker(
        self,
        cr,
        body: Body,
        x: float,
        y: float,
        radius: float,
        rgba: Gdk.RGBA,
        origin_x: float,
        origin_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
    ) -> None:
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
        if body.kind == "asteroid":
            cr.move_to(x, y - radius)
            cr.line_to(x + radius * 0.85, y - radius * 0.15)
            cr.line_to(x + radius * 0.55, y + radius)
            cr.line_to(x - radius * 0.75, y + radius * 0.65)
            cr.line_to(x - radius, y - radius * 0.35)
            cr.close_path()
            cr.fill()
            return
        if body.kind == "comet":
            self._draw_comet_tail(
                cr,
                body,
                x,
                y,
                radius,
                rgba,
                origin_x,
                origin_y,
                scale,
                center_x_m,
                center_y_m,
            )
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
        cr.arc(x, y, radius, 0.0, math.tau)
        cr.fill()

    def _draw_comet_tail(
        self,
        cr,
        body: Body,
        x: float,
        y: float,
        radius: float,
        rgba: Gdk.RGBA,
        origin_x: float,
        origin_y: float,
        scale: float,
        center_x_m: float,
        center_y_m: float,
    ) -> None:
        parent = next(
            (candidate for candidate in self._scene.bodies if candidate.id == body.parent_id),
            None,
        )
        if parent is None or parent.kind != "star":
            return
        parent_x, parent_y = self._project(
            parent.position_m[0],
            parent.position_m[1],
            origin_x,
            origin_y,
            scale,
            center_x_m,
            center_y_m,
        )
        delta_x = x - parent_x
        delta_y = y - parent_y
        distance = math.hypot(delta_x, delta_y)
        if distance <= 0.0:
            return
        direction_x = delta_x / distance
        direction_y = delta_y / distance
        perpendicular_x = -direction_y
        perpendicular_y = direction_x
        tail_start_x = x + direction_x * radius
        tail_start_y = y + direction_y * radius
        tail_end_x = x + direction_x * (radius + 14.0)
        tail_end_y = y + direction_y * (radius + 14.0)
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.35)
        cr.move_to(
            tail_start_x + perpendicular_x * 2.5,
            tail_start_y + perpendicular_y * 2.5,
        )
        cr.line_to(tail_end_x, tail_end_y)
        cr.line_to(
            tail_start_x - perpendicular_x * 2.5,
            tail_start_y - perpendicular_y * 2.5,
        )
        cr.close_path()
        cr.fill()

    def _rgba(self, color: str) -> Gdk.RGBA:
        rgba = Gdk.RGBA()
        if not rgba.parse(color):
            rgba.parse("#ffffff")
        return rgba
