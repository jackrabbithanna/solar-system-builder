import unittest

import numpy as np

from src.constants import AU, DAY, G, SOLAR_MASS
from src.models import Body
from src.physics import SimulationState, acceleration, advance, step
from src.presets import load_builtin_solar_system


class PhysicsTests(unittest.TestCase):
    def test_two_body_orbit_remains_bounded(self):
        sun = Body("Sun", "star", SOLAR_MASS, 1, [0, 0, 0], [0, 0, 0], "#fff")
        earth = Body(
            "Earth",
            "planet",
            5.97237e24,
            1,
            [AU, 0, 0],
            [0, (G * SOLAR_MASS / AU) ** 0.5, 0],
            "#00f",
        )
        state = SimulationState.from_bodies([sun, earth])

        for _ in range(365):
            state = step(state, DAY, "newtonian")

        distance = np.linalg.norm(state.positions_m[1] - state.positions_m[0])
        self.assertLess(abs(distance - AU), 0.03 * AU)

    def test_forward_then_backward_returns_near_start(self):
        bodies = [
            Body("Sun", "star", SOLAR_MASS, 1, [0, 0, 0], [0, 0, 0], "#fff"),
            Body("Earth", "planet", 5.97237e24, 1, [AU, 0, 0], [0, 29780, 0], "#00f"),
        ]
        start = SimulationState.from_bodies(bodies)
        forward = step(start, DAY, "post_newtonian")
        backward = step(forward, -DAY, "post_newtonian")

        self.assertTrue(np.allclose(start.positions_m, backward.positions_m, rtol=0, atol=2.0e5))
        self.assertTrue(np.allclose(start.velocities_mps, backward.velocities_mps, rtol=0, atol=0.2))

    def test_advance_keeps_mercury_bounded_with_30_day_ui_steps(self):
        state = SimulationState.from_bodies(load_builtin_solar_system().bodies)
        mercury_distances = []

        for _ in range(120):
            state = advance(state, 30 * DAY, "post_newtonian")
            mercury_distances.append(np.linalg.norm(state.positions_m[1] - state.positions_m[0]) / AU)

        self.assertLess(max(mercury_distances), 0.42)
        self.assertGreater(min(mercury_distances), 0.35)

    def test_direct_30_day_step_is_too_coarse_for_mercury(self):
        state = SimulationState.from_bodies(load_builtin_solar_system().bodies)

        for _ in range(24):
            state = step(state, 30 * DAY, "post_newtonian")

        mercury_distance = np.linalg.norm(state.positions_m[1] - state.positions_m[0]) / AU
        self.assertGreater(mercury_distance, 2.0)

    def test_advance_splits_negative_time(self):
        start = SimulationState.from_bodies(load_builtin_solar_system().bodies)
        forward = advance(start, 30 * DAY, "post_newtonian")
        backward = advance(forward, -30 * DAY, "post_newtonian")

        self.assertAlmostEqual(backward.elapsed_s, start.elapsed_s, places=6)
        self.assertTrue(np.allclose(start.positions_m, backward.positions_m, rtol=0, atol=1.2e8))

    def test_post_newtonian_differs_from_newtonian(self):
        masses = np.array([SOLAR_MASS, 3.3011e23])
        positions = np.array([[0, 0, 0], [0.387 * AU, 0, 0]], dtype=float)
        velocities = np.array([[0, 0, 0], [0, 47870, 0]], dtype=float)

        newtonian = acceleration(masses, positions, velocities, "newtonian")
        post_newtonian = acceleration(masses, positions, velocities, "post_newtonian")

        self.assertGreater(np.linalg.norm(post_newtonian - newtonian), 0)

    def test_invalid_arrays_fail(self):
        with self.assertRaises(ValueError):
            acceleration(np.array([0.0]), np.zeros((1, 3)), np.zeros((1, 3)))


if __name__ == "__main__":
    unittest.main()
