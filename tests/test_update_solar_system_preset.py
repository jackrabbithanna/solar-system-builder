import copy
import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "update_solar_system_preset.py"
)
SPEC = importlib.util.spec_from_file_location("update_solar_system_preset", SCRIPT_PATH)
update_solar_system_preset = importlib.util.module_from_spec(SPEC)
sys.modules["update_solar_system_preset"] = update_solar_system_preset
SPEC.loader.exec_module(update_solar_system_preset)


SAMPLE_RESULT = """
Target body name: Mars (499)
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000, -2.014, 3.125, 0.042, -0.001, -0.002, 0.003
$$EOE
"""


class UpdateSolarSystemPresetTests(unittest.TestCase):
    def test_parse_horizons_vector_converts_to_si(self):
        vector = update_solar_system_preset.parse_horizons_vector(SAMPLE_RESULT)

        self.assertSequenceEqual(
            [round(component, 6) for component in vector.position_m],
            [-2014.0, 3125.0, 42.0],
        )
        self.assertSequenceEqual(vector.velocity_mps, [-1.0, -2.0, 3.0])

    def test_apply_vectors_preserves_non_vector_fields(self):
        preset = {
            "schema_version": 1,
            "id": "builtin-solar-system",
            "name": "Solar System",
            "epoch": "old",
            "description": "old",
            "bodies": [
                {
                    "id": body_id,
                    "name": body_id.title(),
                    "kind": "star" if body_id == "sun" else "planet",
                    "mass_kg": index + 1,
                    "radius_m": index + 2,
                    "position_m": [0, 0, 0],
                    "velocity_mps": [0, 0, 0],
                    "color": f"#{index:06x}",
                }
                for index, body_id in enumerate(update_solar_system_preset.TARGETS)
            ],
        }
        original = copy.deepcopy(preset)
        vectors = {
            body_id: update_solar_system_preset.StateVector(
                [float(index), float(index + 1), float(index + 2)],
                [float(index + 3), float(index + 4), float(index + 5)],
            )
            for index, body_id in enumerate(update_solar_system_preset.TARGETS)
        }

        updated = update_solar_system_preset.apply_vectors(
            preset, vectors, "2026-06-14 00:00:00"
        )

        self.assertEqual(preset, original)
        self.assertIn("2026-06-14 00:00:00 TDB", updated["epoch"])
        for body in updated["bodies"]:
            original_body = next(item for item in original["bodies"] if item["id"] == body["id"])
            self.assertEqual(body["name"], original_body["name"])
            self.assertEqual(body["kind"], original_body["kind"])
            self.assertEqual(body["mass_kg"], original_body["mass_kg"])
            self.assertEqual(body["radius_m"], original_body["radius_m"])
            self.assertEqual(body["color"], original_body["color"])
            self.assertNotEqual(body["position_m"], original_body["position_m"])
            self.assertNotEqual(body["velocity_mps"], original_body["velocity_mps"])

    def test_apply_vectors_accepts_dwarf_planet_target_set(self):
        targets = update_solar_system_preset.DWARF_PLANET_TARGETS
        preset = {
            "schema_version": 1,
            "id": "builtin-dwarf-planets",
            "name": "Dwarf Planets",
            "epoch": "old",
            "description": "old",
            "bodies": [
                {
                    "id": body_id,
                    "name": body_id.title(),
                    "kind": "dwarf planet",
                    "mass_kg": index + 1,
                    "radius_m": index + 2,
                    "position_m": [0, 0, 0],
                    "velocity_mps": [0, 0, 0],
                    "color": f"#{index:06x}",
                }
                for index, body_id in enumerate(targets)
            ],
        }
        vectors = {
            body_id: update_solar_system_preset.StateVector([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
            for body_id in targets
        }

        updated = update_solar_system_preset.apply_vectors(
            preset,
            vectors,
            "2026-06-14 00:00:00",
            targets,
            "updated description",
        )

        self.assertEqual(updated["description"], "updated description")
        self.assertEqual(len(updated["bodies"]), len(targets))
        self.assertEqual(updated["bodies"][-1]["id"], "orcus")
        self.assertEqual(updated["bodies"][-1]["position_m"], [1.0, 2.0, 3.0])

    def test_dwarf_planet_targets_use_small_body_disambiguation(self):
        targets = update_solar_system_preset.DWARF_PLANET_TARGETS

        self.assertEqual(targets["ceres"], "1;")
        self.assertEqual(targets["eris"], "136199;")
        self.assertNotIn("sedna", targets)

    def test_apply_vectors_requires_all_targets(self):
        preset = {
            "schema_version": 1,
            "id": "builtin-solar-system",
            "name": "Solar System",
            "bodies": [],
        }

        with self.assertRaisesRegex(ValueError, "Preset is missing expected bodies"):
            update_solar_system_preset.apply_vectors(preset, {}, "2026-06-14 00:00:00")


if __name__ == "__main__":
    unittest.main()
