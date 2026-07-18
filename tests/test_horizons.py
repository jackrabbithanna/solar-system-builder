import io
import json
import unittest
from collections import deque

from src.horizons import (
    HorizonsClient,
    HorizonsError,
    HorizonsImportDraft,
    HorizonsSearchResult,
    add_imported_body,
    build_elements_url,
    build_lookup_url,
    build_sbdb_url,
    build_vector_url,
    horizons_command_for_result,
    horizons_import_available,
    parse_horizons_elements,
    parse_horizons_physical_properties,
    parse_horizons_vector,
    parse_required_physical_value,
    parse_sbdb_physical_properties,
)
from src.constants import G
from src.models import DataSource, ModelError, OrbitData
from src.system_editing import create_system


VECTOR_RESULT = """
Target body name: Mars (499)
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000, -2.014, 3.125, 0.042, -0.001, -0.002, 0.003
$$EOE
"""

ELEMENTS_RESULT = """
Target body name: Mars (499)
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000,  1.5E-02,  9.8E-01,  3.3E-03,  1.78E+02,  2.86E+02,  2.46E+06,  9.8E-01,  1.57E+02,  1.58E+02,  1.0E+00,  1.01E+00,  3.65E+02,
$$EOE
"""

JUPITER_RESULT = """
Jupiter physical parameters:
  Mass x 10^26 (kg)     = 18.9819
  Vol. Mean Radius (km) = 69911+-6
  GM (km^3/s^2)         = 126686531.900
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000, -2.014, 3.125, 0.042, -0.001, -0.002, 0.003
$$EOE
"""

SMALL_BODY_RESULT = """
Asteroid physical parameters (km, seconds, rotational period in hours):
   GM= 62.6284             RAD= 469.7              ROTPER= 9.07417
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000, -2.014, 3.125, 0.042, -0.001, -0.002, 0.003
$$EOE
"""


class FakeOpener:
    def __init__(self, *payloads):
        self.payloads = deque(payloads)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append((url, timeout))
        return io.BytesIO(json.dumps(self.payloads.popleft()).encode())


def _payload(result=None, *, source="NASA/JPL Horizons API", version="1.3", **extra):
    payload = {
        "signature": {"source": source, "version": version},
        **extra,
    }
    if result is not None:
        payload["result"] = result
    return payload


