import threading
import unittest

import numpy as np

from src.models import (
    Body,
    ModelError,
    OrbitData,
    ReferenceOrigin,
    SolarSystem,
    SystemReferenceFrame,
    SystemSettings,
)
from src.physics import SimulationState, step
from src.reference_frames import transform_system_to_standard_frame
from src.standard_frames import (
    axes_matrix,
    convert_epoch,
    epoch_delta_seconds,
    rotation_between_frames,
    shift_epoch,
)


def _body(body_id, name, mass, position, velocity, *, parent_id=None):
    return Body(
        id=body_id,
        name=name,
        kind="star" if parent_id is None else "planet",
        mass_kg=mass,
        radius_m=1.0,
        position_m=list(position),
        velocity_mps=list(velocity),
        color="#ffffff",
        parent_id=parent_id,
    )


def _system():
    frame = SystemReferenceFrame(
        epoch="2026-07-19 00:00:00",
        time_scale="TDB",
        axes_id="icrf",
        origin=ReferenceOrigin("jpl", "500@0"),
    )
    return SolarSystem(
        name="Test",
        epoch="2026-07-19 00:00:00 TDB",
        bodies=[
            _body("star", "Star", 2.0e30, (1.0e6, 2.0e6, -3.0e6), (3.0, 4.0, 5.0)),
            _body(
                "planet",
                "Planet",
                6.0e24,
                (1.5e11, 2.0e6, -3.0e6),
                (3.0, 29_800.0, 5.0),
                parent_id="star",
            ),
        ],
        settings=SystemSettings(physics_mode="newtonian", integrator="rk4"),
        reference_frame=frame,
    )


