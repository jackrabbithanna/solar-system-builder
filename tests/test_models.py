import unittest

from src.models import Body, ModelError, SCHEMA_VERSION, SolarSystem
from src.presets import load_builtin_solar_system, load_builtin_solar_systems


class ModelTests(unittest.TestCase):
    def test_builtin_preset_round_trips(self):
        system = load_builtin_solar_system()

        clone = SolarSystem.from_dict(system.to_dict())

        self.assertEqual(system.name, clone.name)
        self.assertEqual(clone.schema_version, SCHEMA_VERSION)
        self.assertGreaterEqual(len(clone.bodies), 11)
        self.assertIn("ceres", {body.id for body in clone.bodies})
        self.assertIn("pluto", {body.id for body in clone.bodies})
        self.assertEqual(
            {body.parent_id for body in clone.bodies if body.kind != "star"},
            {"sun"},
        )

    def test_all_builtin_presets_round_trip(self):
        systems = load_builtin_solar_systems()

        self.assertEqual(
            [system.id for system in systems],
            ["builtin-solar-system", "builtin-dwarf-planets", "builtin-binary-system"],
        )
        self.assertEqual([system.name for system in systems], ["Solar System", "Dwarf Planets", "Binary System"])
        for system in systems:
            clone = SolarSystem.from_dict(system.to_dict())
            self.assertEqual(system.id, clone.id)
            self.assertGreaterEqual(len(clone.bodies), 1)
            body_ids = {body.id for body in clone.bodies}
            for body in clone.bodies:
                if body.parent_id is not None:
                    self.assertIn(body.parent_id, body_ids)

        dwarf_planets = systems[1]
        dwarf_body_ids = {body.id for body in dwarf_planets.bodies}
        self.assertNotIn("earth", dwarf_body_ids)
        self.assertNotIn("jupiter", dwarf_body_ids)
        self.assertNotIn("saturn", dwarf_body_ids)
        self.assertNotIn("uranus", dwarf_body_ids)
        self.assertNotIn("ceres", dwarf_body_ids)

    def test_invalid_body_mass_fails(self):
        with self.assertRaises(ModelError):
            Body(
                name="Invalid",
                kind="planet",
                mass_kg=0,
                radius_m=1,
                position_m=[0, 0, 0],
                velocity_mps=[0, 0, 0],
                color="#ffffff",
            ).validate()

    def test_v1_data_migrates_planets_to_first_star(self):
        data = _sample_system_data(schema_version=1)
        for body in data["bodies"]:
            body.pop("parent_id", None)

        system = SolarSystem.from_dict(data)

        self.assertEqual(system.schema_version, SCHEMA_VERSION)
        self.assertIsNone(system.bodies[0].parent_id)
        self.assertEqual(system.bodies[1].parent_id, "sun")

    def test_missing_parent_fails(self):
        data = _sample_system_data()
        data["bodies"][1]["parent_id"] = "missing"

        with self.assertRaisesRegex(ModelError, "parent_id missing does not exist"):
            SolarSystem.from_dict(data)

    def test_self_parent_fails(self):
        data = _sample_system_data()
        data["bodies"][1]["parent_id"] = "earth"

        with self.assertRaisesRegex(ModelError, "cannot parent itself"):
            SolarSystem.from_dict(data)

    def test_parent_cycle_fails(self):
        data = _sample_system_data()
        data["bodies"][0]["parent_id"] = "earth"

        with self.assertRaisesRegex(ModelError, "parent cycle"):
            SolarSystem.from_dict(data)

    def test_duplicate_remaps_parent_ids(self):
        system = SolarSystem.from_dict(_sample_system_data())

        duplicate = system.duplicate("Copy")

        self.assertNotEqual(system.id, duplicate.id)
        duplicate_ids = {body.id for body in duplicate.bodies}
        original_ids = {body.id for body in system.bodies}
        self.assertTrue(duplicate_ids.isdisjoint(original_ids))
        star = next(body for body in duplicate.bodies if body.kind == "star")
        planet = next(body for body in duplicate.bodies if body.kind == "planet")
        self.assertEqual(planet.parent_id, star.id)


def _sample_system_data(schema_version: int = SCHEMA_VERSION):
    return {
        "schema_version": schema_version,
        "id": "sample-system",
        "name": "Sample",
        "epoch": "",
        "bodies": [
            {
                "id": "sun",
                "name": "Sun",
                "kind": "star",
                "mass_kg": 1.0,
                "radius_m": 1.0,
                "position_m": [0.0, 0.0, 0.0],
                "velocity_mps": [0.0, 0.0, 0.0],
                "color": "#ffffff",
            },
            {
                "id": "earth",
                "name": "Earth",
                "kind": "planet",
                "mass_kg": 1.0,
                "radius_m": 1.0,
                "position_m": [1.0, 0.0, 0.0],
                "velocity_mps": [0.0, 1.0, 0.0],
                "color": "#0000ff",
                "parent_id": "sun",
            },
        ],
    }


if __name__ == "__main__":
    unittest.main()