class HorizonsTests(unittest.TestCase):
    def test_required_physical_values_are_positive_finite_numbers(self):
        self.assertEqual(parse_required_physical_value(" 1.8982e27 ", "Mass"), 1.8982e27)
        for invalid in ("", "not-a-number", "0", "-1", "inf", "nan"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ModelError, "Mass"):
                    parse_required_physical_value(invalid, "Mass")

    def test_parsers_convert_to_si_models(self):
        vector = parse_horizons_vector(VECTOR_RESULT)
        elements = parse_horizons_elements(ELEMENTS_RESULT)

        for actual, expected in zip(vector.position_m, [-2014.0, 3125.0, 42.0]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(vector.velocity_mps, [-1.0, -2.0, 3.0])
        self.assertGreater(elements.semi_major_axis_m, 1.0e11)
        self.assertGreater(elements.orbital_period_s, 3.0e7)

    def test_horizons_physical_parser_prefers_gm_and_mean_radius(self):
        physical = parse_horizons_physical_properties(JUPITER_RESULT)

        self.assertAlmostEqual(
            physical.mass_kg,
            126686531.900 * 1.0e9 / G,
        )
        self.assertEqual(physical.radius_m, 69_911_000.0)
        self.assertIn("derived from JPL GM", physical.mass_note)

    def test_horizons_physical_parser_handles_small_body_fields(self):
        physical = parse_horizons_physical_properties(SMALL_BODY_RESULT)

        self.assertAlmostEqual(physical.mass_kg, 62.6284 * 1.0e9 / G)
        self.assertEqual(physical.radius_m, 469_700.0)

    def test_sbdb_physical_parser_uses_structured_gm_and_diameter(self):
        physical = parse_sbdb_physical_properties(
            {
                "phys_par": [
                    {"name": "diameter", "value": "16.84", "units": "km"},
                    {"name": "GM", "value": "4.463e-04", "units": "km^3/s^2"},
                ]
            }
        )

        self.assertAlmostEqual(physical.mass_kg, 4.463e-4 * 1.0e9 / G)
        self.assertEqual(physical.radius_m, 8_420.0)

    def test_search_returns_structured_supported_and_unsupported_results(self):
        opener = FakeOpener(
            _payload(
                source="NASA/JPL Horizons Lookup API",
                version="1.1",
                count=2,
                result=[
                    {"name": "Mars", "type": "planet", "pdes": None, "spkid": "499", "alias": []},
                    {"name": "Juno", "type": "spacecraft", "pdes": None, "spkid": "-61", "alias": []},
                ],
            )
        )

        results = HorizonsClient(opener=opener).search("Juno")

        self.assertEqual([result.name for result in results], ["Mars", "Juno"])
        self.assertEqual(results[0].suggested_kind, "planet")
        self.assertFalse(results[1].supported)
        self.assertIn("sstr=Juno", opener.urls[0][0])

    def test_halley_integrated_barycenter_results_are_supported(self):
        asteroid = HorizonsSearchResult(
            "2688 Halley",
            "asteroid (integrated barycenter)",
            "1982 HG1",
            "20002688",
        )
        comet = HorizonsSearchResult(
            "Halley",
            "comet (integrated barycenter)",
            "1P",
            "1000036",
        )
        system_barycenter = HorizonsSearchResult(
            "Patroclus (system barycenter)",
            "asteroid barycenter",
            "",
            "20000617",
        )

        self.assertEqual(asteroid.suggested_kind, "asteroid")
        self.assertEqual(comet.suggested_kind, "comet")
        self.assertIsNone(system_barycenter.suggested_kind)

    def test_small_body_lookup_results_use_horizons_search_commands(self):
        frame = create_system(
            "Sol Test",
            "sol",
            epoch="2026-06-14 00:00:00",
        ).reference_frame
        comet = HorizonsSearchResult(
            "Halley",
            "comet (integrated barycenter)",
            "1P",
            "1000036",
        )
        asteroid = HorizonsSearchResult(
            "433 Eros",
            "asteroid (integrated barycenter)",
            "A898 PA",
            "20000433",
        )

        self.assertEqual(
            horizons_command_for_result(comet, frame),
            "DES=1P; CAP < 2026;",
        )
        self.assertEqual(
            horizons_command_for_result(asteroid, frame),
            "DES=A898 PA;",
        )

    def test_search_handles_no_matches_and_rejects_new_api_major(self):
        no_match = FakeOpener(
            _payload(source="NASA/JPL Horizons Lookup API", version="1.1", count="0")
        )
        self.assertEqual(HorizonsClient(opener=no_match).search("nothing"), [])

        incompatible = FakeOpener(
            _payload(source="NASA/JPL Horizons Lookup API", version="2.0", count=0)
        )
        with self.assertRaisesRegex(HorizonsError, "unsupported.*version"):
            HorizonsClient(opener=incompatible).search("Mars")

    def test_fetch_import_uses_system_frame_and_parent_elements(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        opener = FakeOpener(_payload(VECTOR_RESULT), _payload(ELEMENTS_RESULT))
        result = HorizonsSearchResult("Mars", "planet", "", "499")

        draft = HorizonsClient(opener=opener).fetch_import(
            result,
            system.reference_frame,
            parent_catalog_id="10",
        )

        self.assertEqual(draft.kind, "planet")
        self.assertIsNotNone(draft.orbit)
        self.assertEqual(draft.missing_physical_fields, ("mass_kg", "radius_m"))
        self.assertIn("CENTER=%27500%4010%27", opener.urls[0][0])
        self.assertIn("EPHEM_TYPE=ELEMENTS", opener.urls[1][0])

    def test_fetch_import_prefills_available_horizons_physical_values(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        opener = FakeOpener(_payload(JUPITER_RESULT))
        result = HorizonsSearchResult("Jupiter", "planet", "", "599")

        draft = HorizonsClient(opener=opener).fetch_import(
            result,
            system.reference_frame,
        )

        self.assertAlmostEqual(draft.mass_kg, 126686531.900 * 1.0e9 / G)
        self.assertEqual(draft.radius_m, 69_911_000.0)
        self.assertEqual(draft.missing_physical_fields, ())
        self.assertIn("OBJ_DATA=YES", opener.urls[0][0])

    def test_fetch_import_uses_sbdb_for_missing_small_body_values(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        opener = FakeOpener(
            _payload(VECTOR_RESULT),
            _payload(
                source="NASA/JPL Small-Body Database (SBDB) API",
                phys_par=[
                    {"name": "diameter", "value": "11.0", "units": "km"},
                ],
            ),
        )
        result = HorizonsSearchResult(
            "Halley",
            "comet (integrated barycenter)",
            "1P",
            "1000036",
        )

        draft = HorizonsClient(opener=opener).fetch_import(
            result,
            system.reference_frame,
        )

        self.assertIsNone(draft.mass_kg)
        self.assertEqual(draft.radius_m, 5_500.0)
        self.assertEqual(draft.missing_physical_fields, ("mass_kg",))
        self.assertIn("phys-par=true", opener.urls[1][0])

    def test_fetch_import_keeps_vector_when_elements_are_unavailable(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        opener = FakeOpener(_payload(VECTOR_RESULT), _payload("No ephemeris table"))
        result = HorizonsSearchResult("Mars", "planet", "", "499")

        draft = HorizonsClient(opener=opener).fetch_import(
            result,
            system.reference_frame,
            parent_catalog_id="10",
        )

        for actual, expected in zip(draft.position_m, [-2014.0, 3125.0, 42.0]):
            self.assertAlmostEqual(actual, expected)
        self.assertIsNone(draft.orbit)
        self.assertIn("orbital elements unavailable", draft.warning)
        self.assertEqual(draft.data_source.citation, "JPL Horizons state vector.")

    def test_add_imported_body_is_atomic_and_prevents_duplicates(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        draft = HorizonsImportDraft(
            name="Mars",
            kind="planet",
            catalog_id="499",
            position_m=[1.0, 2.0, 3.0],
            velocity_mps=[4.0, 5.0, 6.0],
            orbit=OrbitData(semi_major_axis_m=2.0e11),
            data_source=DataSource(source_name="JPL Horizons", catalog_id="499"),
        )

        body_id = add_imported_body(
            system,
            draft,
            mass_kg=6.4e23,
            radius_m=3.4e6,
            parent_id=sun.id,
        )

        mars = next(body for body in system.bodies if body.id == body_id)
        self.assertEqual(mars.state_origin, "horizons")
        self.assertEqual(mars.position_m, [1.0, 2.0, 3.0])
        with self.assertRaisesRegex(ModelError, "already present"):
            add_imported_body(
                system,
                draft,
                mass_kg=6.4e23,
                radius_m=3.4e6,
                parent_id=sun.id,
            )

    def test_invalid_import_metadata_rolls_back_body(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        draft = HorizonsImportDraft(
            name="Invalid",
            kind="planet",
            catalog_id="999001",
            position_m=[1.0, 2.0, 3.0],
            velocity_mps=[4.0, 5.0, 6.0],
            orbit=OrbitData(semi_major_axis_m=-1.0, eccentricity=0.5),
            data_source=DataSource(source_name="JPL Horizons", catalog_id="999001"),
        )
        before_ids = [body.id for body in system.bodies]

        with self.assertRaises(ModelError):
            add_imported_body(
                system,
                draft,
                mass_kg=1.0,
                radius_m=1.0,
                parent_id=sun.id,
            )

        self.assertEqual([body.id for body in system.bodies], before_ids)

    def test_custom_system_is_not_horizons_compatible(self):
        system = create_system("Custom")
        self.assertFalse(horizons_import_available(system))

    def test_url_builders_validate_and_encode_inputs(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        self.assertIn("sstr=1990+MU", build_lookup_url("1990 MU"))
        self.assertIn("sstr=1000036", build_sbdb_url("1000036"))
        self.assertIn("VEC_TABLE=2", build_vector_url("499", system.reference_frame))
        self.assertIn("500%4010", build_elements_url("499", system.reference_frame, "10"))


if __name__ == "__main__":
    unittest.main()
