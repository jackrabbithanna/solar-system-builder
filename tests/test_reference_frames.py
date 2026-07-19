import unittest

import numpy as np

from src.models import (
    Body,
    DataSource,
    FlybyData,
    ModelError,
    OrbitData,
    SolarSystem,
    SystemGroup,
    SystemReferenceFrame,
)
from src.orbits import state_vectors_from_orbit
from src.reference_frames import (
    ReferenceFrameTransform,
    origin_for_body,
    origin_for_group,
    origin_for_system,
    rotation_matrix_from_xyz_degrees,
    transform_system_reference_frame,
    validate_rotation_matrix,
)


class ReferenceFrameTests(unittest.TestCase):
    def setUp(self):
        self.star = Body(
            "Star",
            "star",
            1.0e30,
            1.0,
            [10.0, 20.0, 30.0],
            [1.0, 2.0, 3.0],
            "#fff",
            id="star",
        )
        orbit = OrbitData(
            semi_major_axis_m=1.0e7,
            eccentricity=0.2,
            inclination_deg=33.0,
            longitude_of_ascending_node_deg=44.0,
            argument_of_periapsis_deg=55.0,
            mean_anomaly_deg=66.0,
            epoch="Epoch",
            reference_plane="old plane",
            approximation_notes="retained notes",
        )
        self.planet = Body(
            "Planet",
            "planet",
            1.0e20,
            1.0,
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            "#fff",
            id="planet",
            parent_id="star",
            orbit=orbit,
            data_source=DataSource(source_name="Catalog"),
            state_origin="orbital",
        )
        self.planet.position_m, self.planet.velocity_mps = state_vectors_from_orbit(
            self.star,
            self.planet,
            orbit,
        )
        self.group = SystemGroup(
            "System",
            "planetary_system",
            ["star"],
            id="group",
            orbit=OrbitData(
                semi_major_axis_m=2.0e7,
                eccentricity=0.1,
                inclination_deg=0.0,
                longitude_of_ascending_node_deg=0.0,
                argument_of_periapsis_deg=0.0,
                reference_plane="old plane",
            ),
        )
        self.system = SolarSystem(
            "Test",
            "Epoch",
            [self.star, self.planet],
            id="system",
            groups=[self.group],
            reference_frame=SystemReferenceFrame(
                epoch="Epoch",
                center_id="old-center",
                reference_plane="old plane",
                reference_system="old system",
            ),
        )

    def target_frame(self):
        return SystemReferenceFrame(
            epoch="Epoch",
            center_id="new-center",
            reference_plane="new plane",
            reference_system="new system",
        )

    def test_guided_rotation_order_and_matrix_validation(self):
        rotation = np.asarray(rotation_matrix_from_xyz_degrees(90.0, 0.0, 90.0))

        np.testing.assert_allclose(rotation @ [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(rotation), 1.0)
        with self.assertRaisesRegex(ModelError, "orthonormal"):
            validate_rotation_matrix(((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        with self.assertRaisesRegex(ModelError, "determinant"):
            validate_rotation_matrix(((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))

    def test_custom_origin_translates_positions_and_velocities(self):
        transform = ReferenceFrameTransform(
            origin_position_m=(10.0, 20.0, 30.0),
            origin_velocity_mps=(1.0, 2.0, 3.0),
        )

        transformed = transform_system_reference_frame(
            self.system,
            self.target_frame(),
            transform,
        )

        np.testing.assert_allclose(transformed.bodies[0].position_m, [0.0, 0.0, 0.0])
        np.testing.assert_allclose(transformed.bodies[0].velocity_mps, [0.0, 0.0, 0.0])
        self.assertEqual(transformed.reference_frame.center_id, "new-center")
        self.assertEqual(self.system.reference_frame.center_id, "old-center")

    def test_body_group_and_system_origins(self):
        self.assertEqual(origin_for_body(self.system, "star"), ((10.0, 20.0, 30.0), (1.0, 2.0, 3.0)))
        group_position, group_velocity = origin_for_group(self.system, "group")
        position, velocity = origin_for_system(self.system)
        np.testing.assert_allclose(group_position, position)
        np.testing.assert_allclose(group_velocity, velocity)
        transformed = transform_system_reference_frame(
            self.system,
            self.target_frame(),
            ReferenceFrameTransform(position, velocity),
        )
        masses = np.asarray([body.mass_kg for body in transformed.bodies])
        positions = np.asarray([body.position_m for body in transformed.bodies])
        velocities = np.asarray([body.velocity_mps for body in transformed.bodies])
        np.testing.assert_allclose(np.average(positions, axis=0, weights=masses), 0.0, atol=1e-8)
        np.testing.assert_allclose(np.average(velocities, axis=0, weights=masses), 0.0, atol=1e-12)

    def test_rotation_updates_orbit_orientation_without_changing_state_meaning(self):
        rotation = rotation_matrix_from_xyz_degrees(12.0, 23.0, 34.0)

        transformed = transform_system_reference_frame(
            self.system,
            self.target_frame(),
            ReferenceFrameTransform(rotation_matrix=rotation),
        )
        star, planet = transformed.bodies
        rebuilt_position, rebuilt_velocity = state_vectors_from_orbit(star, planet, planet.orbit)

        np.testing.assert_allclose(rebuilt_position, planet.position_m, atol=2e-9)
        np.testing.assert_allclose(rebuilt_velocity, planet.velocity_mps, atol=2e-9)
        self.assertEqual(planet.orbit.reference_plane, "new plane")
        self.assertEqual(planet.orbit.approximation_notes, "retained notes")
        self.assertEqual(planet.data_source.source_name, "Catalog")
        self.assertEqual(planet.orbit.mean_anomaly_deg, 66.0)
        self.assertEqual(transformed.groups[0].orbit.reference_plane, "new plane")

    def test_coplanar_group_and_flyby_angles_use_deterministic_node(self):
        visitor = Body(
            "Visitor",
            "comet",
            1.0,
            1.0,
            [100.0, 0.0, 0.0],
            [0.0, 10.0, 0.0],
            "#fff",
            id="visitor",
            orbit=OrbitData(
                semi_major_axis_m=-10.0,
                eccentricity=2.0,
                inclination_deg=0.0,
                longitude_of_ascending_node_deg=0.0,
                argument_of_periapsis_deg=0.0,
            ),
            state_origin="flyby",
            flyby=FlybyData(
                anchor_body_id="star",
                periapsis_distance_m=10.0,
                velocity_at_infinity_mps=1.0,
                start_distance_m=100.0,
            ),
        )
        self.system.bodies.append(visitor)
        self.system.validate()

        transformed = transform_system_reference_frame(
            self.system,
            self.target_frame(),
            ReferenceFrameTransform(rotation_matrix=rotation_matrix_from_xyz_degrees(0, 0, 90)),
        )

        transformed_visitor = next(body for body in transformed.bodies if body.id == "visitor")
        self.assertAlmostEqual(transformed_visitor.flyby.inclination_deg, 0.0)
        self.assertAlmostEqual(transformed_visitor.flyby.longitude_of_ascending_node_deg, 0.0)
        self.assertAlmostEqual(transformed_visitor.flyby.argument_of_periapsis_deg, 90.0)
        self.assertAlmostEqual(transformed.groups[0].orbit.longitude_of_ascending_node_deg, 0.0)
        self.assertAlmostEqual(transformed.groups[0].orbit.argument_of_periapsis_deg, 90.0)

    def test_invalid_target_is_atomic(self):
        original = self.system.to_dict()
        invalid = self.target_frame()
        invalid.axes_id = "not-a-frame"

        with self.assertRaisesRegex(ModelError, "unsupported reference axes"):
            transform_system_reference_frame(
                self.system,
                invalid,
                ReferenceFrameTransform(origin_position_m=(999.0, 0.0, 0.0)),
            )

        self.assertEqual(self.system.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
