import importlib.util
import json
import sys
import unittest
from pathlib import Path

from src.constants import DAY
from src.models import Body, ModelError, SCHEMA_VERSION, SolarSystem
from src.presets import load_builtin_solar_system, load_builtin_solar_systems

ALPHA_CENTAURI_GENERATOR_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "generate_alpha_centauri_preset.py"
)


class ModelTests(unittest.TestCase):
    def test_builtin_preset_round_trips(self):
        system = load_builtin_solar_system()

        clone = SolarSystem.from_dict(system.to_dict())

        self.assertEqual(system.name, clone.name)
        self.assertEqual(clone.schema_version, SCHEMA_VERSION)
        self.assertEqual(clone.settings.accuracy_profile, "balanced")
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
        self.assertEqual([system.name for system in systems], ["Solar System", "Dwarf Planets", "Alpha Centauri"])
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
        self.assertEqual(system.settings.visible_step_s, DAY)

    def test_v2_data_migrates_default_settings(self):
        data = _sample_system_data(schema_version=2)
        system = SolarSystem.from_dict(data)

        self.assertEqual(system.schema_version, SCHEMA_VERSION)
        self.assertEqual(system.settings.visible_step_s, DAY)
        self.assertEqual(system.settings.distance_unit, "AU")

    def test_dwarf_planets_preset_gets_large_step_default(self):
        system = next(
            item
            for item in load_builtin_solar_systems()
            if item.id == "builtin-dwarf-planets"
        )

        self.assertEqual(system.settings.visible_step_s, 90.0 * DAY)
        self.assertEqual(system.settings.trail_sample_interval_s, 90.0 * DAY)

    def test_alpha_centauri_generator_matches_preset(self):
        spec = importlib.util.spec_from_file_location(
            "generate_alpha_centauri_preset",
            ALPHA_CENTAURI_GENERATOR_PATH,
        )
        generate_alpha_centauri_preset = importlib.util.module_from_spec(spec)
        sys.modules["generate_alpha_centauri_preset"] = generate_alpha_centauri_preset
        spec.loader.exec_module(generate_alpha_centauri_preset)
        preset_path = Path(__file__).resolve().parent.parent / "src" / "presets" / "binary_system.json"

        generated = generate_alpha_centauri_preset.build_preset()
        current = json.loads(preset_path.read_text(encoding="utf-8"))

        self.assertEqual(generated, current)

    def test_settings_round_trip(self):
        data = _sample_system_data()
        data["settings"] = {
            "visible_step_s": 10.0 * DAY,
            "accuracy_profile": "fast",
            "distance_unit": "kAU",
            "view_mode": "log_overview",
            "trail_sample_interval_s": 5.0 * DAY,
        }

        system = SolarSystem.from_dict(data)
        clone = SolarSystem.from_dict(system.to_dict())

        self.assertEqual(clone.settings.visible_step_s, 10.0 * DAY)
        self.assertEqual(clone.settings.accuracy_profile, "fast")
        self.assertEqual(clone.settings.distance_unit, "kAU")
        self.assertEqual(clone.settings.view_mode, "log_overview")
        self.assertEqual(clone.settings.trail_sample_interval_s, 5.0 * DAY)

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
