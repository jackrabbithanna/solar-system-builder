import unittest

from src import viewport
from src.constants import AU
from src.models import Body
from src.scales import OverviewEntity, focused_canvas_bounds


class ViewportTests(unittest.TestCase):
    def test_projection_maps_model_coordinates_to_canvas_coordinates(self):
        x, y = viewport.project(AU, AU, 100.0, 100.0, 1.0 / AU, 0.0, 0.0, "fit_system")

        self.assertAlmostEqual(x, 101.0)
        self.assertAlmostEqual(y, 99.0)

    def test_zoom_clamping_honors_limits(self):
        self.assertEqual(viewport.clamp_zoom_factor(0.1), 1.0)
        self.assertEqual(viewport.clamp_zoom_factor(128.0), 64.0)
        self.assertEqual(viewport.clamp_zoom_factor(4.0), 4.0)

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

    def test_focused_bounds_fit_compact_asymmetric_system_without_au_floor(self):
        bodies = _bodies()
        bodies[1].position_m = [1.0e8, 2.0e8, 0.0]

        bounds = focused_canvas_bounds(bodies, [0, 1])
        scale = viewport.canvas_scale(
            900,
            300,
            bodies,
            [0, 1],
            *bounds.center,
            1.0,
            "follow_selected",
            use_focused_bounds=True,
        )

        self.assertEqual(bounds.center, (5.0e7, 1.0e8))
        self.assertEqual(bounds.half_width_m, 5.0e7)
        self.assertEqual(bounds.half_height_m, 1.0e8)
        self.assertAlmostEqual(scale, 300.0 * 0.45 / 1.0e8)

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
