import math
import unittest

from src.constants import AU, DAY, G
from src.models import Body, ModelError, OrbitData, SystemGroup
from src.orbits import (
    binary_pair_state_vectors,
    group_barycenter,
    semi_major_axis_from_period,
    shift_group_to_barycenter,
    state_vectors_from_orbit,
)

SOLAR_MASS = 1.98847e30
EARTH_MASS = 5.97237e24


class OrbitConversionTests(unittest.TestCase):
    def test_circular_earth_like_orbit(self):
        sun = Body("Sun", "star", SOLAR_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        earth = Body("Earth", "planet", EARTH_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#00f")
        orbit = OrbitData(semi_major_axis_m=AU, eccentricity=0.0, mean_anomaly_deg=0.0)

        position, velocity = state_vectors_from_orbit(sun, earth, orbit)

        self.assertAlmostEqual(position[0], AU, delta=1.0)
        self.assertAlmostEqual(position[1], 0.0, delta=1.0)
        self.assertAlmostEqual(position[2], 0.0, delta=1.0)
        expected_speed = math.sqrt(G * (SOLAR_MASS + EARTH_MASS) / AU)
        self.assertAlmostEqual(velocity[0], 0.0, delta=1.0e-6)
        self.assertAlmostEqual(velocity[1], expected_speed, delta=0.01)

    def test_period_derived_axis_matches_direct_axis(self):
        period_s = 365.2568983 * DAY

        axis = semi_major_axis_from_period(period_s, SOLAR_MASS, EARTH_MASS)

        self.assertAlmostEqual(axis, AU, delta=0.001 * AU)

    def test_eccentric_orbit_is_finite(self):
        star = Body("Star", "star", SOLAR_MASS, 1.0, [10.0, 20.0, 30.0], [1.0, 2.0, 3.0], "#fff")
        planet = Body("Planet", "planet", EARTH_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#00f")
        orbit = OrbitData(
            semi_major_axis_m=0.5 * AU,
            eccentricity=0.4,
            inclination_deg=30.0,
            longitude_of_ascending_node_deg=15.0,
            argument_of_periapsis_deg=60.0,
            mean_anomaly_deg=120.0,
        )

        position, velocity = state_vectors_from_orbit(star, planet, orbit)

        self.assertTrue(all(math.isfinite(component) for component in position))
        self.assertTrue(all(math.isfinite(component) for component in velocity))
        self.assertNotEqual(position[2], 30.0)

    def test_hyperbolic_orbit_has_expected_periapsis_and_positive_energy(self):
        star = Body("Star", "star", SOLAR_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        comet = Body("Comet", "comet", 1.0e14, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        axis_magnitude = 2.0 * AU
        eccentricity = 1.5
        orbit = OrbitData(
            semi_major_axis_m=-axis_magnitude,
            eccentricity=eccentricity,
            mean_anomaly_deg=0.0,
        )

        position, velocity = state_vectors_from_orbit(star, comet, orbit)

        radius = math.dist(position, star.position_m)
        speed_sq = sum(component * component for component in velocity)
        mu = G * (star.mass_kg + comet.mass_kg)
        specific_energy = 0.5 * speed_sq - mu / radius
        self.assertAlmostEqual(radius, axis_magnitude * (eccentricity - 1.0), delta=1.0)
        self.assertAlmostEqual(specific_energy, mu / (2.0 * axis_magnitude), delta=1.0)

    def test_hyperbolic_mean_anomaly_is_not_angle_wrapped(self):
        star = Body("Star", "star", SOLAR_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        comet = Body("Comet", "comet", 1.0e14, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")

        positive, _ = state_vectors_from_orbit(
            star,
            comet,
            OrbitData(semi_major_axis_m=-AU, eccentricity=2.0, mean_anomaly_deg=720.0),
        )
        negative, _ = state_vectors_from_orbit(
            star,
            comet,
            OrbitData(semi_major_axis_m=-AU, eccentricity=2.0, mean_anomaly_deg=-720.0),
        )

        self.assertAlmostEqual(positive[0], negative[0], delta=1.0)
        self.assertAlmostEqual(positive[1], -negative[1], delta=1.0)

    def test_missing_axis_and_period_fails(self):
        star = Body("Star", "star", SOLAR_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        planet = Body("Planet", "planet", EARTH_MASS, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#00f")

        with self.assertRaisesRegex(ModelError, "semi_major_axis_m or orbital_period_s"):
            state_vectors_from_orbit(star, planet, OrbitData(eccentricity=0.0))

    def test_group_barycenter_and_shift_preserves_internal_separation(self):
        bodies = [
            Body("A", "star", 3.0, 1.0, [-2.0, 0.0, 0.0], [0.0, -1.0, 0.0], "#fff", id="a"),
            Body("B", "star", 1.0, 1.0, [6.0, 0.0, 0.0], [0.0, 3.0, 0.0], "#fff", id="b"),
        ]
        groups = [SystemGroup("Binary", "binary_system", ["a", "b"], id="binary")]

        center = group_barycenter(bodies, groups, "binary")
        shift_group_to_barycenter(
            bodies,
            groups,
            "binary",
            type(center)(center.mass_kg, [10.0, 20.0, 30.0], [1.0, 2.0, 3.0]),
        )
        shifted_center = group_barycenter(bodies, groups, "binary")

        self.assertEqual(shifted_center.position_m, [10.0, 20.0, 30.0])
        self.assertEqual(shifted_center.velocity_mps, [1.0, 2.0, 3.0])
        self.assertAlmostEqual(bodies[1].position_m[0] - bodies[0].position_m[0], 8.0)

    def test_binary_pair_generation_conserves_barycenter_and_momentum(self):
        first = Body("A", "star", 3.0 * SOLAR_MASS, 1.0, [-1.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        second = Body("B", "star", SOLAR_MASS, 1.0, [3.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        orbit = OrbitData(semi_major_axis_m=4.0 * AU, eccentricity=0.0)

        first_state, second_state = binary_pair_state_vectors(
            first,
            second,
            orbit,
            [100.0, 200.0, 300.0],
            [1.0, 2.0, 3.0],
        )

        total_mass = first.mass_kg + second.mass_kg
        center_position = [
            (first.mass_kg * first_state[0][axis] + second.mass_kg * second_state[0][axis]) / total_mass
            for axis in range(3)
        ]
        center_velocity = [
            (first.mass_kg * first_state[1][axis] + second.mass_kg * second_state[1][axis]) / total_mass
            for axis in range(3)
        ]
        self.assertAlmostEqual(center_position[0], 100.0, delta=1.0e-3)
        self.assertAlmostEqual(center_position[1], 200.0, delta=1.0e-3)
        self.assertAlmostEqual(center_position[2], 300.0, delta=1.0e-3)
        self.assertAlmostEqual(center_velocity[0], 1.0, delta=1.0e-9)
        self.assertAlmostEqual(center_velocity[1], 2.0, delta=1.0e-9)
        self.assertAlmostEqual(center_velocity[2], 3.0, delta=1.0e-9)


if __name__ == "__main__":
    unittest.main()
