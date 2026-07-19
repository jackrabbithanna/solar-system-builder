import math
import unittest

import numpy as np

from src import viewport
from src.constants import AU
from src.models import Body
from src.scales import OverviewEntity


class ViewportTests(unittest.TestCase):
    def test_projection_maps_model_coordinates_to_canvas_coordinates(self):
        x, y = viewport.project(AU, AU, 100.0, 100.0, 1.0 / AU, 0.0, 0.0, "fit_system")

        self.assertAlmostEqual(x, 101.0)
        self.assertAlmostEqual(y, 99.0)

    def test_3d_projection_uses_xyz_and_reports_camera_depth(self):
        camera = viewport.Camera3D(azimuth_deg=-90.0, elevation_deg=90.0)

        point = viewport.project_3d(
            (AU, AU, 2.0 * AU),
            100.0,
            100.0,
            1.0 / AU,
            (0.0, 0.0, 0.0),
            "fit_system",
            camera,
        )

        self.assertAlmostEqual(point.x, 101.0)
        self.assertAlmostEqual(point.y, 99.0)
        self.assertAlmostEqual(point.depth, 2.0 * AU)

    def test_orthographic_projection_preserves_size_across_depth(self):
        camera = viewport.Camera3D(azimuth_deg=-90.0, elevation_deg=90.0)

        near = viewport.project_3d((AU, 0.0, AU), 0.0, 0.0, 1.0 / AU, (0.0, 0.0, 0.0), "fit_system", camera)
        far = viewport.project_3d((AU, 0.0, -AU), 0.0, 0.0, 1.0 / AU, (0.0, 0.0, 0.0), "fit_system", camera)

        self.assertAlmostEqual(near.x, far.x)
        self.assertAlmostEqual(near.y, far.y)
        self.assertGreater(near.depth, far.depth)

    def test_3d_log_projection_compresses_full_radial_distance(self):
        camera = viewport.Camera3D(azimuth_deg=0.0, elevation_deg=0.0)

        point = viewport.project_3d(
            (0.0, 0.0, AU),
            0.0,
            0.0,
            1.0 / AU,
            (0.0, 0.0, 0.0),
            "log_overview",
            camera,
        )

        self.assertAlmostEqual(point.y, -math.log(2.0))

    def test_batch_3d_projection_matches_scalar_projection(self):
        camera = viewport.Camera3D()
        positions = np.array([[AU, 2.0 * AU, 3.0 * AU], [-AU, 0.0, AU]])

        batch = viewport.project_points_3d(
            positions,
            100.0,
            80.0,
            1.0 / AU,
            (0.0, 0.0, 0.0),
            "log_overview",
            camera,
        )
        scalar = [
            viewport.project_3d(
                position,
                100.0,
                80.0,
                1.0 / AU,
                (0.0, 0.0, 0.0),
                "log_overview",
                camera,
            )
            for position in positions
        ]

        np.testing.assert_allclose(
            batch,
            [(point.x, point.y, point.depth) for point in scalar],
        )

    def test_zoom_clamping_honors_limits(self):
        self.assertEqual(viewport.clamp_zoom_factor(0.1), 1.0)
        self.assertEqual(viewport.clamp_zoom_factor(128.0), 64.0)
        self.assertEqual(viewport.clamp_zoom_factor(4.0), 4.0)

    def test_linear_pan_delta_keeps_dragged_view_center_under_pointer(self):
        delta = viewport.pan_center_delta(20.0, -10.0, 2.0, "fit_system")

        self.assertEqual(delta, (-10.0, -5.0))
        x, y = viewport.project(0.0, 0.0, 100.0, 100.0, 2.0, *delta, "fit_system")
        self.assertAlmostEqual(x, 120.0)
        self.assertAlmostEqual(y, 90.0)

    def test_log_pan_delta_keeps_dragged_view_center_under_pointer(self):
        scale = 100.0 / AU
        delta = viewport.pan_center_delta(120.0, -60.0, scale, "log_overview")

        x, y = viewport.project(0.0, 0.0, 200.0, 200.0, scale, *delta, "log_overview")
        self.assertAlmostEqual(x, 320.0)
        self.assertAlmostEqual(y, 140.0)

    def test_pan_delta_ignores_invalid_scale(self):
        self.assertEqual(viewport.pan_center_delta(20.0, 10.0, 0.0, "fit_system"), (0.0, 0.0))

    def test_3d_pan_moves_in_the_camera_plane(self):
        camera = viewport.Camera3D(azimuth_deg=-90.0, elevation_deg=90.0)

        delta = viewport.pan_center_delta_3d(20.0, -10.0, 2.0, "fit_system", camera)

        self.assertAlmostEqual(delta[0], -10.0)
        self.assertAlmostEqual(delta[1], -5.0)
        self.assertAlmostEqual(delta[2], 0.0)

    def test_camera_drag_wraps_azimuth_and_clamps_elevation(self):
        camera = viewport.camera_after_drag(viewport.Camera3D(5.0, 80.0), 20.0, 100.0)

        self.assertAlmostEqual(camera.azimuth_deg, 358.0)
        self.assertEqual(camera.elevation_deg, viewport.CAMERA_ELEVATION_LIMIT_DEG)

    def test_relative_trail_points_are_anchored_to_current_reference(self):
        self.assertEqual(
            viewport.trail_point_in_system_frame(2.0, -3.0, (10.0, 20.0)),
            (12.0, 17.0),
        )
        self.assertEqual(
            viewport.trail_point_in_system_frame(2.0, -3.0, None),
            (2.0, -3.0),
        )

    def test_body_view_centers_follow_selected_parent_and_mass_center(self):
        bodies = _bodies()

        self.assertEqual(viewport.body_view_center(bodies, "follow_selected", 1), (0.0, 0.0))
        self.assertEqual(viewport.body_view_center(bodies, "follow_selected", 0), (0.0, 0.0))
        center = viewport.body_view_center(bodies, "fit_system", 0)
        self.assertAlmostEqual(center[0], AU / 3.0)
        self.assertAlmostEqual(center[1], 0.0)

    def test_overview_center_and_scale_are_stable(self):
        entities = [
            OverviewEntity("a", "A", "system", 3.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff"),
            OverviewEntity("b", "B", "system", 1.0, [4.0 * AU, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff"),
        ]

        center = viewport.overview_view_center(entities, [entity.position_m for entity in entities])
        scale = viewport.overview_canvas_scale(400, 300, [entity.position_m for entity in entities], *center, 1.0, "fit_system")

        self.assertAlmostEqual(center[0], AU)
        self.assertGreater(scale, 0.0)

    def test_focused_bounds_fit_compact_system_without_au_floor(self):
        bodies = _bodies()
        bodies[1].position_m = [1.0e8, 2.0e8, 0.0]

        bounds = viewport.focused_fit_bounds(bodies, [0, 1])
        scale = viewport.canvas_scale(
            900,
            300,
            bodies,
            [0, 1],
            *bounds.center,
            1.0,
            "follow_selected",
            use_focused_bounds=True,
            focused_bounds=bounds,
        )

        self.assertAlmostEqual(bounds.center[0], 1.0e8 / 3.0)
        self.assertAlmostEqual(bounds.center[1], 2.0e8 / 3.0)
        self.assertEqual(bounds.half_width_m, bounds.half_height_m)
        self.assertAlmostEqual(scale, 300.0 * 0.45 / bounds.half_height_m)

    def test_focused_fit_is_rotation_invariant(self):
        bodies = _bodies()
        bodies[0].mass_kg = 1.0
        bodies[1].mass_kg = 1.0
        bodies[0].position_m = [-AU, 0.0, 0.0]
        bodies[1].position_m = [AU, 0.0, 0.0]
        horizontal = viewport.focused_fit_bounds(bodies, [0, 1])

        bodies[0].position_m = [0.0, -AU, 0.0]
        bodies[1].position_m = [0.0, AU, 0.0]
        vertical = viewport.focused_fit_bounds(bodies, [0, 1])

        self.assertEqual(horizontal.half_width_m, vertical.half_width_m)
        self.assertEqual(horizontal.half_height_m, vertical.half_height_m)

    def test_3d_focused_fit_includes_out_of_plane_extent(self):
        bodies = _bodies()
        bodies[0].mass_kg = 1.0
        bodies[1].mass_kg = 1.0
        bodies[0].position_m = [0.0, 0.0, -AU]
        bodies[1].position_m = [0.0, 0.0, AU]

        bounds = viewport.focused_fit_bounds_3d(bodies, [0, 1])
        scale = viewport.canvas_scale_3d(
            400,
            300,
            bodies,
            [0, 1],
            bounds.center,
            1.0,
            "follow_selected",
            use_focused_bounds=True,
            focused_bounds=bounds,
        )

        self.assertEqual(bounds.center, (0.0, 0.0, 0.0))
        self.assertGreater(bounds.radius_m, AU)
        self.assertAlmostEqual(scale, 300.0 * 0.45 / bounds.radius_m)

    def test_focused_extent_expands_immediately_and_contracts_gradually(self):
        self.assertEqual(viewport.stabilize_focused_extent(100.0, 110.0), 110.0)
        self.assertEqual(viewport.stabilize_focused_extent(100.0, 90.0), 100.0)
        self.assertEqual(viewport.stabilize_focused_extent(100.0, 80.0), 99.0)

    def test_planet_sized_extent_changes_stay_inside_focused_fit_deadband(self):
        retained_extent = 100.0

        for required_extent in (94.0, 100.0, 91.0, 98.0):
            retained_extent = viewport.stabilize_focused_extent(retained_extent, required_extent)

        self.assertEqual(retained_extent, 100.0)

    def test_overview_inset_geometry_and_containment(self):
        rect = viewport.overview_inset_rect(1000, 600)

        self.assertEqual(rect, viewport.InsetRect(12.0, 428.0, 240.0, 160.0))
        self.assertTrue(viewport.point_in_rect(20.0, 500.0, rect))
        self.assertFalse(viewport.point_in_rect(500.0, 500.0, rect))

    def test_body_and_entity_hit_tests_select_closest_visible_target(self):
        bodies = _bodies()
        scale = 1.0 / AU

        selected = viewport.body_index_at_point(
            bodies,
            [0, 1],
            101.0,
            100.0,
            200,
            200,
            scale,
            0.0,
            0.0,
            "fit_system",
            lambda _body: 4.0,
        )

        self.assertEqual(selected, 1)

        entities = [
            OverviewEntity("a", "A", "system", 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff"),
            OverviewEntity("b", "B", "system", 1.0, [AU, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff"),
        ]
        entity = viewport.entity_at_point(
            entities,
            [item.position_m for item in entities],
            101.0,
            100.0,
            200,
            200,
            scale,
            0.0,
            0.0,
            "fit_system",
            8.0,
        )

        self.assertEqual(entity.id, "b")

    def test_3d_hit_test_selects_frontmost_overlapping_body(self):
        bodies = _bodies()
        bodies[0].position_m = [0.0, 0.0, -AU]
        bodies[1].position_m = [0.0, 0.0, AU]

        selected = viewport.body_index_at_point_3d(
            bodies,
            [0, 1],
            100.0,
            100.0,
            200,
            200,
            1.0 / AU,
            (0.0, 0.0, 0.0),
            "fit_system",
            viewport.Camera3D(azimuth_deg=-90.0, elevation_deg=90.0),
            lambda _body: 4.0,
        )

        self.assertEqual(selected, 1)

    def test_empty_systems_return_safe_defaults(self):
        self.assertEqual(viewport.body_view_center([], "fit_system", 0), (0.0, 0.0))
        self.assertIsNone(
            viewport.body_index_at_point([], [], 0.0, 0.0, 0, 0, 1.0, 0.0, 0.0, "fit_system", lambda _body: 1.0)
        )


def _bodies() -> list[Body]:
    return [
        Body(
            id="sun",
            name="Sun",
            kind="star",
            mass_kg=2.0,
            radius_m=1.0,
            position_m=[0.0, 0.0, 0.0],
            velocity_mps=[0.0, 0.0, 0.0],
            color="#ffff00",
        ),
        Body(
            id="earth",
            name="Earth",
            kind="planet",
            mass_kg=1.0,
            radius_m=1.0,
            position_m=[AU, 0.0, 0.0],
            velocity_mps=[0.0, 0.0, 0.0],
            color="#00ff00",
            parent_id="sun",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
