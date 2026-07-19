import csv
import io
import unittest

import numpy as np

from src.analysis_frames import AnalysisFrameSpec
from src.frame_exports import relative_system_snapshot, serialize_relative_csv
from src.models import Body, ReferenceOrigin, SolarSystem, SystemGroup, SystemReferenceFrame
from src.physics import SimulationState


def _system():
    bodies = [
        Body(
            id="a",
            name="A",
            kind="star",
            mass_kg=3.0,
            radius_m=1.0,
            position_m=[10.0, 20.0, 30.0],
            velocity_mps=[1.0, 2.0, 3.0],
            color="#ffffff",
        ),
        Body(
            id="b",
            name="B",
            kind="star",
            mass_kg=1.0,
            radius_m=1.0,
            position_m=[14.0, 20.0, 30.0],
            velocity_mps=[1.0, 4.0, 3.0],
            color="#ffffff",
        ),
    ]
    return SolarSystem(
        name="Export",
        epoch="2026-07-19 00:00:00 TDB",
        bodies=bodies,
        groups=[SystemGroup(name="Pair", kind="stellar_system", body_ids=["a", "b"], id="pair")],
        reference_frame=SystemReferenceFrame(
            epoch="2026-07-19 00:00:00",
            time_scale="TDB",
            axes_id="icrf",
            origin=ReferenceOrigin("jpl", "500@0"),
        ),
    )


class FrameExportTests(unittest.TestCase):
    def test_body_centered_snapshot_is_importable_and_nonmutating(self):
        system = _system()
        original = system.to_dict()
        state = SimulationState.from_bodies(system.bodies)
        state.positions_m += np.asarray((100.0, -50.0, 25.0))
        state.velocities_mps += np.asarray((5.0, -2.0, 1.0))
        state.elapsed_s = 12.5

        exported = relative_system_snapshot(system, state, "body", "a")
        reloaded = SolarSystem.from_dict(exported.to_dict())

        np.testing.assert_allclose(reloaded.bodies[0].position_m, 0.0)
        np.testing.assert_allclose(reloaded.bodies[0].velocity_mps, 0.0)
        np.testing.assert_allclose(reloaded.bodies[1].position_m, (4.0, 0.0, 0.0))
        np.testing.assert_allclose(reloaded.bodies[1].velocity_mps, (0.0, 2.0, 0.0))
        self.assertEqual(reloaded.reference_frame.origin.to_dict(), {"kind": "body", "id": "a"})
        self.assertTrue(reloaded.reference_frame.epoch.endswith("12.500000"))
        self.assertEqual(system.to_dict(), original)

    def test_group_and_system_barycenter_presets_center_mass_weighted_state(self):
        system = _system()
        state = SimulationState.from_bodies(system.bodies)

        for kind, origin_id in (("group_barycenter", "pair"), ("system_barycenter", None)):
            with self.subTest(kind=kind):
                exported = relative_system_snapshot(system, state, kind, origin_id)
                masses = np.asarray([body.mass_kg for body in exported.bodies])
                positions = np.asarray([body.position_m for body in exported.bodies])
                velocities = np.asarray([body.velocity_mps for body in exported.bodies])
                np.testing.assert_allclose(np.average(positions, axis=0, weights=masses), 0.0)
                np.testing.assert_allclose(np.average(velocities, axis=0, weights=masses), 0.0)

    def test_current_snapshot_marks_the_propagated_origin_as_app_local(self):
        system = _system()
        state = SimulationState.from_bodies(system.bodies)
        state.elapsed_s = 5.0

        exported = relative_system_snapshot(system, state, "fixed")

        self.assertEqual(exported.reference_frame.origin.kind, "custom")
        self.assertEqual(exported.reference_frame.origin.id, "inertial-snapshot-origin")
        np.testing.assert_allclose(exported.bodies[0].position_m, system.bodies[0].position_m)

    def test_csv_contains_relative_state_and_all_explicit_terms(self):
        system = _system()
        state = SimulationState.from_bodies(system.bodies)
        spec = AnalysisFrameSpec(origin_kind="body", origin_id="a", label="Body A")

        contents = serialize_relative_csv(system, state, spec).decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(contents)))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["analysis_frame"], "Body A")
        self.assertEqual(float(rows[0]["x_m"]), 0.0)
        for column in (
            "gravity_x_mps2",
            "translation_x_mps2",
            "coriolis_x_mps2",
            "centrifugal_x_mps2",
            "euler_x_mps2",
            "apparent_total_x_mps2",
        ):
            self.assertIn(column, rows[0])


if __name__ == "__main__":
    unittest.main()
