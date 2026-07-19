import io
import json
import math
import threading
import unittest
from collections import deque
from datetime import datetime, timezone

from src.horizons import (
    HorizonsClient,
    HorizonsError,
    HorizonsImportDraft,
    HorizonsSearchResult,
    add_imported_body,
    apply_system_refresh,
    build_elements_url,
    build_lookup_url,
    build_sbdb_url,
    build_vector_url,
    horizons_command_for_result,
    horizons_command_for_body,
    horizons_import_available,
    horizons_refresh_available,
    horizons_refreshable_bodies,
    parse_horizons_elements,
    parse_horizons_physical_properties,
    parse_horizons_vector,
    parse_horizons_vector_with_delta_t,
    parse_required_physical_value,
    parse_sbdb_physical_properties,
    shift_horizons_frame_epoch,
)
from src.constants import AU, DAY, G
from src.models import DataSource, FlybyData, ModelError, OrbitData
from src.system_editing import (
    BodyStateInput,
    add_body_from_state,
    add_flyby_from_state,
    create_system,
)


VECTOR_RESULT = """
Target body name: Mars (499)
$$SOE
2461205.500000000, A.D. 2026-Jun-14 00:00:00.0000, -2.014, 3.125, 0.042, -0.001, -0.002, 0.003
$$EOE
"""

VECTOR_WITH_DELTA_RESULT = """
Target body name: Earth (399)
$$SOE
2461239.500000000, A.D. 2026-Jul-18 00:00:00.0000, 69.183656, 6.4E+07, -1.3E+08, 8.0E+03, 2.6E+01, 1.2E+01, 2.2E-04,
$$EOE
"""

SECOND_VECTOR_WITH_DELTA_RESULT = """
Target body name: Mars (499)
$$SOE
2461239.500000000, A.D. 2026-Jul-18 00:00:00.0000, 69.183656, 2.0E+08, -3.0E+08, 4.0E+07, 5.0E+00, 6.0E+00, 7.0E+00,
$$EOE
"""


