# canvas.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Canvas widget for drawing and selecting solar system entities."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from collections.abc import Sequence

import gi
import numpy as np

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GObject, Gtk

from . import viewport
from .models import Body
from .scales import CanvasBounds, OverviewEntity, collapsed_child_counts

Trail = list[tuple[float, float, float]]
Positions = Sequence[Sequence[float]]
PAN_DRAG_THRESHOLD_PX = 4.0
RENDER_MODES = {"2d", "3d"}


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
    selected_group_center: tuple[float, float, float] | None = None
    trail_reference_position: tuple[float, float, float] | None = None
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
        "view-state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._scene = CanvasScene()
        self._render_mode = "2d"
        self._zoom_factors = {"2d": 1.0, "3d": 1.0}
        self._pan_offset_m = (0.0, 0.0)
        self._pan_offset_3d_m = (0.0, 0.0, 0.0)
        self._camera = viewport.Camera3D()
        self._camera_basis = viewport.camera_basis(self._camera)
        self._drag_start_pan_offset_m = (0.0, 0.0)
        self._drag_start_pan_offset_3d_m = (0.0, 0.0, 0.0)
        self._drag_start_camera = self._camera
        self._drag_3d_pan = False
        self._drag_scale = 1.0
        self._drag_view_mode = "fit_system"
        self._drag_active = False
        self._drag_moved = False
        self._focused_fit_key: tuple[int, tuple[int, ...]] | None = None
        self._focused_fit_bounds: CanvasBounds | None = None
        self._focused_fit_bounds_3d: viewport.CanvasBounds3D | None = None
        self._trail_arrays: list[np.ndarray] = []
        self._overview_trail_arrays: dict[str, np.ndarray] = {}
        self.set_draw_func(self._draw)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_query_tooltip)
        click_controller = Gtk.GestureClick.new()
        click_controller.connect("pressed", self._on_click_pressed)
        click_controller.connect("released", self._on_click_released)
        self.add_controller(click_controller)
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect("scroll", self._on_scroll)
        self.add_controller(scroll_controller)
        drag_gesture = Gtk.GestureDrag.new()
        drag_gesture.set_button(Gdk.BUTTON_PRIMARY)
        drag_gesture.connect("drag-begin", self._on_drag_begin)
        drag_gesture.connect("drag-update", self._on_drag_update)
        drag_gesture.connect("drag-end", self._on_drag_end)
        drag_gesture.connect("cancel", self._on_drag_cancel)
        self.add_controller(drag_gesture)

    def set_scene(self, scene: CanvasScene) -> None:
        self._scene = scene
        if self._render_mode == "3d":
            self._cache_3d_trails()
        self._update_focused_fit()
        self.queue_draw()

    def _cache_3d_trails(self) -> None:
        self._trail_arrays = [self._trail_array(trail) for trail in self._scene.trails]
        self._overview_trail_arrays = {
            entity_id: self._trail_array(trail)
            for entity_id, trail in self._scene.overview_trails.items()
        }

    def _trail_array(self, trail: Trail) -> np.ndarray:
        if not trail:
            return np.empty((0, 3), dtype=float)
        return np.asarray(trail, dtype=float).reshape((-1, 3))

    def _update_focused_fit(self) -> None:
        if not self._scene.using_focused_fit:
            self._focused_fit_key = None
            self._focused_fit_bounds = None
            self._focused_fit_bounds_3d = None
            return

        key = (self._scene.focused_fit_session, tuple(self._scene.active_indices))
        required_bounds = viewport.focused_fit_bounds(self._scene.bodies, self._scene.active_indices)
        required_bounds_3d = viewport.focused_fit_bounds_3d(
            self._scene.bodies,
            self._scene.active_indices,
        )
        if required_bounds is None or required_bounds_3d is None:
            self._focused_fit_key = key
            self._focused_fit_bounds = None
            self._focused_fit_bounds_3d = None
            return

        previous_extent = None
        previous_extent_3d = None
        if key == self._focused_fit_key and self._focused_fit_bounds is not None:
            previous_extent = self._focused_fit_bounds.half_width_m
        if key == self._focused_fit_key and self._focused_fit_bounds_3d is not None:
            previous_extent_3d = self._focused_fit_bounds_3d.radius_m
        extent = viewport.stabilize_focused_extent(previous_extent, required_bounds.half_width_m)
        extent_3d = viewport.stabilize_focused_extent(
            previous_extent_3d,
            required_bounds_3d.radius_m,
        )
        self._focused_fit_key = key
        self._focused_fit_bounds = CanvasBounds(required_bounds.center, extent, extent)
        self._focused_fit_bounds_3d = viewport.CanvasBounds3D(
            required_bounds_3d.center,
            extent_3d,
        )

    @property
    def _zoom_factor(self) -> float:
        return self._zoom_factors[self._render_mode]

    @_zoom_factor.setter
    def _zoom_factor(self, value: float) -> None:
        self._zoom_factors[self._render_mode] = value

    def set_render_mode(self, render_mode: str) -> None:
        if render_mode not in RENDER_MODES:
            raise ValueError(f"unsupported render mode {render_mode}")
        if render_mode == self._render_mode:
            return
        self._finish_drag()
        self._render_mode = render_mode
        if render_mode == "3d":
            self._cache_3d_trails()
        self._update_pan_cursor()
        self.emit("zoom-factor-changed", self._zoom_factor)
        self.emit("view-state-changed")
        self.queue_draw()

    def get_render_mode(self) -> str:
        return self._render_mode

    def view_is_default(self) -> bool:
        if self._render_mode == "2d":
            return self._zoom_factor == 1.0 and self._pan_offset_m == (0.0, 0.0)
        return (
            self._zoom_factor == 1.0
            and self._pan_offset_3d_m == (0.0, 0.0, 0.0)
            and self._camera == viewport.Camera3D()
        )

    def reset_view(self) -> None:
        zoom_changed = self._zoom_factor != 1.0
        changed = not self.view_is_default()
        self._zoom_factor = 1.0
        if self._render_mode == "2d":
            self._pan_offset_m = (0.0, 0.0)
        else:
            self._pan_offset_3d_m = (0.0, 0.0, 0.0)
            self._camera = viewport.Camera3D()
            self._camera_basis = viewport.camera_basis(self._camera)
        self._finish_drag()
        self._update_pan_cursor()
        if zoom_changed:
            self.emit("zoom-factor-changed", self._zoom_factor)
        if changed:
            self.emit("view-state-changed")
            self.queue_draw()

    def reset_all_views(self) -> None:
        active_zoom_changed = self._zoom_factor != 1.0
        self._zoom_factors = {"2d": 1.0, "3d": 1.0}
        self._pan_offset_m = (0.0, 0.0)
        self._pan_offset_3d_m = (0.0, 0.0, 0.0)
        self._camera = viewport.Camera3D()
        self._camera_basis = viewport.camera_basis(self._camera)
        self._finish_drag()
        self._update_pan_cursor()
        if active_zoom_changed:
            self.emit("zoom-factor-changed", self._zoom_factor)
        self.emit("view-state-changed")
        self.queue_draw()

    def set_zoom_factor(self, zoom_factor: float) -> None:
        clamped = viewport.clamp_zoom_factor(zoom_factor)
        pan_changed = False
        if self._render_mode == "2d" and clamped == 1.0 and self._pan_offset_m != (0.0, 0.0):
            self._pan_offset_m = (0.0, 0.0)
            self._drag_active = False
            pan_changed = True
        if clamped == self._zoom_factor:
            if pan_changed:
                self._update_pan_cursor()
                self.emit("view-state-changed")
                self.queue_draw()
            return
        self._zoom_factor = clamped
        self._update_pan_cursor()
        self.emit("zoom-factor-changed", self._zoom_factor)
        self.emit("view-state-changed")
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

    def _on_drag_begin(self, gesture, start_x: float, start_y: float) -> None:
        width = self.get_width()
        height = self.get_height()
        if (
            (self._render_mode == "2d" and self._zoom_factor <= 1.0)
            or width <= 0
            or height <= 0
            or self._point_in_overview_inset(start_x, start_y)
        ):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        scale = self._current_canvas_scale(width, height)
        if scale is None or scale <= 0.0 or not math.isfinite(scale):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._drag_start_pan_offset_m = self._pan_offset_m
        self._drag_start_pan_offset_3d_m = self._pan_offset_3d_m
        self._drag_start_camera = self._camera
        state = gesture.get_current_event_state()
        self._drag_3d_pan = bool(state & Gdk.ModifierType.SHIFT_MASK)
        self._drag_scale = scale
        self._drag_view_mode = self._scene.view_mode
        self._drag_active = True
        self._drag_moved = False
        self._update_pan_cursor()

    def _on_drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        if not self._drag_active:
            return
        if math.hypot(offset_x, offset_y) < PAN_DRAG_THRESHOLD_PX:
            return
        self._drag_moved = True
        if self._render_mode == "3d":
            if self._drag_3d_pan:
                delta = viewport.pan_center_delta_3d(
                    offset_x,
                    offset_y,
                    self._drag_scale,
                    self._drag_view_mode,
                    self._drag_start_camera,
                )
                self._pan_offset_3d_m = tuple(
                    self._drag_start_pan_offset_3d_m[axis] + delta[axis]
                    for axis in range(3)
                )
            else:
                self._camera = viewport.camera_after_drag(
                    self._drag_start_camera,
                    offset_x,
                    offset_y,
                )
                self._camera_basis = viewport.camera_basis(self._camera)
            self.emit("view-state-changed")
            self.queue_draw()
            return
        delta_x_m, delta_y_m = viewport.pan_center_delta(
            offset_x,
            offset_y,
            self._drag_scale,
            self._drag_view_mode,
        )
        self._pan_offset_m = (
            self._drag_start_pan_offset_m[0] + delta_x_m,
            self._drag_start_pan_offset_m[1] + delta_y_m,
        )
        self.emit("view-state-changed")
        self.queue_draw()

    def _on_drag_end(self, _gesture, _offset_x: float, _offset_y: float) -> None:
        self._finish_drag()

    def _on_drag_cancel(self, _gesture, _sequence) -> None:
        self._finish_drag()

    def _finish_drag(self) -> None:
        if not self._drag_active:
            return
        self._drag_active = False
        self._update_pan_cursor()

    def _update_pan_cursor(self) -> None:
        if self._drag_active:
            cursor_name = "grabbing"
        elif self._render_mode == "3d" or self._zoom_factor > 1.0:
            cursor_name = "grab"
        else:
            cursor_name = None
        self.set_cursor_from_name(cursor_name)

    def _point_in_overview_inset(self, x: float, y: float) -> bool:
        if not self._scene.using_hybrid_focus or len(self._scene.inset_entities) < 2:
            return False
        return viewport.point_in_rect(
            x,
            y,
            viewport.overview_inset_rect(self.get_width(), self.get_height()),
        )

    def _on_click_pressed(self, _gesture, _n_press: int, _x: float, _y: float) -> None:
        self._drag_moved = False

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

    def _on_click_released(self, _gesture, _n_press: int, x: float, y: float) -> None:
        if self._drag_moved:
            return
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
        if self._render_mode == "3d":
            self._draw_3d(cr, width, height)
            return
        if self._scene.using_system_overview:
            self._draw_system_overview(cr, width, height)
            return

        base_center_x_m, base_center_y_m = self._base_view_center()
        center_x_m, center_y_m = self._view_center(base_center_x_m, base_center_y_m)
        scale = self._canvas_scale(width, height, base_center_x_m, base_center_y_m)
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
            for point in trail[1:]:
                trail_x, trail_y = viewport.trail_point_in_system_frame(
                    point[0], point[1], self._scene.trail_reference_position
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

    def _draw_3d(self, cr, width: int, height: int) -> None:
        if self._scene.using_system_overview:
            self._draw_system_overview_3d(cr, width, height)
            return
        base_center = self._base_view_center_3d()
        center = self._view_center_3d(base_center)
        scale = self._canvas_scale_3d(width, height, base_center)
        origin_x = width / 2.0
        origin_y = height / 2.0
        active_indices = set(self._scene.active_indices)

        self._draw_reference_plane_3d(cr, width, height, scale, center)
        projected_trails = []
        for index, trail in enumerate(self._trail_arrays):
            if index not in active_indices or len(trail) < 2:
                continue
            points = self._project_trail_3d(
                trail,
                origin_x,
                origin_y,
                scale,
                center,
                self._scene.trail_reference_position,
            )
            if len(points) >= 2:
                projected_trails.append(
                    (
                        float(points[:, 2].mean()),
                        self._rgba(self._scene.bodies[index].color),
                        points,
                    )
                )
        self._draw_projected_trails_3d(cr, projected_trails, scale, width, height, 0.35)

        projected_bodies = []
        for index, body in enumerate(self._scene.bodies):
            if index not in active_indices or not body.visible:
                continue
            projected_bodies.append(
                (
                    self._project_3d(body.position_m, origin_x, origin_y, scale, center),
                    index,
                    body,
                )
            )
        projected_bodies.sort(key=lambda item: item[0].depth)
        for point, index, body in projected_bodies:
            radius = self._display_radius(body)
            rgba = self._rgba(body.color)
            alpha = 0.78 + 0.2 * self._depth_factor(point.depth, scale, width, height)
            self._draw_body_marker_3d(
                cr,
                body,
                point.x,
                point.y,
                radius,
                rgba,
                alpha,
                origin_x,
                origin_y,
                scale,
                center,
            )
            if index == self._scene.selected_body_index:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
                cr.set_line_width(2.0)
                cr.arc(point.x, point.y, radius + 4.0, 0.0, math.tau)
                cr.stroke()
            self._draw_collapsed_child_indicator(
                cr,
                index,
                point.x,
                point.y,
                radius,
                active_indices,
            )

        barycenter = viewport.focused_body_barycenter_point_3d(
            self._scene.bodies,
            active_indices,
            origin_x,
            origin_y,
            scale,
            center,
            self._scene.view_mode,
            self._camera,
        )
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter.x, barycenter.y)
        self._draw_axis_triad_3d(cr, width, height)
        if self._scene.using_hybrid_focus:
            self._draw_overview_inset(cr, width, height)

    def _draw_system_overview_3d(self, cr, width: int, height: int) -> None:
        entities = self._scene.overview_entities
        positions = self._scene.overview_positions
        if not entities or len(positions) < len(entities):
            return
        base_center = viewport.overview_view_center_3d(entities, positions)
        center = self._view_center_3d(base_center)
        scale = viewport.overview_canvas_scale_3d(
            width,
            height,
            positions,
            base_center,
            self._zoom_factor,
            self._scene.view_mode,
        )
        origin_x = width / 2.0
        origin_y = height / 2.0
        self._draw_reference_plane_3d(cr, width, height, scale, center)

        projected_trails = []
        for entity in entities:
            trail = self._overview_trail_arrays.get(entity.id, np.empty((0, 3), dtype=float))
            if len(trail) < 2:
                continue
            points = self._project_trail_3d(
                trail,
                origin_x,
                origin_y,
                scale,
                center,
                None,
            )
            projected_trails.append(
                (
                    float(points[:, 2].mean()),
                    self._rgba(entity.color),
                    points,
                )
            )
        self._draw_projected_trails_3d(cr, projected_trails, scale, width, height, 0.45)

        projected_entities = [
            (
                self._project_3d(positions[index], origin_x, origin_y, scale, center),
                entity,
            )
            for index, entity in enumerate(entities)
        ]
        projected_entities.sort(key=lambda item: item[0].depth)
        for point, entity in projected_entities:
            rgba = self._rgba(entity.color)
            alpha = 0.78 + 0.2 * self._depth_factor(point.depth, scale, width, height)
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)
            cr.arc(point.x, point.y, 7.0, 0.0, math.tau)
            cr.fill()
            if entity.id == self._scene.selected_group_id:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
                cr.set_line_width(2.0)
                cr.arc(point.x, point.y, 12.0, 0.0, math.tau)
                cr.stroke()

        barycenter = viewport.shared_barycenter_point_3d(
            entities,
            positions,
            origin_x,
            origin_y,
            scale,
            center,
            self._scene.view_mode,
            self._camera,
        )
        if barycenter is not None:
            self._draw_shared_barycenter(cr, barycenter.x, barycenter.y)
        self._draw_axis_triad_3d(cr, width, height)

    def _project_trail_3d(
        self,
        trail: Trail | np.ndarray,
        origin_x: float,
        origin_y: float,
        scale: float,
        center: tuple[float, float, float],
        reference_position: tuple[float, float, float] | None,
    ) -> np.ndarray:
        points = np.asarray(trail, dtype=float).reshape((-1, 3))
        if reference_position is not None:
            points = points + np.asarray(reference_position, dtype=float)
        return viewport.project_points_3d(
            points,
            origin_x,
            origin_y,
            scale,
            center,
            self._scene.view_mode,
            self._camera,
            self._camera_basis,
        )

    def _draw_projected_trails_3d(
        self,
        cr,
        trails,
        scale: float,
        width: int,
        height: int,
        base_alpha: float,
    ) -> None:
        cr.set_line_width(1.1)
        for mean_depth, rgba, points in sorted(trails, key=lambda item: item[0]):
            alpha = base_alpha * (
                0.65 + 0.35 * self._depth_factor(mean_depth, scale, width, height)
            )
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)
            cr.move_to(points[0][0], points[0][1])
            for point in points[1:]:
                cr.line_to(point[0], point[1])
            cr.stroke()

    def _draw_reference_plane_3d(
        self,
        cr,
        width: int,
        height: int,
        scale: float,
        center: tuple[float, float, float],
    ) -> None:
        if scale <= 0.0:
            return
        compressed_extent = min(width, height) * 0.38 / scale
        extent = viewport.uncompress_view_distance(
            compressed_extent,
            self._scene.view_mode,
        )
        if extent <= 0.0 or not math.isfinite(extent):
            return
        grid_center_x = center[0]
        grid_center_y = center[1]
        origin_x = width / 2.0
        origin_y = height / 2.0
        divisions = 4
        samples = 16
        cr.set_line_width(0.75)
        for grid_index in range(-divisions, divisions + 1):
            offset = extent * grid_index / divisions
            for along_x in (True, False):
                points = []
                for sample_index in range(samples + 1):
                    along = -extent + 2.0 * extent * sample_index / samples
                    position = (
                        (grid_center_x + along if along_x else grid_center_x + offset),
                        (grid_center_y + offset if along_x else grid_center_y + along),
                        0.0,
                    )
                    points.append(
                        self._project_3d(
                            position,
                            origin_x,
                            origin_y,
                            scale,
                            center,
                        )
                    )
                alpha = 0.13 if grid_index == 0 else 0.055
                cr.set_source_rgba(0.72, 0.78, 0.86, alpha)
                cr.move_to(points[0].x, points[0].y)
                for point in points[1:]:
                    cr.line_to(point.x, point.y)
                cr.stroke()

    def _draw_axis_triad_3d(self, cr, width: int, height: int) -> None:
        right, up, _toward_camera = self._camera_basis
        origin_x = width - 48.0
        origin_y = height - 44.0
        length = 28.0
        axes = (
            ("X", (1.0, 0.0, 0.0), (0.95, 0.25, 0.25)),
            ("Y", (0.0, 1.0, 0.0), (0.3, 0.9, 0.4)),
            ("Z", (0.0, 0.0, 1.0), (0.35, 0.55, 1.0)),
        )
        cr.set_font_size(10.0)
        for label, axis, color in axes:
            horizontal = sum(axis[index] * right[index] for index in range(3))
            vertical = sum(axis[index] * up[index] for index in range(3))
            end_x = origin_x + horizontal * length
            end_y = origin_y - vertical * length
            cr.set_source_rgba(*color, 0.9)
            cr.set_line_width(1.5)
            cr.move_to(origin_x, origin_y)
            cr.line_to(end_x, end_y)
            cr.stroke()
            cr.arc(end_x, end_y, 1.8, 0.0, math.tau)
            cr.fill()
            cr.move_to(end_x + 3.0, end_y - 3.0)
            cr.show_text(label)

    def _depth_factor(
        self,
        depth_m: float,
        scale: float,
        width: int,
        height: int,
    ) -> float:
        canvas_radius = max(1.0, min(width, height) * 0.45)
        return 0.5 + 0.5 * math.tanh(depth_m * scale / canvas_radius)

    def _draw_body_marker_3d(
        self,
        cr,
        body: Body,
        x: float,
        y: float,
        radius: float,
        rgba: Gdk.RGBA,
        alpha: float,
        origin_x: float,
        origin_y: float,
        scale: float,
        center: tuple[float, float, float],
    ) -> None:
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)
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
            self._draw_comet_tail_3d(
                cr,
                body,
                x,
                y,
                radius,
                rgba,
                origin_x,
                origin_y,
                scale,
                center,
            )
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, alpha)
        cr.arc(x, y, radius, 0.0, math.tau)
        cr.fill()

    def _draw_comet_tail_3d(
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
        center: tuple[float, float, float],
    ) -> None:
        parent = next(
            (candidate for candidate in self._scene.bodies if candidate.id == body.parent_id),
            None,
        )
        if parent is None or parent.kind != "star":
            return
        parent_point = self._project_3d(
            parent.position_m,
            origin_x,
            origin_y,
            scale,
            center,
        )
        delta_x = x - parent_point.x
        delta_y = y - parent_point.y
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

    def _draw_system_overview(self, cr, width: int, height: int) -> None:
        entities = self._scene.overview_entities
        positions = self._scene.overview_positions
        if not entities or len(positions) == 0:
            return
        base_center_x_m, base_center_y_m = viewport.overview_view_center(entities, positions)
        center_x_m, center_y_m = self._view_center(base_center_x_m, base_center_y_m)
        scale = viewport.overview_canvas_scale(
            width,
            height,
            positions,
            base_center_x_m,
            base_center_y_m,
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
            for point in trail[1:]:
                x, y = self._project(point[0], point[1], origin_x, origin_y, scale, center_x_m, center_y_m)
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
            for point in trail[1:]:
                projected = viewport.project(
                    point[0], point[1], origin_x, origin_y, scale, center_x_m, center_y_m, "fit_system"
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

        if self._render_mode == "3d":
            base_center = self._base_view_center_3d()
            center = self._view_center_3d(base_center)
            scale = self._canvas_scale_3d(width, height, base_center)
            return viewport.body_index_at_point_3d(
                self._scene.bodies,
                self._scene.active_indices,
                pointer_x,
                pointer_y,
                width,
                height,
                scale,
                center,
                self._scene.view_mode,
                self._camera,
                self._display_radius,
            )

        base_center_x_m, base_center_y_m = self._base_view_center()
        center_x_m, center_y_m = self._view_center(base_center_x_m, base_center_y_m)
        scale = self._canvas_scale(width, height, base_center_x_m, base_center_y_m)
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
        if self._render_mode == "3d":
            base_center = viewport.overview_view_center_3d(entities, positions)
            center = self._view_center_3d(base_center)
            scale = viewport.overview_canvas_scale_3d(
                width,
                height,
                positions,
                base_center,
                self._zoom_factor,
                self._scene.view_mode,
            )
            return viewport.entity_at_point_3d(
                entities,
                positions,
                pointer_x,
                pointer_y,
                width,
                height,
                scale,
                center,
                self._scene.view_mode,
                self._camera,
                hit_radius,
            )
        base_center_x_m, base_center_y_m = viewport.overview_view_center(entities, positions)
        center_x_m, center_y_m = self._view_center(base_center_x_m, base_center_y_m)
        scale = viewport.overview_canvas_scale(
            width,
            height,
            positions,
            base_center_x_m,
            base_center_y_m,
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

    def _base_view_center(self) -> tuple[float, float]:
        return viewport.body_view_center(
            self._scene.bodies,
            self._scene.view_mode,
            self._scene.selected_body_index,
            self._scene.selected_group_center,
            self._focused_fit_bounds if self._scene.using_focused_fit else None,
        )

    def _view_center(self, base_center_x_m: float, base_center_y_m: float) -> tuple[float, float]:
        return (
            base_center_x_m + self._pan_offset_m[0],
            base_center_y_m + self._pan_offset_m[1],
        )

    def _base_view_center_3d(self) -> tuple[float, float, float]:
        return viewport.body_view_center_3d(
            self._scene.bodies,
            self._scene.view_mode,
            self._scene.selected_body_index,
            self._scene.selected_group_center,
            self._focused_fit_bounds_3d if self._scene.using_focused_fit else None,
        )

    def _view_center_3d(
        self,
        base_center: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        return tuple(
            base_center[axis] + self._pan_offset_3d_m[axis]
            for axis in range(3)
        )

    def _current_canvas_scale(self, width: int, height: int) -> float | None:
        if self._render_mode == "3d":
            return self._current_canvas_scale_3d(width, height)
        if self._scene.using_system_overview:
            entities = self._scene.overview_entities
            positions = self._scene.overview_positions
            if not entities or len(positions) < len(entities):
                return None
            center_x_m, center_y_m = viewport.overview_view_center(entities, positions)
            return viewport.overview_canvas_scale(
                width,
                height,
                positions,
                center_x_m,
                center_y_m,
                self._zoom_factor,
                self._scene.view_mode,
            )
        if not self._scene.bodies or not self._scene.active_indices:
            return None
        center_x_m, center_y_m = self._base_view_center()
        return self._canvas_scale(width, height, center_x_m, center_y_m)

    def _current_canvas_scale_3d(self, width: int, height: int) -> float | None:
        if self._scene.using_system_overview:
            entities = self._scene.overview_entities
            positions = self._scene.overview_positions
            if not entities or len(positions) < len(entities):
                return None
            center = viewport.overview_view_center_3d(entities, positions)
            return viewport.overview_canvas_scale_3d(
                width,
                height,
                positions,
                center,
                self._zoom_factor,
                self._scene.view_mode,
            )
        if not self._scene.bodies or not self._scene.active_indices:
            return None
        return self._canvas_scale_3d(width, height, self._base_view_center_3d())

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

    def _canvas_scale_3d(
        self,
        width: int,
        height: int,
        center: tuple[float, float, float],
    ) -> float:
        return viewport.canvas_scale_3d(
            width,
            height,
            self._scene.bodies,
            self._scene.active_indices,
            center,
            self._zoom_factor,
            self._scene.view_mode,
            use_focused_bounds=self._scene.using_focused_fit,
            focused_bounds=self._focused_fit_bounds_3d,
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

    def _project_3d(
        self,
        position_m: Sequence[float],
        origin_x: float,
        origin_y: float,
        scale: float,
        center: tuple[float, float, float],
    ) -> viewport.ProjectedPoint3D:
        return viewport.project_3d(
            position_m,
            origin_x,
            origin_y,
            scale,
            center,
            self._scene.view_mode,
            self._camera,
            self._camera_basis,
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
