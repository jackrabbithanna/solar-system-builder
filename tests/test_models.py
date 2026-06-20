import importlib.util
import json
import sys
import unittest
from pathlib import Path

from src.constants import DAY
from src.models import (
    BODY_KINDS,
    SCHEMA_VERSION,
    Body,
    DataSource,
    ModelError,
    OrbitData,
    SolarSystem,
)
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
        self.assertEqual([group.name for group in clone.groups], ["Solar System"])
        self.assertEqual(clone.groups[0].body_ids, ["sun"])
        self.assertGreaterEqual(len(clone.bodies), 11)
        self.assertIn("ceres", {body.id for body in clone.bodies})
        self.assertIn("pluto", {body.id for body in clone.bodies})
        parents_by_id = {body.id: body.parent_id for body in clone.bodies}
        self.assertEqual(parents_by_id["moon"], "earth")
        self.assertEqual(parents_by_id["halley"], "sun")
        self.assertEqual(parents_by_id["bennu"], "sun")
        kinds_by_id = {body.id: body.kind for body in clone.bodies}
        self.assertEqual(kinds_by_id["moon"], "moon")
        self.assertEqual(kinds_by_id["halley"], "comet")
        self.assertEqual(kinds_by_id["bennu"], "asteroid")
        self.assertEqual(
            BODY_KINDS,
            {"star", "planet", "dwarf planet", "moon", "comet", "asteroid"},
        )

    def test_all_builtin_presets_round_trip(self):
        systems = load_builtin_solar_systems()

        self.assertEqual(
            [system.id for system in systems],
            ["builtin-solar-system", "builtin-dwarf-planets", "builtin-binary-system"],
        )
        self.assertEqual([system.name for system in systems], ["Solar System", "Dwarf Planets", "Alpha Centauri"])
        alpha = systems[-1]
        self.assertEqual(alpha.settings.visible_step_s, 1_000.0 * 365.25 * DAY)
        self.assertEqual(alpha.settings.trail_sample_interval_s, 1_000.0 * 365.25 * DAY)
        self.assertEqual(alpha.settings.view_mode, "fit_system")
        for system in systems:
            clone = SolarSystem.from_dict(system.to_dict())
            self.assertEqual(system.id, clone.id)
            self.assertGreaterEqual(len(clone.groups), 1)
            self.assertGreaterEqual(len(clone.bodies), 1)
            body_ids = {body.id for body in clone.bodies}
            for body in clone.bodies:
                if body.parent_id is not None:
                    self.assertIn(body.parent_id, body_ids)
            for group in clone.groups:
                for body_id in group.body_ids:
                    self.assertIn(body_id, body_ids)

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
        self.assertEqual(system.groups[0].body_ids, ["sun"])

    def test_v2_data_migrates_default_settings(self):
        data = _sample_system_data(schema_version=2)
        system = SolarSystem.from_dict(data)

        self.assertEqual(system.schema_version, SCHEMA_VERSION)
        self.assertEqual(system.settings.visible_step_s, DAY)
        self.assertEqual(system.settings.distance_unit, "AU")
        self.assertEqual(system.settings.simulation_scope, "auto")

    def test_v3_data_migrates_default_simulation_scope(self):
        data = _sample_system_data(schema_version=3)
        system = SolarSystem.from_dict(data)

        self.assertEqual(system.schema_version, SCHEMA_VERSION)
        self.assertEqual(system.settings.simulation_scope, "auto")
        self.assertEqual(system.groups[0].body_ids, ["sun"])

    def test_v4_data_migrates_default_groups(self):
        data = _sample_system_data(schema_version=4)
        system = SolarSystem.from_dict(data)

        self.assertEqual(system.schema_version, SCHEMA_VERSION)
        self.assertEqual(system.groups[0].name, "Sample")
        self.assertEqual(system.groups[0].kind, "planetary_system")
        self.assertEqual(system.groups[0].body_ids, ["sun"])

    def test_v5_data_migrates_without_orbit_metadata(self):
        data = _sample_system_data(schema_version=5)

        system = SolarSystem.from_dict(data)

        self.assertEqual(system.schema_version, SCHEMA_VERSION)
        self.assertIsNone(system.bodies[1].orbit)
        self.assertIsNone(system.bodies[1].data_source)

    def test_dwarf_planets_preset_gets_large_step_default(self):
        system = next(
            item
            for item in load_builtin_solar_systems()
            if item.id == "builtin-dwarf-planets"
        )

        self.assertEqual(system.settings.visible_step_s, 90.0 * DAY)
        self.assertEqual(system.settings.trail_sample_interval_s, 90.0 * DAY)
        self.assertEqual(system.settings.simulation_scope, "auto")
        self.assertEqual(system.groups[0].body_ids, ["sun"])

    def test_alpha_centauri_groups_describe_hierarchy(self):
        system = next(
            item
            for item in load_builtin_solar_systems()
            if item.id == "builtin-binary-system"
        )
        groups_by_id = {group.id: group for group in system.groups}

        self.assertEqual(groups_by_id["alpha-centauri-system"].body_ids, [])
        self.assertIsNone(groups_by_id["alpha-centauri-system"].parent_group_id)
        self.assertEqual(
            groups_by_id["alpha-centauri-ab-system"].body_ids,
            ["alpha-centauri-a", "alpha-centauri-b"],
        )
        self.assertEqual(
            groups_by_id["alpha-centauri-ab-system"].parent_group_id,
            "alpha-centauri-system",
        )
        self.assertEqual(
            groups_by_id["proxima-centauri-system"].body_ids,
            ["proxima-centauri"],
        )
        self.assertEqual(
            groups_by_id["proxima-centauri-system"].parent_group_id,
            "alpha-centauri-system",
        )
        self.assertEqual(_body_ids_for_group(system, "alpha-centauri-system"), {
            "alpha-centauri-a",
            "alpha-centauri-b",
            "alpha-centauri-a-candidate",
            "proxima-centauri",
            "proxima-centauri-b",
            "proxima-centauri-d",
            "proxima-centauri-c-candidate",
        })
        self.assertEqual(_body_ids_for_group(system, "alpha-centauri-ab-system"), {
            "alpha-centauri-a",
            "alpha-centauri-b",
            "alpha-centauri-a-candidate",
        })

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
            "simulation_scope": "stellar_overview",
            "trail_sample_interval_s": 5.0 * DAY,
        }

        system = SolarSystem.from_dict(data)
        clone = SolarSystem.from_dict(system.to_dict())

        self.assertEqual(clone.settings.visible_step_s, 10.0 * DAY)
        self.assertEqual(clone.settings.accuracy_profile, "fast")
        self.assertEqual(clone.settings.distance_unit, "kAU")
        self.assertEqual(clone.settings.view_mode, "log_overview")
        self.assertEqual(clone.settings.simulation_scope, "stellar_overview")
        self.assertEqual(clone.settings.trail_sample_interval_s, 5.0 * DAY)

    def test_orbit_metadata_round_trip(self):
        data = _sample_system_data()
        data["bodies"][1]["orbit"] = {
            "semi_major_axis_m": 149_597_870_700.0,
            "orbital_period_s": 365.25 * DAY,
            "eccentricity": 0.0167,
            "inclination_deg": 1.2,
            "longitude_of_ascending_node_deg": 2.3,
            "argument_of_periapsis_deg": 3.4,
            "mean_anomaly_deg": 4.5,
            "epoch": "J2000",
            "reference_plane": "app-local XY",
            "approximation_notes": "Approximate seed",
        }
        data["bodies"][1]["data_source"] = {
            "source_name": "NASA Exoplanet Archive",
            "source_url": "https://example.test",
            "catalog_id": "Example b",
            "retrieved_at": "2026-06-14",
            "citation": "Example citation",
        }

        clone = SolarSystem.from_dict(SolarSystem.from_dict(data).to_dict())
        planet = clone.bodies[1]

        self.assertIsInstance(planet.orbit, OrbitData)
        self.assertEqual(planet.orbit.semi_major_axis_m, 149_597_870_700.0)
        self.assertEqual(planet.orbit.reference_plane, "app-local XY")
        self.assertIsInstance(planet.data_source, DataSource)
        self.assertEqual(planet.data_source.source_name, "NASA Exoplanet Archive")

    def test_orbit_requires_axis_or_period(self):
        data = _sample_system_data()
        data["bodies"][1]["orbit"] = {"eccentricity": 0.0}

        with self.assertRaisesRegex(ModelError, "semi_major_axis_m or orbital_period_s"):
            SolarSystem.from_dict(data)

    def test_invalid_orbit_eccentricity_fails(self):
        data = _sample_system_data()
        data["bodies"][1]["orbit"] = {
            "semi_major_axis_m": 1.0,
            "eccentricity": 1.0,
        }

        with self.assertRaisesRegex(ModelError, "eccentricity"):
            SolarSystem.from_dict(data)

    def test_hyperbolic_orbit_requires_negative_axis_and_no_period(self):
        orbit = OrbitData(semi_major_axis_m=-2.0, eccentricity=1.5)

        orbit.validate()
        self.assertEqual(OrbitData.from_dict(orbit.to_dict()), orbit)

        with self.assertRaisesRegex(ModelError, "must be negative"):
            OrbitData(semi_major_axis_m=2.0, eccentricity=1.5).validate()
        with self.assertRaisesRegex(ModelError, "cannot have orbital_period_s"):
            OrbitData(semi_major_axis_m=-2.0, orbital_period_s=10.0, eccentricity=1.5).validate()

    def test_invalid_simulation_scope_fails(self):
        data = _sample_system_data()
        data["settings"] = {"simulation_scope": "nearby_only"}

        with self.assertRaisesRegex(ModelError, "unsupported simulation_scope"):
            SolarSystem.from_dict(data)

    def test_group_round_trip(self):
        data = _sample_system_data()
        data["groups"] = [
            {
                "id": "sample-root",
                "name": "Sample Root",
                "kind": "system",
                "body_ids": [],
            },
            {
                "id": "sample-inner",
                "name": "Sample Inner",
                "kind": "planetary_system",
                "parent_group_id": "sample-root",
                "body_ids": ["sun"],
            },
        ]

        clone = SolarSystem.from_dict(SolarSystem.from_dict(data).to_dict())

        self.assertEqual([group.id for group in clone.groups], ["sample-root", "sample-inner"])
        self.assertEqual(clone.groups[1].parent_group_id, "sample-root")
        self.assertEqual(clone.groups[1].body_ids, ["sun"])

    def test_group_orbit_metadata_round_trip(self):
        data = _sample_system_data()
        data["bodies"].append(
            {
                "id": "other-star",
                "name": "Other Star",
                "kind": "star",
                "mass_kg": 1.0,
                "radius_m": 1.0,
                "position_m": [10.0, 0.0, 0.0],
                "velocity_mps": [0.0, 0.0, 0.0],
                "color": "#ffff00",
            }
        )
        data["groups"] = [
            {
                "id": "sample-root",
                "name": "Sample Root",
                "kind": "system",
                "body_ids": ["other-star"],
            },
            {
                "id": "sample-inner",
                "name": "Sample Inner",
                "kind": "planetary_system",
                "body_ids": ["earth"],
                "orbit_target_type": "group",
                "orbit_target_id": "sample-root",
                "orbit": {
                    "semi_major_axis_m": 10.0,
                    "eccentricity": 0.1,
                    "reference_plane": "app-local XY",
                },
                "data_source": {
                    "source_name": "Example",
                    "source_url": "https://example.test",
                },
            },
        ]

        clone = SolarSystem.from_dict(SolarSystem.from_dict(data).to_dict())
        inner = next(group for group in clone.groups if group.id == "sample-inner")

        self.assertIsInstance(inner.orbit, OrbitData)
        self.assertEqual(inner.orbit_target_type, "group")
        self.assertEqual(inner.orbit_target_id, "sample-root")
        self.assertIsInstance(inner.data_source, DataSource)
        self.assertEqual(inner.data_source.source_name, "Example")

    def test_invalid_group_orbit_target_type_fails(self):
        data = _sample_system_data()
        data["groups"] = [
            {
                "id": "sample-root",
                "name": "Sample Root",
                "kind": "system",
                "body_ids": ["sun"],
                "orbit_target_type": "cluster",
                "orbit_target_id": "earth",
            },
        ]

        with self.assertRaisesRegex(ModelError, "unsupported orbit_target_type"):
            SolarSystem.from_dict(data)

    def test_group_cannot_orbit_own_body(self):
        data = _sample_system_data()
        data["groups"] = [
            {
                "id": "sample-root",
                "name": "Sample Root",
                "kind": "system",
                "body_ids": ["sun"],
                "orbit_target_type": "body",
                "orbit_target_id": "sun",
            },
        ]

        with self.assertRaisesRegex(ModelError, "cannot orbit a body inside itself"):
            SolarSystem.from_dict(data)

    def test_group_cannot_orbit_overlapping_group(self):
        data = _sample_system_data()
        data["bodies"].append(
            {
                "id": "other-star",
                "name": "Other Star",
                "kind": "star",
                "mass_kg": 1.0,
                "radius_m": 1.0,
                "position_m": [10.0, 0.0, 0.0],
                "velocity_mps": [0.0, 0.0, 0.0],
                "color": "#ffff00",
            }
        )
        data["groups"] = [
            {
                "id": "sample-root",
                "name": "Sample Root",
                "kind": "system",
                "body_ids": ["other-star"],
            },
            {
                "id": "sample-inner",
                "name": "Sample Inner",
                "kind": "planetary_system",
                "parent_group_id": "sample-root",
                "body_ids": ["earth"],
                "orbit_target_type": "group",
                "orbit_target_id": "sample-root",
            },
        ]

        with self.assertRaisesRegex(ModelError, "overlapping group"):
            SolarSystem.from_dict(data)

    def test_group_missing_body_fails(self):
        data = _sample_system_data()
        data["groups"] = [
            {
                "id": "bad-group",
                "name": "Bad",
                "kind": "system",
                "body_ids": ["missing"],
            }
        ]

        with self.assertRaisesRegex(ModelError, "body_id missing does not exist"):
            SolarSystem.from_dict(data)

    def test_group_missing_parent_fails(self):
        data = _sample_system_data()
        data["groups"] = [
            {
                "id": "child-group",
                "name": "Child",
                "kind": "system",
                "parent_group_id": "missing-group",
                "body_ids": ["sun"],
            }
        ]

        with self.assertRaisesRegex(ModelError, "parent_group_id missing-group does not exist"):
            SolarSystem.from_dict(data)

    def test_group_cycle_fails(self):
        data = _sample_system_data()
        data["groups"] = [
            {
                "id": "first",
                "name": "First",
                "kind": "system",
                "parent_group_id": "second",
                "body_ids": ["sun"],
            },
            {
                "id": "second",
                "name": "Second",
                "kind": "system",
                "parent_group_id": "first",
                "body_ids": [],
            },
        ]

        with self.assertRaisesRegex(ModelError, "group cycle"):
            SolarSystem.from_dict(data)

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

    def test_body_kinds_and_parent_semantics_are_validated(self):
        data = _sample_system_data()
        data["bodies"].extend(
            [
                {
                    "id": "pluto",
                    "name": "Pluto",
                    "kind": "dwarf planet",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [2.0, 0.0, 0.0],
                    "velocity_mps": [0.0, 1.0, 0.0],
                    "color": "#aaa",
                    "parent_id": "sun",
                },
                {
                    "id": "moon",
                    "name": "Moon",
                    "kind": "moon",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [1.1, 0.0, 0.0],
                    "velocity_mps": [0.0, 1.1, 0.0],
                    "color": "#ccc",
                    "parent_id": "earth",
                },
                {
                    "id": "charon",
                    "name": "Charon",
                    "kind": "moon",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [2.1, 0.0, 0.0],
                    "velocity_mps": [0.0, 1.1, 0.0],
                    "color": "#bbb",
                    "parent_id": "pluto",
                },
                {
                    "id": "halley",
                    "name": "Halley",
                    "kind": "comet",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [3.0, 0.0, 0.0],
                    "velocity_mps": [0.0, 1.0, 0.0],
                    "color": "#fff",
                    "parent_id": "sun",
                },
                {
                    "id": "bennu",
                    "name": "Bennu",
                    "kind": "asteroid",
                    "mass_kg": 1.0,
                    "radius_m": 1.0,
                    "position_m": [4.0, 0.0, 0.0],
                    "velocity_mps": [0.0, 1.0, 0.0],
                    "color": "#999",
                    "parent_id": "sun",
                },
            ]
        )

        system = SolarSystem.from_dict(data)

        self.assertEqual(len(system.bodies), 7)

    def test_unknown_kind_and_invalid_moon_parent_fail(self):
        data = _sample_system_data()
        data["bodies"][1]["kind"] = "space rock"
        with self.assertRaisesRegex(ModelError, "unsupported body kind"):
            SolarSystem.from_dict(data)

        data = _sample_system_data()
        data["bodies"][1]["kind"] = "moon"
        with self.assertRaisesRegex(ModelError, "moon parent must be"):
            SolarSystem.from_dict(data)

    def test_root_nonstar_and_nonstar_planet_parent_fail(self):
        data = _sample_system_data()
        data["bodies"][1].pop("parent_id")
        with self.assertRaisesRegex(ModelError, "requires a parent star"):
            SolarSystem.from_dict(data)

        data = _sample_system_data()
        data["bodies"].append(
            {
                "id": "child",
                "name": "Child",
                "kind": "asteroid",
                "mass_kg": 1.0,
                "radius_m": 1.0,
                "position_m": [2.0, 0.0, 0.0],
                "velocity_mps": [0.0, 1.0, 0.0],
                "color": "#999",
                "parent_id": "earth",
            }
        )
        with self.assertRaisesRegex(ModelError, "parent must be a star"):
            SolarSystem.from_dict(data)

    def test_duplicate_remaps_parent_ids(self):
        data = _sample_system_data()
        data["bodies"].append(
            {
                "id": "other-star",
                "name": "Other Star",
                "kind": "star",
                "mass_kg": 1.0,
                "radius_m": 1.0,
                "position_m": [10.0, 0.0, 0.0],
                "velocity_mps": [0.0, 0.0, 0.0],
                "color": "#ffff00",
            }
        )
        data["groups"] = [
            {
                "id": "sample-root",
                "name": "Sample Root",
                "kind": "system",
                "body_ids": ["other-star"],
            },
            {
                "id": "sample-inner",
                "name": "Sample Inner",
                "kind": "planetary_system",
                "body_ids": ["earth"],
                "orbit_target_type": "group",
                "orbit_target_id": "sample-root",
            },
        ]
        system = SolarSystem.from_dict(data)

        duplicate = system.duplicate("Copy")

        self.assertNotEqual(system.id, duplicate.id)
        duplicate_ids = {body.id for body in duplicate.bodies}
        original_ids = {body.id for body in system.bodies}
        self.assertTrue(duplicate_ids.isdisjoint(original_ids))
        star = next(body for body in duplicate.bodies if body.kind == "star")
        planet = next(body for body in duplicate.bodies if body.kind == "planet")
        self.assertEqual(planet.parent_id, star.id)
        self.assertNotEqual(system.groups[0].id, duplicate.groups[0].id)
        other_star = next(body for body in duplicate.bodies if body.name == "Other Star")
        self.assertEqual(duplicate.groups[0].body_ids, [other_star.id])
        duplicate_groups_by_name = {group.name: group for group in duplicate.groups}
        self.assertEqual(
            duplicate_groups_by_name["Sample Inner"].orbit_target_id,
            duplicate_groups_by_name["Sample Root"].id,
        )


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


def _body_ids_for_group(system: SolarSystem, group_id: str) -> set[str]:
    group_ids = {group_id}
    changed = True
    while changed:
        changed = False
        for group in system.groups:
            if group.parent_group_id in group_ids and group.id not in group_ids:
                group_ids.add(group.id)
                changed = True

    body_ids = {
        body_id
        for group in system.groups
        if group.id in group_ids
        for body_id in group.body_ids
    }
    changed = True
    while changed:
        changed = False
        for body in system.bodies:
            if body.parent_id in body_ids and body.id not in body_ids:
                body_ids.add(body.id)
                changed = True
    return body_ids


if __name__ == "__main__":
    unittest.main()
