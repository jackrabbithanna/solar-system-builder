import copy
import importlib.util
import sys
import unittest
from pathlib import Path

from src.constants import AU, DAY
from src.models import SCHEMA_VERSION


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

SAMPLE_ELEMENTS_RESULT = """
Target body name: Earth (399)
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000,  1.585631189570606E-02,  9.850437990796771E-01,  3.324222762500364E-03,  1.784280325590773E+02,  2.860556274903796E+02,  2.461045537895458E+06,  9.842585160222173E-01,  1.574440636358304E+02,  1.581284844924016E+02,  1.000914613370246E+00,  1.016785427660814E+00,  3.657575668787750E+02,
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

    def test_parse_horizons_elements_converts_to_si(self):
        elements = update_solar_system_preset.parse_horizons_elements(SAMPLE_ELEMENTS_RESULT)

        self.assertAlmostEqual(elements.eccentricity, 0.01585631189570606)
        self.assertAlmostEqual(elements.inclination_deg, 0.003324222762500364)
        self.assertAlmostEqual(elements.longitude_of_ascending_node_deg, 178.4280325590773)
        self.assertAlmostEqual(elements.argument_of_periapsis_deg, 286.0556274903796)
        self.assertAlmostEqual(elements.mean_anomaly_deg, 157.4440636358304)
        self.assertAlmostEqual(elements.semi_major_axis_m, 1.000914613370246 * AU)
        self.assertAlmostEqual(elements.orbital_period_s, 365.757566878775 * DAY)

    def test_apply_vectors_preserves_non_vector_fields(self):
        preset = {
            "schema_version": 2,
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
                    **({} if body_id == "sun" else {"parent_id": "sun"}),
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
            self.assertEqual(body.get("parent_id"), original_body.get("parent_id"))
            self.assertNotEqual(body["position_m"], original_body["position_m"])
            self.assertNotEqual(body["velocity_mps"], original_body["velocity_mps"])

    def test_apply_vectors_adds_orbital_metadata_when_elements_are_available(self):
        targets = update_solar_system_preset.SOLAR_SYSTEM_TARGETS
        preset = {
            "schema_version": 5,
            "id": "builtin-solar-system",
            "name": "Solar System",
            "epoch": "old",
            "description": "old",
            "groups": [
                {
                    "id": "solar-system-group",
                    "name": "Solar System",
                    "kind": "planetary_system",
                    "body_ids": ["sun"],
                }
            ],
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
                    **({} if body_id == "sun" else {"parent_id": "sun"}),
                }
                for index, body_id in enumerate(targets)
            ],
        }
        vectors = {
            body_id: update_solar_system_preset.StateVector([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
            for body_id in targets
        }
        elements = {
            body_id: update_solar_system_preset.OrbitalElements(
                semi_major_axis_m=AU,
                orbital_period_s=365.25 * DAY,
                eccentricity=0.01,
                inclination_deg=1.0,
                longitude_of_ascending_node_deg=2.0,
                argument_of_periapsis_deg=3.0,
                mean_anomaly_deg=4.0,
            )
            for body_id in targets
            if body_id != "sun"
        }

        updated = update_solar_system_preset.apply_vectors(
            preset,
            vectors,
            "2026-06-14 00:00:00",
            targets,
            elements=elements,
            retrieved_at="2026-06-15",
        )

        self.assertEqual(updated["schema_version"], SCHEMA_VERSION)
        sun = next(body for body in updated["bodies"] if body["id"] == "sun")
        earth = next(body for body in updated["bodies"] if body["id"] == "earth")
        self.assertNotIn("orbit", sun)
        self.assertEqual(earth["orbit"]["semi_major_axis_m"], AU)
        self.assertEqual(earth["orbit"]["orbital_period_s"], 365.25 * DAY)
        self.assertEqual(earth["orbit"]["reference_plane"], "J2000 ecliptic")
        self.assertEqual(earth["data_source"]["source_name"], "JPL Horizons")
        self.assertEqual(earth["data_source"]["catalog_id"], targets["earth"])
        self.assertEqual(earth["data_source"]["retrieved_at"], "2026-06-15")
        self.assertIn("EPHEM_TYPE=ELEMENTS", earth["data_source"]["source_url"])

    def test_apply_vectors_accepts_dwarf_planet_target_set(self):
        targets = update_solar_system_preset.DWARF_PLANET_TARGETS
        preset = {
            "schema_version": 2,
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
                    **({} if body_id == "sun" else {"parent_id": "sun"}),
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
        self.assertEqual(updated["bodies"][-1]["parent_id"], "sun")

    def test_dwarf_planet_targets_use_small_body_disambiguation(self):
        targets = update_solar_system_preset.DWARF_PLANET_TARGETS

        self.assertEqual(targets["eris"], "136199;")
        self.assertEqual(targets["orcus"], "90482;")
        self.assertNotIn("ceres", targets)
        self.assertNotIn("sedna", targets)

    def test_solar_system_targets_include_ceres_and_pluto(self):
        targets = update_solar_system_preset.SOLAR_SYSTEM_TARGETS

        self.assertEqual(targets["pluto"], "999")
        self.assertEqual(targets["ceres"], "1;")

    def test_apply_vectors_requires_all_targets(self):
        preset = {
            "schema_version": 2,
            "id": "builtin-solar-system",
            "name": "Solar System",
            "bodies": [],
        }

        with self.assertRaisesRegex(ValueError, "Preset is missing expected bodies"):
            update_solar_system_preset.apply_vectors(preset, {}, "2026-06-14 00:00:00")

    def test_apply_vectors_requires_elements_for_non_sun_targets(self):
        targets = {"sun": "10", "earth": "399"}
        preset = {
            "schema_version": 5,
            "id": "builtin-solar-system",
            "name": "Solar System",
            "bodies": [
                {
                    "id": "sun",
                    "name": "Sun",
                    "kind": "star",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [0, 0, 0],
                    "velocity_mps": [0, 0, 0],
                    "color": "#fff",
                },
                {
                    "id": "earth",
                    "name": "Earth",
                    "kind": "planet",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [0, 0, 0],
                    "velocity_mps": [0, 0, 0],
                    "color": "#fff",
                    "parent_id": "sun",
                },
            ],
        }
        vectors = {
            body_id: update_solar_system_preset.StateVector([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
            for body_id in targets
        }

        with self.assertRaisesRegex(ValueError, "Missing fetched orbital elements: earth"):
            update_solar_system_preset.apply_vectors(
                preset,
                vectors,
                "2026-06-14 00:00:00",
                targets,
                elements={},
            )


if __name__ == "__main__":
    unittest.main()
