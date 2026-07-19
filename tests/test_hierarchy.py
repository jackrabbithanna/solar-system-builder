import unittest

from src import hierarchy
from src.constants import AU
from src.models import Body, SystemGroup
from src.presets import load_builtin_solar_systems


class HierarchyTests(unittest.TestCase):
    def test_body_only_system_preserves_parent_child_order(self):
        bodies = _sample_bodies()

        order = hierarchy.body_list_order(bodies)

        self.assertEqual([bodies[index].id for index in order], ["alpha", "sun", "earth", "moon"])

    def test_grouped_system_produces_stable_group_and_body_rows(self):
        bodies = _sample_bodies()
        groups = [
            SystemGroup(id="sol", name="Sol", kind="planetary_system", body_ids=["sun"]),
            SystemGroup(id="nearby", name="Nearby", kind="stellar_system", body_ids=["alpha"]),
        ]

        rows = hierarchy.body_list_rows(bodies, groups)

        self.assertEqual(
            rows,
            [
                ("group", "nearby"),
                ("body", 3),
                ("group", "sol"),
                ("body", 0),
                ("body", 1),
                ("body", 2),
            ],
        )

    def test_nested_groups_compute_expected_depth(self):
        groups = [
            SystemGroup(id="root", name="Root", kind="system"),
            SystemGroup(id="child", name="Child", kind="system", parent_group_id="root"),
            SystemGroup(id="grandchild", name="Grandchild", kind="system", parent_group_id="child"),
        ]

        self.assertEqual(hierarchy.group_depth(groups, groups[0]), 0)
        self.assertEqual(hierarchy.group_depth(groups, groups[1]), 1)
        self.assertEqual(hierarchy.group_depth(groups, groups[2]), 2)
        self.assertEqual(hierarchy.descendant_group_ids(groups, "root"), {"root", "child", "grandchild"})
        self.assertEqual(
            hierarchy.descendant_group_ids(groups, "root", include_self=False),
            {"child", "grandchild"},
        )

    def test_relationship_labels_cover_orbits_and_nearest_stars(self):
        bodies = _sample_bodies()

        self.assertEqual(hierarchy.body_relationship_label(bodies, bodies[1]), "planet - orbits Sun - 1.00 AU")
        self.assertEqual(hierarchy.body_relationship_label(bodies, bodies[2]), "moon - orbits Earth - 0.0000 AU")
        self.assertEqual(hierarchy.body_relationship_label(bodies, bodies[0]), "star - nearest star 2.00 AU")

    def test_group_lookup_includes_descendant_body_parent_chains(self):
        system = next(item for item in load_builtin_solar_systems() if item.id == "builtin-binary-system")

        body_ids = {
            system.bodies[index].id
            for index in hierarchy.body_indices_for_group(
                system.bodies,
                system.groups,
                "alpha-centauri-system",
            )
        }

        self.assertIn("alpha-centauri-a-candidate", body_ids)
        self.assertIn("proxima-centauri-b", body_ids)
        self.assertIn("proxima-centauri-c-candidate", body_ids)

    def test_group_center_retains_all_three_coordinates(self):
        bodies = _sample_bodies()
        bodies[0].position_m[2] = 3.0
        bodies[1].position_m[2] = 6.0
        bodies[2].position_m[2] = 9.0
        groups = [
            SystemGroup(
                id="sol",
                name="Sol",
                kind="planetary_system",
                body_ids=["sun", "earth"],
            )
        ]

        center = hierarchy.group_center(bodies, groups, "sol")

        self.assertAlmostEqual(center[0], AU / 2.0 + 0.25)
        self.assertEqual(center[1:], (0.0, 5.25))


def _sample_bodies() -> list[Body]:
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
        Body(
            id="moon",
            name="Moon",
            kind="moon",
            mass_kg=1.0,
            radius_m=1.0,
            position_m=[AU + 1.0, 0.0, 0.0],
            velocity_mps=[0.0, 0.0, 0.0],
            color="#cccccc",
            parent_id="earth",
        ),
        Body(
            id="alpha",
            name="Alpha",
            kind="star",
            mass_kg=1.0,
            radius_m=1.0,
            position_m=[2.0 * AU, 0.0, 0.0],
            velocity_mps=[0.0, 0.0, 0.0],
            color="#ffffff",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
