import unittest
from unittest.mock import patch

import numpy as np

from src import playback
from src.models import Body
from src.physics import SimulationState


class PlaybackTests(unittest.TestCase):
    def test_trail_appending_uses_sampled_positions_and_caps_limit(self):
        bodies = [_body("a"), _body("b")]
        trails = [[(-10.0, -10.0)], []]
        samples = [
            np.array([[1.0, 2.0, 0.0], [10.0, 20.0, 0.0]]),
            np.array([[3.0, 4.0, 0.0], [30.0, 40.0, 0.0]]),
            np.array([[5.0, 6.0, 0.0], [50.0, 60.0, 0.0]]),
        ]

        last_elapsed = playback.append_body_trails(
            trails,
            bodies,
            samples,
            [0, 1],
            0.0,
            3.0,
            0.0,
            1.0,
            limit=2,
        )

        self.assertEqual(last_elapsed, 3.0)
        self.assertEqual(trails[0], [(3.0, 4.0), (5.0, 6.0)])
        self.assertEqual(trails[1], [(30.0, 40.0), (50.0, 60.0)])

    def test_negative_step_returns_frame_samples_with_correct_elapsed_direction(self):
        samples = [
            np.array([[3.0, 0.0, 0.0]]),
            np.array([[2.0, 0.0, 0.0]]),
            np.array([[1.0, 0.0, 0.0]]),
        ]

        selected, last_elapsed = playback.select_trail_samples(samples, 3.0, 0.0, 3.0, 1.0)

        self.assertEqual(last_elapsed, 0.0)
        self.assertEqual([float(sample[0][0]) for sample in selected], [3.0, 2.0, 1.0])

    def test_stale_generation_results_are_discarded(self):
        self.assertTrue(playback.should_apply_generation(4, 4))
        self.assertFalse(playback.should_apply_generation(3, 4))

    def test_worker_helper_calls_advance_with_samples(self):
        state = SimulationState(
            masses_kg=np.array([1.0]),
            positions_m=np.array([[0.0, 0.0, 0.0]]),
            velocities_mps=np.array([[0.0, 0.0, 0.0]]),
            elapsed_s=0.0,
        )
        expected_result = (state.copy(), [])

        with patch("src.playback.advance_with_samples", return_value=expected_result) as advance:
            focused_result, context_result = playback.advance_hybrid_simulations(
                state,
                None,
                10.0,
                1.0,
                2.0,
            )

        self.assertEqual(focused_result, expected_result)
        self.assertIsNone(context_result)
        advance.assert_called_once_with(state, 10.0, "post_newtonian", 1.0)

    def test_state_copy_and_merge_transition_active_indices(self):
        state = SimulationState(
            masses_kg=np.array([1.0, 2.0]),
            positions_m=np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]]),
            velocities_mps=np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]]),
            elapsed_s=5.0,
        )

        active = playback.simulation_state_for_indices(state, [1])
        active.positions_m[0][0] = 9.0
        active.velocities_mps[0][0] = 8.0
        active.elapsed_s = 6.0
        playback.merge_active_state(state, active, [1])

        self.assertEqual(state.positions_m[0][0], 0.0)
        self.assertEqual(state.positions_m[1][0], 9.0)
        self.assertEqual(state.velocities_mps[1][0], 8.0)
        self.assertEqual(state.elapsed_s, 6.0)


def _body(body_id: str) -> Body:
    return Body(
        id=body_id,
        name=body_id,
        kind="planet",
        mass_kg=1.0,
        radius_m=1.0,
        position_m=[0.0, 0.0, 0.0],
        velocity_mps=[0.0, 0.0, 0.0],
        color="#ffffff",
    )


if __name__ == "__main__":
    unittest.main()