class StandardFrameTests(unittest.TestCase):
    def test_time_scale_conversion_preserves_physical_instant(self):
        tai = convert_epoch("2026-01-01 00:00:00", "UTC", "TAI")

        self.assertTrue(tai.startswith("2026-01-01 00:00:37"))
        self.assertAlmostEqual(
            epoch_delta_seconds("2026-01-01 00:00:00", "UTC", tai, "TAI"),
            0.0,
            places=6,
        )
        self.assertEqual(
            shift_epoch("2026-01-01 00:00:00", "TDB", 90.5),
            "2026-01-01 00:01:30.500000",
        )

    def test_all_registered_standard_axes_are_proper_rotations(self):
        axes_ids = (
            "icrf",
            "fk5_j2000",
            "mean_equator_of_date",
            "true_equator_of_date",
            "jpl_ecliptic_j2000",
            "mean_ecliptic_of_date",
            "true_ecliptic_of_date",
            "galactic_iau1958",
        )
        matrices = {}
        for axes_id in axes_ids:
            matrix = np.asarray(axes_matrix(axes_id, "2026-07-19 00:00:00", "TT"))
            matrices[axes_id] = matrix
            np.testing.assert_allclose(matrix @ matrix.T, np.identity(3), atol=2.0e-12)
            self.assertAlmostEqual(np.linalg.det(matrix), 1.0, places=12)

        np.testing.assert_allclose(matrices["icrf"], np.identity(3))
        np.testing.assert_allclose(
            matrices["fk5_j2000"],
            axes_matrix("fk5_j2000", "2126-07-19 00:00:00", "TT"),
        )
        self.assertFalse(
            np.allclose(
                matrices["mean_equator_of_date"],
                matrices["true_equator_of_date"],
                atol=1.0e-10,
            )
        )

    def test_rotation_between_frames_uses_registered_metadata(self):
        source = _system().reference_frame
        target = SystemReferenceFrame(
            epoch=source.epoch,
            time_scale=source.time_scale,
            axes_id="jpl_ecliptic_j2000",
            origin=ReferenceOrigin("jpl", "500@0"),
        )

        rotation = np.asarray(rotation_between_frames(source, target))

        np.testing.assert_allclose(
            rotation,
            axes_matrix("jpl_ecliptic_j2000", source.epoch, source.time_scale),
        )

    def test_epoch_transform_matches_full_nbody_step(self):
        system = _system()
        system.bodies[1].orbit = OrbitData(
            semi_major_axis_m=1.5e11,
            eccentricity=0.01,
        )
        initial = SimulationState.from_bodies(system.bodies)
        expected = step(initial, 10.0, "newtonian", "rk4")
        target = SystemReferenceFrame(
            epoch=shift_epoch(system.reference_frame.epoch, "TDB", 10.0),
            time_scale="TDB",
            axes_id="icrf",
            origin=ReferenceOrigin("jpl", "500@0"),
        )

        transformed = transform_system_to_standard_frame(system, target)

        np.testing.assert_allclose(
            [body.position_m for body in transformed.bodies],
            expected.positions_m,
        )
        np.testing.assert_allclose(
            [body.velocity_mps for body in transformed.bodies],
            expected.velocities_mps,
        )
        self.assertEqual(transformed.reference_frame.to_dict(), target.to_dict())
        self.assertEqual(transformed.bodies[1].orbit.epoch, f"{target.epoch} TDB")
        self.assertIn("recomputed", transformed.bodies[1].orbit.approximation_notes)
        np.testing.assert_allclose(
            [body.position_m for body in system.bodies],
            initial.positions_m,
        )

    def test_local_body_origin_is_applied_after_axes_rotation(self):
        system = _system()
        target = SystemReferenceFrame(
            epoch=system.reference_frame.epoch,
            time_scale="TDB",
            axes_id="jpl_ecliptic_j2000",
            origin=ReferenceOrigin("body", "star"),
        )
        relative = np.asarray(system.bodies[1].position_m) - np.asarray(
            system.bodies[0].position_m
        )
        rotation = np.asarray(rotation_between_frames(system.reference_frame, target))

        transformed = transform_system_to_standard_frame(system, target)

        np.testing.assert_allclose(transformed.bodies[0].position_m, 0.0, atol=1.0e-9)
        np.testing.assert_allclose(transformed.bodies[0].velocity_mps, 0.0, atol=1.0e-12)
        np.testing.assert_allclose(transformed.bodies[1].position_m, rotation @ relative)
        self.assertEqual(transformed.reference_frame.origin.kind, "body")

    def test_external_authoritative_origin_is_subtracted_before_rotation(self):
        system = _system()
        target = SystemReferenceFrame(
            epoch=system.reference_frame.epoch,
            time_scale="TDB",
            axes_id="jpl_ecliptic_j2000",
            origin=ReferenceOrigin("jpl", "500@10"),
        )
        origin_position = (2.0e5, -3.0e5, 4.0e5)
        origin_velocity = (7.0, -8.0, 9.0)
        rotation = np.asarray(rotation_between_frames(system.reference_frame, target))

        transformed = transform_system_to_standard_frame(
            system,
            target,
            external_origin=(origin_position, origin_velocity),
        )

        np.testing.assert_allclose(
            transformed.bodies[0].position_m,
            rotation @ (np.asarray(system.bodies[0].position_m) - origin_position),
        )
        np.testing.assert_allclose(
            transformed.bodies[0].velocity_mps,
            rotation @ (np.asarray(system.bodies[0].velocity_mps) - origin_velocity),
        )

    def test_cancelled_epoch_transform_is_atomic(self):
        system = _system()
        original = system.to_dict()
        target = SystemReferenceFrame(
            epoch="2027-07-19 00:00:00",
            time_scale="TDB",
            axes_id="icrf",
            origin=ReferenceOrigin("jpl", "500@0"),
        )
        cancelled = threading.Event()
        cancelled.set()

        with self.assertRaisesRegex(ModelError, "cancelled"):
            transform_system_to_standard_frame(
                system,
                target,
                cancel_event=cancelled,
            )

        self.assertEqual(system.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
