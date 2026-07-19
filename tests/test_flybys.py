import math
import unittest

from src.constants import AU, G, SOLAR_MASS
from src.flybys import radial_velocity_mps, solve_flyby
from src.models import Body, FlybyData, ModelError


class FlybyTests(unittest.TestCase):
    def setUp(self):
        self.anchor = Body(
            "Sun",
            "star",
            SOLAR_MASS,
            695_700_000.0,
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            "#fff",
            id="sun",
        )
        self.visitor = Body(
            "Visitor",
            "star",
            0.1221 * SOLAR_MASS,
            1.0,
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            "#f00",
            id="visitor",
        )

    def test_solution_matches_start_periapsis_and_velocity_at_infinity(self):
        flyby = FlybyData("sun", 19.2 * AU, 20_000.0, 100.0 * AU)

        solution = solve_flyby(self.anchor, self.visitor, flyby)
        self.visitor.position_m = solution.position_m
        self.visitor.velocity_mps = solution.velocity_mps

        mu = G * (self.anchor.mass_kg + self.visitor.mass_kg)
        relative_position = [
            solution.position_m[axis] - self.anchor.position_m[axis]
            for axis in range(3)
        ]
        relative_velocity = [
            solution.velocity_mps[axis] - self.anchor.velocity_mps[axis]
            for axis in range(3)
        ]
        radius = math.sqrt(sum(value * value for value in relative_position))
        speed_sq = sum(value * value for value in relative_velocity)
        energy = 0.5 * speed_sq - mu / radius
        angular_momentum = (
            relative_position[0] * relative_velocity[1]
            - relative_position[1] * relative_velocity[0]
        )
        eccentricity = math.sqrt(1.0 + 2.0 * energy * angular_momentum**2 / mu**2)
        periapsis = angular_momentum**2 / (mu * (1.0 + eccentricity))

        self.assertAlmostEqual(radius / AU, 100.0, places=9)
        self.assertAlmostEqual(periapsis / AU, 19.2, places=9)
        self.assertAlmostEqual(math.sqrt(2.0 * energy), 20_000.0, places=6)
        self.assertLess(radial_velocity_mps(self.anchor, self.visitor), 0.0)
        self.assertGreater(solution.orbit.eccentricity, 1.0)
        self.assertLess(solution.orbit.semi_major_axis_m, 0.0)

    def test_three_dimensional_orientation_rotates_state(self):
        planar = solve_flyby(
            self.anchor,
            self.visitor,
            FlybyData("sun", AU, 10_000.0, 10.0 * AU),
        )
        inclined = solve_flyby(
            self.anchor,
            self.visitor,
            FlybyData("sun", AU, 10_000.0, 10.0 * AU, 45.0, 20.0, 30.0),
        )

        self.assertAlmostEqual(math.dist(planar.position_m, self.anchor.position_m), 10.0 * AU, delta=1.0)
        self.assertAlmostEqual(math.dist(inclined.position_m, self.anchor.position_m), 10.0 * AU, delta=1.0)
        self.assertNotEqual(inclined.position_m[2], 0.0)

    def test_starting_distance_must_exceed_periapsis(self):
        with self.assertRaisesRegex(ModelError, "start_distance_m"):
            solve_flyby(
                self.anchor,
                self.visitor,
                FlybyData("sun", 10.0 * AU, 10_000.0, 10.0 * AU),
            )


if __name__ == "__main__":
    unittest.main()
