import unittest

from src.models import Body, ModelError, SolarSystem
from src.presets import load_builtin_solar_system, load_builtin_solar_systems


class ModelTests(unittest.TestCase):
    def test_builtin_preset_round_trips(self):
        system = load_builtin_solar_system()

        clone = SolarSystem.from_dict(system.to_dict())

        self.assertEqual(system.name, clone.name)
        self.assertGreaterEqual(len(clone.bodies), 9)

    def test_all_builtin_presets_round_trip(self):
        systems = load_builtin_solar_systems()

        self.assertEqual([system.id for system in systems], ["builtin-solar-system", "builtin-dwarf-planets"])
        self.assertEqual([system.name for system in systems], ["Solar System", "Dwarf Planets"])
        for system in systems:
            clone = SolarSystem.from_dict(system.to_dict())
            self.assertEqual(system.id, clone.id)
            self.assertGreaterEqual(len(clone.bodies), 1)

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


if __name__ == "__main__":
    unittest.main()