def _vector_with_delta_result(name, position_km, velocity_kmps=(0.0, 0.0, 0.0)):
    values = ", ".join(
        str(value) for value in (*position_km, *velocity_kmps)
    )
    return f"""
Target body name: {name}
$$SOE
2461239.500000000, A.D. 2026-Jul-18 00:00:00.0000, 69.183656, {values},
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

    def test_vector_delta_t_parser_handles_horizons_trailing_csv_field(self):
        vector, delta_t_s = parse_horizons_vector_with_delta_t(
            VECTOR_WITH_DELTA_RESULT
        )

        self.assertAlmostEqual(delta_t_s, 69.183656)
        self.assertEqual(vector.position_m, [6.4e10, -1.3e11, 8.0e6])
        self.assertEqual(vector.velocity_mps, [2.6e4, 1.2e4, 0.22])

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

    def test_existing_body_recovers_exact_horizons_command_before_catalog_fallback(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        body = system.bodies[0]
        body.data_source = DataSource(
            source_name="JPL Horizons",
            source_url=(
                "https://ssd.jpl.nasa.gov/api/horizons.api?"
                "format=json&COMMAND=%27DES%3D1P%3B+CAP+%3C+2026%3B%27"
            ),
            catalog_id="1000036",
        )

        self.assertEqual(
            horizons_command_for_body(body),
            "DES=1P; CAP < 2026;",
        )

        body.data_source.source_url = "https://ssd.jpl.nasa.gov/horizons/"
        self.assertEqual(horizons_command_for_body(body), "1000036")

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
        self.assertEqual(draft.vector_center_catalog_id, "10")
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

    def test_system_refresh_uses_one_shared_utc_frame_and_matching_tdb_elements(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        mars = add_body_from_state(
            system,
            BodyStateInput(
                name="Mars",
                kind="planet",
                mass_kg=6.4e23,
                radius_m=3.4e6,
                position_m=(1.0, 2.0, 3.0),
                velocity_mps=(4.0, 5.0, 6.0),
                color="#ffffff",
                parent_id=sun.id,
            ),
        )
        mars.data_source = DataSource(
            source_name="JPL Horizons",
            source_url=(
                "https://ssd.jpl.nasa.gov/api/horizons.api?"
                "COMMAND=%27499%27&EPHEM_TYPE=ELEMENTS"
            ),
            catalog_id="499",
        )
        opener = FakeOpener(
            _payload(VECTOR_WITH_DELTA_RESULT),
            _payload(SECOND_VECTOR_WITH_DELTA_RESULT),
            _payload(ELEMENTS_RESULT),
        )
        progress = []

        refresh = HorizonsClient(opener=opener).fetch_system_refresh(
            system,
            datetime(2026, 7, 18, 0, 0, 0, 987654, tzinfo=timezone.utc),
            progress=lambda *values: progress.append(values),
        )

        self.assertEqual(refresh.utc_epoch, "2026-07-18T00:00:00Z")
        self.assertEqual(refresh.tdb_epoch, "2026-07-18 00:01:09.183656")
        self.assertEqual([item.body_id for item in refresh.bodies], [sun.id, mars.id])
        vector_urls = [url for url, _timeout in opener.urls[:2]]
        self.assertTrue(all("CENTER=%27500%4010%27" in url for url in vector_urls))
        self.assertTrue(all("TIME_TYPE=UT" in url for url in vector_urls))
        self.assertTrue(all("TIME_DIGITS=FRACSEC" in url for url in vector_urls))
        self.assertTrue(all("VEC_DELTA_T=YES" in url for url in vector_urls))
        self.assertIn("TIME_TYPE=TDB", opener.urls[2][0])
        self.assertIn("TIME_DIGITS=FRACSEC", opener.urls[2][0])
        self.assertIn("CENTER=%27500%4010%27", opener.urls[2][0])
        self.assertEqual(progress[-1][:2], (3, 3))

    def test_apply_system_refresh_preserves_non_horizons_and_physical_properties(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        manual = add_body_from_state(
            system,
            BodyStateInput(
                name="Manual Planet",
                kind="planet",
                mass_kg=7.0,
                radius_m=8.0,
                position_m=(9.0, 10.0, 11.0),
                velocity_mps=(12.0, 13.0, 14.0),
                color="#123456",
                parent_id=sun.id,
            ),
        )
        opener = FakeOpener(_payload(VECTOR_WITH_DELTA_RESULT))
        refresh = HorizonsClient(opener=opener).fetch_system_refresh(
            system,
            datetime(2026, 7, 18, tzinfo=timezone.utc),
        )

        updated = apply_system_refresh(system, refresh)

        self.assertIsNot(updated, system)
        self.assertEqual(system.reference_frame.epoch, "2026-06-14 00:00:00")
        self.assertEqual(updated.reference_frame.epoch, "2026-07-18 00:01:09.183656")
        self.assertEqual(updated.bodies[0].mass_kg, sun.mass_kg)
        self.assertEqual(updated.bodies[0].radius_m, sun.radius_m)
        updated_manual = next(body for body in updated.bodies if body.id == manual.id)
        self.assertEqual(updated_manual.position_m, manual.position_m)
        self.assertEqual(updated_manual.velocity_mps, manual.velocity_mps)
        self.assertEqual(updated_manual.color, "#123456")
        self.assertEqual(updated.bodies[0].state_origin, "horizons")
        self.assertIn("JPL Horizons", updated.epoch)

    def test_apply_system_refresh_regenerates_flyby_against_refreshed_anchor(self):
        system = create_system("Sol Flyby", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        visitor = add_flyby_from_state(
            system,
            BodyStateInput(
                name="Visitor",
                kind="star",
                mass_kg=1.0e29,
                radius_m=1.0e8,
                position_m=(0.0, 0.0, 0.0),
                velocity_mps=(0.0, 0.0, 0.0),
                color="#ff0000",
            ),
            FlybyData(sun.id, 5.0 * AU, 20_000.0, 50.0 * AU),
        )
        original_visitor_position = visitor.position_m[:]
        refresh = HorizonsClient(opener=FakeOpener(_payload(VECTOR_WITH_DELTA_RESULT))).fetch_system_refresh(
            system,
            datetime(2026, 7, 18, tzinfo=timezone.utc),
        )

        updated = apply_system_refresh(system, refresh)
        updated_by_id = {body.id: body for body in updated.bodies}
        updated_sun = updated_by_id[sun.id]
        updated_visitor = updated_by_id[visitor.id]

        self.assertEqual(visitor.position_m, original_visitor_position)
        self.assertAlmostEqual(
            math.dist(updated_visitor.position_m, updated_sun.position_m),
            50.0 * AU,
            delta=1.0,
        )
        self.assertEqual(updated_visitor.flyby.anchor_body_id, updated_sun.id)
        self.assertEqual(updated_visitor.state_origin, "flyby")
        self.assertEqual(updated_visitor.orbit.epoch, updated.reference_frame.epoch)

    def test_system_refresh_keeps_enceladus_closer_to_saturn_than_titan(self):
        system = create_system("Saturn with Moons", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]

        def add_horizons_body(name, kind, catalog_id, parent, distance_km):
            body = add_body_from_state(
                system,
                BodyStateInput(
                    name=name,
                    kind=kind,
                    mass_kg=1.0e20,
                    radius_m=1.0e5,
                    position_m=(distance_km * 1000.0, 0.0, 0.0),
                    velocity_mps=(0.0, 0.0, 0.0),
                    color="#ffffff",
                    parent_id=parent.id,
                ),
            )
            body.data_source = DataSource(
                source_name="JPL Horizons",
                catalog_id=catalog_id,
            )
            return body

        saturn = add_horizons_body("Saturn", "planet", "699", sun, 1.4e9)
        titan = add_horizons_body("Titan", "moon", "606", saturn, 1.4e9)
        enceladus = add_horizons_body("Enceladus", "moon", "602", saturn, 1.4e9)
        saturn_x_km = 1.4e9
        opener = FakeOpener(
            _payload(_vector_with_delta_result("Sun", (0.0, 0.0, 0.0))),
            _payload(_vector_with_delta_result("Saturn", (saturn_x_km, 0.0, 0.0))),
            _payload(
                _vector_with_delta_result(
                    "Titan",
                    (saturn_x_km + 1_221_870.0, 0.0, 0.0),
                )
            ),
            _payload(
                _vector_with_delta_result(
                    "Enceladus",
                    (saturn_x_km + 237_948.0, 0.0, 0.0),
                )
            ),
            _payload(ELEMENTS_RESULT),
            _payload(ELEMENTS_RESULT),
            _payload(ELEMENTS_RESULT),
        )

        refresh = HorizonsClient(opener=opener).fetch_system_refresh(
            system,
            datetime(2026, 7, 18, tzinfo=timezone.utc),
        )
        updated = apply_system_refresh(system, refresh)
        by_id = {body.id: body for body in updated.bodies}
        enceladus_distance = math.dist(
            by_id[enceladus.id].position_m,
            by_id[saturn.id].position_m,
        )
        titan_distance = math.dist(
            by_id[titan.id].position_m,
            by_id[saturn.id].position_m,
        )

        self.assertAlmostEqual(enceladus_distance, 237_948_000.0)
        self.assertAlmostEqual(titan_distance, 1_221_870_000.0)
        self.assertLess(enceladus_distance, titan_distance)
        self.assertTrue(
            all(
                "CENTER=%27500%4010%27" in url
                for url, _timeout in opener.urls[:4]
            )
        )

    def test_system_refresh_cancel_and_failure_leave_source_model_unchanged(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        before = system.to_dict()
        cancelled = threading.Event()
        cancelled.set()
        opener = FakeOpener(_payload(VECTOR_WITH_DELTA_RESULT))

        with self.assertRaisesRegex(HorizonsError, "cancelled"):
            HorizonsClient(opener=opener).fetch_system_refresh(
                system,
                datetime(2026, 7, 18, tzinfo=timezone.utc),
                cancel_event=cancelled,
            )
        self.assertEqual(opener.urls, [])
        self.assertEqual(system.to_dict(), before)

        failing = FakeOpener(_payload("No ephemeris table"))
        with self.assertRaisesRegex(HorizonsError, "Sun"):
            HorizonsClient(opener=failing).fetch_system_refresh(
                system,
                datetime(2026, 7, 18, tzinfo=timezone.utc),
            )
        self.assertEqual(system.to_dict(), before)

    def test_refresh_availability_requires_compatible_frame_and_provenance(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        self.assertTrue(horizons_refresh_available(system))
        self.assertEqual(horizons_refreshable_bodies(system), [system.bodies[0]])

        custom = create_system("Custom")
        custom.bodies[0].data_source = DataSource(
            source_name="JPL Horizons",
            catalog_id="10",
        )
        self.assertFalse(horizons_refresh_available(custom))

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

    def test_parent_centered_import_translates_onto_current_parent_state(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        sun.position_m = [100.0, 200.0, 300.0]
        sun.velocity_mps = [10.0, 20.0, 30.0]
        draft = HorizonsImportDraft(
            name="Mars",
            kind="planet",
            catalog_id="499",
            position_m=[1.0, 2.0, 3.0],
            velocity_mps=[4.0, 5.0, 6.0],
            orbit=None,
            data_source=DataSource(source_name="JPL Horizons", catalog_id="499"),
            vector_center_catalog_id="10",
        )

        body_id = add_imported_body(
            system,
            draft,
            mass_kg=6.4e23,
            radius_m=3.4e6,
            parent_id=sun.id,
        )

        mars = next(body for body in system.bodies if body.id == body_id)
        self.assertEqual(mars.position_m, [101.0, 202.0, 303.0])
        self.assertEqual(mars.velocity_mps, [14.0, 25.0, 36.0])

    def test_advanced_parent_regression_keeps_enceladus_closer_than_titan(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        saturn = add_body_from_state(
            system,
            BodyStateInput(
                name="Saturn",
                kind="planet",
                mass_kg=5.7e26,
                radius_m=5.8e7,
                position_m=(1.3e12, 5.3e11, -6.0e10),
                velocity_mps=(-4.2e3, 8.9e3, 10.0),
                color="#ffffff",
                parent_id=sun.id,
            ),
        )
        saturn.data_source = DataSource(source_name="JPL Horizons", catalog_id="699")
        titan = add_body_from_state(
            system,
            BodyStateInput(
                name="Titan",
                kind="moon",
                mass_kg=1.3e23,
                radius_m=2.6e6,
                position_m=(
                    saturn.position_m[0] + 1.25e9,
                    saturn.position_m[1],
                    saturn.position_m[2],
                ),
                velocity_mps=tuple(saturn.velocity_mps),
                color="#ffffff",
                parent_id=saturn.id,
            ),
        )
        draft = HorizonsImportDraft(
            name="Enceladus",
            kind="moon",
            catalog_id="602",
            position_m=[2.37e8, 0.0, 0.0],
            velocity_mps=[0.0, 12_000.0, 0.0],
            orbit=None,
            data_source=DataSource(source_name="JPL Horizons", catalog_id="602"),
            vector_center_catalog_id="699",
        )

        enceladus_id = add_imported_body(
            system,
            draft,
            mass_kg=1.1e20,
            radius_m=2.5e5,
            parent_id=saturn.id,
        )
        enceladus = next(body for body in system.bodies if body.id == enceladus_id)

        self.assertAlmostEqual(math.dist(enceladus.position_m, saturn.position_m), 2.37e8)
        self.assertLess(
            math.dist(enceladus.position_m, saturn.position_m),
            math.dist(titan.position_m, saturn.position_m),
        )

    def test_parent_centered_import_rejects_wrong_or_missing_moon_parent_catalog(self):
        system = create_system("Sol Test", "sol", epoch="2026-06-14 00:00:00")
        sun = system.bodies[0]
        saturn = add_body_from_state(
            system,
            BodyStateInput(
                name="Manual Saturn",
                kind="planet",
                mass_kg=5.7e26,
                radius_m=5.8e7,
                position_m=(1.0, 0.0, 0.0),
                velocity_mps=(0.0, 1.0, 0.0),
                color="#ffffff",
                parent_id=sun.id,
            ),
        )
        relative_moon = HorizonsImportDraft(
            name="Enceladus",
            kind="moon",
            catalog_id="602",
            position_m=[2.0, 0.0, 0.0],
            velocity_mps=[0.0, 2.0, 0.0],
            orbit=None,
            data_source=DataSource(source_name="JPL Horizons", catalog_id="602"),
            vector_center_catalog_id="699",
        )
        absolute_moon = HorizonsImportDraft(
            name="Enceladus",
            kind="moon",
            catalog_id="602",
            position_m=[2.0, 0.0, 0.0],
            velocity_mps=[0.0, 2.0, 0.0],
            orbit=None,
            data_source=DataSource(source_name="JPL Horizons", catalog_id="602"),
        )

        for draft in (relative_moon, absolute_moon):
            with self.subTest(center=draft.vector_center_catalog_id):
                with self.assertRaisesRegex(ModelError, "Horizons catalog id"):
                    add_imported_body(
                        system,
                        draft,
                        mass_kg=1.1e20,
                        radius_m=2.5e5,
                        parent_id=saturn.id,
                    )

        saturn.data_source = DataSource(
            source_name="JPL Horizons",
            catalog_id="799",
        )
        with self.assertRaisesRegex(ModelError, "does not match"):
            add_imported_body(
                system,
                relative_moon,
                mass_kg=1.1e20,
                radius_m=2.5e5,
                parent_id=saturn.id,
            )

    def test_shift_horizons_frame_epoch_applies_positive_and_negative_elapsed_time(self):
        frame = create_system(
            "Sol Test",
            "sol",
            epoch="2026-06-14 00:00:00",
        ).reference_frame

        forward = shift_horizons_frame_epoch(frame, DAY + 1.5)
        backward = shift_horizons_frame_epoch(frame, -DAY)

        self.assertEqual(forward.epoch, "2026-06-15 00:00:01.500000")
        self.assertEqual(backward.epoch, "2026-06-13 00:00:00")
        self.assertEqual(frame.epoch, "2026-06-14 00:00:00")

    def test_shift_horizons_frame_epoch_rejects_invalid_offset_inputs(self):
        frame = create_system("Sol Test", "sol", epoch="not-a-date").reference_frame

        with self.assertRaisesRegex(ModelError, "ISO date"):
            shift_horizons_frame_epoch(frame, 1.0)
        with self.assertRaisesRegex(ModelError, "finite"):
            shift_horizons_frame_epoch(frame, float("nan"))

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
        self.assertIn(
            "CENTER=%27500%40699%27",
            build_vector_url("602", system.reference_frame, center_catalog_id="699"),
        )
        self.assertIn("500%4010", build_elements_url("499", system.reference_frame, "10"))


if __name__ == "__main__":
    unittest.main()
