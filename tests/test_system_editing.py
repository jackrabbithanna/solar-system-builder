import math
import unittest

from src.constants import AU, G
from src.models import DataSource, FlybyData, ModelError, OrbitData, SolarSystem
from src.system_editing import (
    BodyStateInput,
    add_body,
    add_body_from_state,
    add_flyby_from_state,
    add_star_system,
    create_system,
    delete_body_cascade,
    delete_group_cascade,
    regenerate_flyby,
    update_body_from_state,
)


class SystemEditingTests(unittest.TestCase):
    def test_create_single_star_system_round_trips(self):
        system = create_system("Test System", "single_star")

        clone = SolarSystem.from_dict(system.to_dict())

        self.assertEqual(clone.name, "Test System")
        self.assertEqual(len(clone.bodies), 1)
        self.assertEqual(clone.bodies[0].kind, "star")
        self.assertEqual(clone.groups[0].body_ids, [clone.bodies[0].id])

    def test_create_binary_star_system_seeds_barycentric_state(self):
        system = create_system("Binary", "binary_star")

        self.assertEqual(len(system.bodies), 2)
        self.assertLess(system.bodies[0].position_m[0], 0.0)
        self.assertGreater(system.bodies[1].position_m[0], 0.0)
        self.assertLess(system.bodies[0].velocity_mps[1], 0.0)
        self.assertGreater(system.bodies[1].velocity_mps[1], 0.0)
        system.validate()

    def test_create_sol_system_has_horizons_compatible_sun(self):
        system = create_system("Sol Builder", "sol", epoch="2026-07-18 00:00:00")

        self.assertEqual(system.bodies[0].name, "Sun")
        self.assertEqual(system.bodies[0].data_source.catalog_id, "10")
        self.assertEqual(system.reference_frame.center_id, "500@10")
        self.assertTrue(system.reference_frame.horizons_compatible)
        self.assertIn("TDB", system.epoch)

    def test_create_system_accepts_explicit_primary_state(self):
        primary = BodyStateInput(
            name="Tau Ceti",
            kind="star",
            mass_kg=1.56e30,
            radius_m=5.5e8,
            position_m=(1.0, 2.0, 3.0),
            velocity_mps=(4.0, 5.0, 6.0),
            color="#ffcc88",
        )

        system = create_system("Tau Ceti", primary_state=primary)

        self.assertEqual(system.bodies[0].position_m, [1.0, 2.0, 3.0])
        self.assertEqual(system.bodies[0].velocity_mps, [4.0, 5.0, 6.0])
        self.assertEqual(system.bodies[0].state_origin, "cartesian")

    def test_add_star_system_creates_group_and_root_star(self):
        system = create_system("Root")

        group, star = add_star_system(system, "Neighbor")

        self.assertIn(group, system.groups)
        self.assertIn(star, system.bodies)
        self.assertEqual(group.body_ids, [star.id])
        self.assertIsNone(star.parent_id)
        system.validate()

    def test_add_planet_uses_circular_orbit_seed(self):
        system = create_system("Root")
        star = system.bodies[0]

        planet = add_body(system, "planet", "Planet", parent_id=star.id, orbit_radius_m=AU)

        expected_speed = math.sqrt(G * (star.mass_kg + planet.mass_kg) / AU)
        self.assertEqual(planet.parent_id, star.id)
        self.assertAlmostEqual(planet.position_m[0] - star.position_m[0], AU)
        self.assertAlmostEqual(planet.velocity_mps[1] - star.velocity_mps[1], expected_speed)
        system.validate()

    def test_add_body_from_state_preserves_complete_3d_state(self):
        system = create_system("Root")
        star = system.bodies[0]
        state = BodyStateInput(
            name="Inclined",
            kind="planet",
            mass_kg=3.0,
            radius_m=2.0,
            position_m=(11.0, 12.0, 13.0),
            velocity_mps=(21.0, 22.0, 23.0),
            color="#123456",
            parent_id=star.id,
            visible=False,
            trail_enabled=False,
        )

        body = add_body_from_state(system, state)

        self.assertEqual(body.position_m, [11.0, 12.0, 13.0])
        self.assertEqual(body.velocity_mps, [21.0, 22.0, 23.0])
        self.assertFalse(body.visible)
        self.assertFalse(body.trail_enabled)
        system.validate()

    def test_add_and_regenerate_flyby_preserves_encounter_inputs(self):
        system = create_system("Root")
        star = system.bodies[0]
        state = BodyStateInput(
            name="Visitor",
            kind="asteroid",
            mass_kg=1.0e16,
            radius_m=1.0e4,
            position_m=(0.0, 0.0, 0.0),
            velocity_mps=(0.0, 0.0, 0.0),
            color="#abcdef",
        )
        flyby = FlybyData(star.id, 2.0 * AU, 15_000.0, 20.0 * AU, 30.0, 10.0, 5.0)

        body = add_flyby_from_state(system, state, flyby)
        original_position = body.position_m[:]
        changed = FlybyData(star.id, 3.0 * AU, 15_000.0, 20.0 * AU, 30.0, 10.0, 5.0)
        regenerated = regenerate_flyby(system, body.id, changed)

        self.assertIsNone(regenerated.parent_id)
        self.assertEqual(regenerated.state_origin, "flyby")
        self.assertEqual(regenerated.flyby, changed)
        self.assertEqual(system.settings.simulation_scope, "full_nbody")
        self.assertGreater(regenerated.orbit.eccentricity, 1.0)
        self.assertNotEqual(regenerated.position_m, original_position)
        self.assertAlmostEqual(math.dist(regenerated.position_m, star.position_m), 20.0 * AU, delta=1.0)
        system.validate()

    def test_cartesian_edit_can_detach_flyby_metadata(self):
        system = create_system("Root")
        star = system.bodies[0]
        body = add_flyby_from_state(
            system,
            BodyStateInput(
                "Visitor",
                "planet",
                1.0e24,
                1.0e6,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                "#fff",
            ),
            FlybyData(star.id, AU, 10_000.0, 10.0 * AU),
        )

        updated = update_body_from_state(
            system,
            body.id,
            BodyStateInput(
                body.name,
                body.kind,
                body.mass_kg,
                body.radius_m,
                (1.0, 2.0, 3.0),
                (4.0, 5.0, 6.0),
                body.color,
            ),
            preserve_metadata=False,
        )

        self.assertIsNone(updated.parent_id)
        self.assertIsNone(updated.flyby)
        self.assertIsNone(updated.orbit)
        self.assertEqual(updated.state_origin, "cartesian")
        system.validate()

    def test_deleting_flyby_anchor_keeps_body_as_unbound_cartesian(self):
        system = create_system("Root")
        star = system.bodies[0]
        body = add_flyby_from_state(
            system,
            BodyStateInput(
                "Visitor",
                "asteroid",
                1.0e16,
                1.0e4,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                "#fff",
            ),
            FlybyData(star.id, AU, 10_000.0, 10.0 * AU),
        )

        delete_body_cascade(system, star.id)

        self.assertEqual(system.bodies, [body])
        self.assertIsNone(body.flyby)
        self.assertIsNone(body.orbit)
        self.assertEqual(body.state_origin, "cartesian")
        system.validate()

    def test_update_body_reparents_atomically_and_keeps_group_ownership(self):
        system = create_system("Root")
        first_star = system.bodies[0]
        second_star = add_body(system, "star", "Second", group_id=system.groups[0].id)
        planet = add_body(system, "planet", "Planet", parent_id=first_star.id)
        state = BodyStateInput(
            name="Renamed",
            kind="planet",
            mass_kg=4.0,
            radius_m=5.0,
            position_m=(6.0, 7.0, 8.0),
            velocity_mps=(9.0, 10.0, 11.0),
            color="#abcdef",
            parent_id=second_star.id,
            visible=False,
        )

        updated = update_body_from_state(system, planet.id, state)

        self.assertEqual(updated.id, planet.id)
        self.assertEqual(updated.parent_id, second_star.id)
        self.assertEqual(updated.position_m, [6.0, 7.0, 8.0])
        self.assertFalse(updated.visible)
        self.assertNotIn(updated.id, system.groups[0].body_ids)
        system.validate()

    def test_reclassifying_horizons_body_preserves_state_and_provenance(self):
        system = create_system("Root")
        star = system.bodies[0]
        body = add_body(system, "asteroid", "Sedna", parent_id=star.id)
        body.orbit = OrbitData(semi_major_axis_m=7.5e12, eccentricity=0.85)
        body.data_source = DataSource(
            source_name="JPL Horizons",
            source_url="https://ssd.jpl.nasa.gov/api/horizons.api?COMMAND=90377%3B",
            catalog_id="90377",
        )
        body.state_origin = "horizons"
        original_orbit = body.orbit
        original_source = body.data_source
        original_position = body.position_m[:]
        original_velocity = body.velocity_mps[:]

        updated = update_body_from_state(
            system,
            body.id,
            BodyStateInput(
                name=body.name,
                kind="dwarf planet",
                mass_kg=body.mass_kg,
                radius_m=body.radius_m,
                position_m=tuple(body.position_m),
                velocity_mps=tuple(body.velocity_mps),
                color=body.color,
                parent_id=body.parent_id,
                visible=body.visible,
                trail_enabled=body.trail_enabled,
            ),
        )

        self.assertEqual(updated.kind, "dwarf planet")
        self.assertEqual(updated.position_m, original_position)
        self.assertEqual(updated.velocity_mps, original_velocity)
        self.assertEqual(updated.orbit, original_orbit)
        self.assertEqual(updated.data_source, original_source)
        self.assertEqual(updated.state_origin, "horizons")
        system.validate()

    def test_reclassification_preserves_flyby_metadata(self):
        system = create_system("Root")
        star = system.bodies[0]
        body = add_flyby_from_state(
            system,
            BodyStateInput(
                "Visitor",
                "asteroid",
                1.0e16,
                1.0e4,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                "#fff",
            ),
            FlybyData(star.id, AU, 10_000.0, 10.0 * AU),
        )
        original_flyby = body.flyby
        original_orbit = body.orbit

        updated = update_body_from_state(
            system,
            body.id,
            BodyStateInput(
                body.name,
                "comet",
                body.mass_kg,
                body.radius_m,
                tuple(body.position_m),
                tuple(body.velocity_mps),
                body.color,
            ),
        )

        self.assertEqual(updated.kind, "comet")
        self.assertEqual(updated.flyby, original_flyby)
        self.assertEqual(updated.orbit, original_orbit)
        self.assertEqual(updated.state_origin, "flyby")
        system.validate()

    def test_reclassification_validates_child_moons_atomically(self):
        system = create_system("Root")
        star = system.bodies[0]
        planet = add_body(system, "planet", "Planet", parent_id=star.id)
        moon = add_body(system, "moon", "Moon", parent_id=planet.id)

        dwarf_planet = update_body_from_state(
            system,
            planet.id,
            BodyStateInput(
                planet.name,
                "dwarf planet",
                planet.mass_kg,
                planet.radius_m,
                tuple(planet.position_m),
                tuple(planet.velocity_mps),
                planet.color,
                parent_id=planet.parent_id,
            ),
        )

        self.assertEqual(dwarf_planet.kind, "dwarf planet")
        self.assertEqual(moon.parent_id, dwarf_planet.id)
        before = system.to_dict()
        with self.assertRaisesRegex(ModelError, "moon parent"):
            update_body_from_state(
                system,
                dwarf_planet.id,
                BodyStateInput(
                    dwarf_planet.name,
                    "asteroid",
                    dwarf_planet.mass_kg,
                    dwarf_planet.radius_m,
                    tuple(dwarf_planet.position_m),
                    tuple(dwarf_planet.velocity_mps),
                    dwarf_planet.color,
                    parent_id=dwarf_planet.parent_id,
                ),
            )
        self.assertEqual(system.to_dict(), before)

    def test_invalid_body_update_leaves_original_unchanged(self):
        system = create_system("Root")
        star = system.bodies[0]
        planet = add_body(system, "planet", "Planet", parent_id=star.id)
        moon = add_body(system, "moon", "Moon", parent_id=planet.id)
        before = system.to_dict()
        invalid = BodyStateInput(
            name=moon.name,
            kind="moon",
            mass_kg=moon.mass_kg,
            radius_m=moon.radius_m,
            position_m=tuple(moon.position_m),
            velocity_mps=tuple(moon.velocity_mps),
            color=moon.color,
            parent_id=star.id,
        )

        with self.assertRaisesRegex(ModelError, "moon parent"):
            update_body_from_state(system, moon.id, invalid)

        self.assertEqual(system.to_dict(), before)

    def test_add_moon_requires_planet_parent(self):
        system = create_system("Root")
        star = system.bodies[0]

        with self.assertRaisesRegex(ModelError, "moon parent"):
            add_body(system, "moon", "Bad Moon", parent_id=star.id)

        planet = add_body(system, "planet", "Planet", parent_id=star.id, orbit_radius_m=AU)
        moon = add_body(system, "moon", "Moon", parent_id=planet.id)

        self.assertEqual(moon.parent_id, planet.id)
        system.validate()

    def test_delete_body_cascade_cleans_groups(self):
        system = create_system("Root")
        star = system.bodies[0]
        planet = add_body(system, "planet", "Planet", parent_id=star.id)
        moon = add_body(system, "moon", "Moon", parent_id=planet.id)

        deleted = delete_body_cascade(system, planet.id)

        self.assertEqual(set(deleted), {planet.id, moon.id})
        self.assertNotIn(planet.id, {body.id for body in system.bodies})
        self.assertNotIn(moon.id, {body.id for body in system.bodies})
        system.validate()

    def test_delete_group_cascade_removes_descendant_group_bodies(self):
        system = create_system("Root")
        group, star = add_star_system(system, "Nested", parent_group_id=system.groups[0].id)
        planet = add_body(system, "planet", "Nested Planet", parent_id=star.id)

        deleted_groups, deleted_bodies = delete_group_cascade(system, group.id)

        self.assertEqual(deleted_groups, [group.id])
        self.assertEqual(set(deleted_bodies), {star.id, planet.id})
        self.assertNotIn(group.id, {item.id for item in system.groups})
        self.assertNotIn(star.id, {body.id for body in system.bodies})
        self.assertNotIn(planet.id, {body.id for body in system.bodies})
        system.validate()

    def test_cannot_delete_last_body(self):
        system = create_system("Root")

        with self.assertRaisesRegex(ModelError, "at least one body"):
            delete_body_cascade(system, system.bodies[0].id)


if __name__ == "__main__":
    unittest.main()
