import unittest
from unittest.mock import patch

import numpy as np

from src import playback
from src.models import Body, SystemGroup, SystemSettings
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

    def test_session_job_contains_copied_worker_state(self):
        bodies = [_body("a"), _body("b", position=[1.0, 0.0, 0.0])]
        session = playback.SimulationSession.from_bodies(bodies)

        job = session.create_job(
            bodies,
            [],
            SystemSettings(simulation_scope="full_nbody"),
            0,
            None,
            None,
            1.0,
        )
        job.state.positions_m[0][0] = 99.0

        self.assertEqual(job.plan.mode, "body_detail")
        self.assertEqual(session.state.positions_m[0][0], 0.0)
        self.assertEqual(bodies[0].position_m[0], 0.0)

    def test_session_ignores_stale_generation_result(self):
        bodies = [_body("a")]
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], SystemSettings(), 0, None, None, 1.0)
        result = playback.SimulationJobResult(
            job.plan,
            _state([[10.0, 0.0, 0.0]], elapsed_s=1.0),
            [np.array([[10.0, 0.0, 0.0]])],
        )

        session.increment_generation()

        self.assertFalse(session.apply_result(result, bodies, [], SystemSettings()))
        self.assertEqual(bodies[0].position_m[0], 0.0)

    def test_session_applies_body_detail_result_to_active_bodies_and_trails(self):
        bodies = [
            _body("a", position=[0.0, 0.0, 0.0]),
            _body("b", position=[1.0, 0.0, 0.0]),
        ]
        settings = SystemSettings(simulation_scope="full_nbody", trail_sample_interval_s=1.0)
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], settings, 0, None, None, 1.0)
        result = playback.SimulationJobResult(
            job.plan,
            _state([[10.0, 0.0, 0.0], [20.0, 0.0, 0.0]], elapsed_s=1.0),
            [np.array([[10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])],
        )

        self.assertTrue(session.apply_result(result, bodies, [], settings))

        self.assertEqual(bodies[0].position_m[0], 10.0)
        self.assertEqual(bodies[1].position_m[0], 20.0)
        self.assertEqual(session.trails[0], [(10.0, 0.0)])
        self.assertEqual(session.trails[1], [(20.0, 0.0)])

    def test_session_applies_system_overview_without_mutating_body_positions(self):
        bodies = [
            _body("a", position=[0.0, 0.0, 0.0]),
            _body("b", position=[100.0, 0.0, 0.0]),
        ]
        groups = [
            SystemGroup(id="ga", name="A", kind="system", body_ids=["a"]),
            SystemGroup(id="gb", name="B", kind="system", body_ids=["b"]),
        ]
        settings = SystemSettings(simulation_scope="system_overview", trail_sample_interval_s=1.0)
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, groups, settings, 0, None, None, 1.0)
        result = playback.SimulationJobResult(
            job.plan,
            _state([[5.0, 0.0, 0.0], [105.0, 0.0, 0.0]], elapsed_s=1.0),
            [np.array([[5.0, 0.0, 0.0], [105.0, 0.0, 0.0]])],
        )

        self.assertEqual(job.plan.mode, "system_overview")
        self.assertTrue(session.apply_result(result, bodies, groups, settings))

        self.assertEqual(bodies[0].position_m[0], 0.0)
        self.assertEqual(bodies[1].position_m[0], 100.0)
        self.assertEqual(session.state.elapsed_s, 1.0)
        self.assertEqual(session.overview_trails["ga"], [(5.0, 0.0)])
        self.assertEqual(session.overview_trails["gb"], [(105.0, 0.0)])

    def test_session_applies_hybrid_focus_result_with_display_only_context(self):
        bodies = [
            _body("star", kind="star", position=[0.0, 0.0, 0.0]),
            _body("planet", parent_id="star", position=[1.0, 0.0, 0.0]),
            _body("other", kind="star", position=[100.0, 0.0, 0.0]),
        ]
        settings = SystemSettings(simulation_scope="hybrid_focused_context", trail_sample_interval_s=1.0)
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], settings, 0, None, "body:star", 1.0)
        context_state = _state([[110.0, 0.0, 0.0]], elapsed_s=1.0)
        result = playback.SimulationJobResult(
            job.plan,
            _state([[10.0, 0.0, 0.0], [20.0, 0.0, 0.0]], elapsed_s=1.0),
            [np.array([[10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])],
            (context_state, [np.array([[110.0, 0.0, 0.0]])]),
        )

        self.assertEqual(job.plan.mode, "hybrid_focus")
        self.assertTrue(session.apply_result(result, bodies, [], settings))

        self.assertEqual(bodies[0].position_m[0], 10.0)
        self.assertEqual(bodies[1].position_m[0], 20.0)
        self.assertEqual(bodies[2].position_m[0], 100.0)
        self.assertEqual(session.context_state.positions_m[0][0], 110.0)
        self.assertEqual(session.context_trails["context-other"], [(110.0, 0.0)])

    def test_session_replace_bodies_resets_dynamic_state_and_generation(self):
        bodies = [_body("a")]
        session = playback.SimulationSession.from_bodies(bodies)
        session.trails[0].append((1.0, 1.0))
        session.overview_trails["x"] = [(2.0, 2.0)]

        session.replace_bodies([_body("b", position=[3.0, 0.0, 0.0])])

        self.assertEqual(session.generation, 1)
        self.assertEqual(session.trails, [[]])
        self.assertEqual(session.overview_trails, {})
        self.assertEqual(session.state.positions_m[0][0], 3.0)


def _body(
    body_id: str,
    *,
    kind: str = "planet",
    mass: float = 1.0,
    position: list[float] | None = None,
    velocity: list[float] | None = None,
    parent_id: str | None = None,
) -> Body:
    return Body(
        id=body_id,
        name=body_id,
        kind=kind,
        mass_kg=mass,
        radius_m=1.0,
        position_m=position or [0.0, 0.0, 0.0],
        velocity_mps=velocity or [0.0, 0.0, 0.0],
        color="#ffffff",
        parent_id=parent_id,
    )


def _state(positions: list[list[float]], *, elapsed_s: float) -> SimulationState:
    return SimulationState(
        masses_kg=np.ones(len(positions), dtype=float),
        positions_m=np.array(positions, dtype=float),
        velocities_mps=np.zeros((len(positions), 3), dtype=float),
        elapsed_s=elapsed_s,
    )


if __name__ == "__main__":
    unittest.main()
