import math
import unittest

import numpy as np

from src.analysis_frames import (
    AnalysisFrameSpec,
    frame_kinematics,
    relative_diagnostics,
    transform_state,
)
from src.constants import G
from src.models import (
    Body,
    ReferenceOrigin,
    SolarSystem,
    SystemReferenceFrame,
    SystemSettings,
)
from src.physics import SimulationState


def _body(body_id, mass, position, velocity):
    return Body(
        id=body_id,
        name=body_id.title(),
        kind="star",
        mass_kg=mass,
        radius_m=1.0,
        position_m=list(position),
        velocity_mps=list(velocity),
        color="#ffffff",
    )


def _system(bodies):
    return SolarSystem(
        name="Analysis",
        epoch="custom",
        bodies=bodies,
        settings=SystemSettings(physics_mode="newtonian"),
    )


class AnalysisFrameTests(unittest.TestCase):
    def test_body_translation_zeroes_origin_and_exposes_translation_term(self):
        bodies = [
            _body("primary", 5.0e20, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            _body("secondary", 2.0e20, (10.0, 0.0, 0.0), (0.0, 2.0, 0.0)),
        ]
        system = _system(bodies)
        state = SimulationState.from_bodies(bodies)
        spec = AnalysisFrameSpec(origin_kind="body", origin_id="primary", label="Primary")

        kinematics, rows = relative_diagnostics(system, state, spec)

        np.testing.assert_allclose(rows[0].position_m, 0.0)
        np.testing.assert_allclose(rows[0].velocity_mps, 0.0)
        np.testing.assert_allclose(
            rows[0].translational_acceleration_mps2,
            -np.asarray(rows[0].gravitational_acceleration_mps2),
        )
        np.testing.assert_allclose(rows[0].total_apparent_acceleration_mps2, 0.0)
        self.assertNotEqual(kinematics.origin_acceleration_mps2, (0.0, 0.0, 0.0))

    def test_prescribed_rotation_makes_matching_motion_stationary(self):
        rate = 0.5
        elapsed = 2.0
        angle = rate * elapsed
        body = _body(
            "point",
            1.0,
            (math.cos(angle), math.sin(angle), 0.0),
            (-rate * math.sin(angle), rate * math.cos(angle), 0.0),
        )
        system = _system([body])
        state = SimulationState.from_bodies([body])
        state.elapsed_s = elapsed
        spec = AnalysisFrameSpec(
            rotation_mode="prescribed",
            angular_rate_rad_s=rate,
            rotation_axis=(0.0, 0.0, 1.0),
        )

        transformed = transform_state(state, frame_kinematics(system, state, spec))

        np.testing.assert_allclose(transformed.positions_m[0], (1.0, 0.0, 0.0), atol=1e-12)
        np.testing.assert_allclose(transformed.velocities_mps[0], 0.0, atol=1e-12)

    def test_prescribed_frame_reports_centrifugal_and_euler_terms(self):
        body = _body("point", 1.0, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        system = _system([body])
        state = SimulationState.from_bodies([body])
        spec = AnalysisFrameSpec(
            rotation_mode="prescribed",
            angular_rate_rad_s=2.0,
            angular_acceleration_rad_s2=3.0,
        )

        _kinematics, rows = relative_diagnostics(system, state, spec)

        np.testing.assert_allclose(rows[0].centrifugal_acceleration_mps2, (4.0, 0.0, 0.0))
        np.testing.assert_allclose(rows[0].euler_acceleration_mps2, (0.0, -3.0, 0.0))
        np.testing.assert_allclose(rows[0].coriolis_acceleration_mps2, (-8.0, 0.0, 0.0))

    def test_target_pair_frame_tracks_relative_line_and_angular_rate(self):
        mass = 4.0 / G
        bodies = [
            _body("a", mass, (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
            _body("b", mass, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ]
        system = _system(bodies)
        state = SimulationState.from_bodies(bodies)
        spec = AnalysisFrameSpec(
            origin_kind="body",
            origin_id="a",
            rotation_mode="target_pair",
            secondary_kind="body",
            secondary_id="b",
        )

        kinematics = frame_kinematics(system, state, spec)
        transformed = transform_state(state, kinematics)

        np.testing.assert_allclose(transformed.positions_m[0], 0.0, atol=1e-10)
        np.testing.assert_allclose(transformed.positions_m[1], (2.0, 0.0, 0.0), atol=1e-10)
        np.testing.assert_allclose(transformed.velocities_mps, 0.0, atol=2e-6)
        self.assertAlmostEqual(kinematics.angular_velocity_rad_s[2], 1.0, places=6)

    def test_axes_of_date_expose_precession_nutation_angular_motion(self):
        body = _body("point", 1.0, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        system = _system([body])
        system.reference_frame = SystemReferenceFrame(
            epoch="2026-07-19 00:00:00",
            time_scale="TT",
            axes_id="icrf",
            origin=ReferenceOrigin("jpl", "500@0"),
        )
        state = SimulationState.from_bodies([body])
        state.elapsed_s = 10.0 * 86_400.0
        spec = AnalysisFrameSpec(
            axes_id="true_equator_of_date",
            rotation_mode="of_date",
        )

        kinematics = frame_kinematics(system, state, spec)
        rotation = np.asarray(kinematics.rotation_matrix)

        np.testing.assert_allclose(rotation @ rotation.T, np.identity(3), atol=2e-12)
        self.assertGreater(np.linalg.norm(kinematics.angular_velocity_rad_s), 0.0)


if __name__ == "__main__":
    unittest.main()
