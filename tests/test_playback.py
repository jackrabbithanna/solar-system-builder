import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from src import playback
from src.constants import DAY
from src.models import Body, SystemGroup, SystemSettings
from src.physics import SimulationState
from src.presets import load_builtin_solar_systems
from src.scales import derived_max_step_s, focus_target_body_indices, focused_visible_step_s


class PlaybackTests(unittest.TestCase):
    def test_auto_collapses_moon_into_planet_for_cost_and_timestep(self):
        system = next(
            item for item in load_builtin_solar_systems() if item.id == "builtin-solar-system"
        )
        session = playback.SimulationSession.from_bodies(system.bodies)
        decision = session.physics_decision(
            system.bodies,
            system.groups,
            system.settings,
            0,
            None,
            None,
            system.settings.visible_step_s,
        )
        indices_by_id = {body.id: index for index, body in enumerate(system.bodies)}

        self.assertTrue(decision.moons_collapsed)
        self.assertNotIn(indices_by_id["moon"], decision.physics_indices)
        self.assertNotIn(indices_by_id["moon"], decision.display_indices)
        earth_row = decision.physics_indices.index(indices_by_id["earth"])
        self.assertEqual(
            decision.physics_memberships[earth_row],
            [indices_by_id["earth"], indices_by_id["moon"]],
        )
        proxy_bodies = playback.proxy_bodies_for_memberships(
            system.bodies,
            decision.physics_memberships,
        )
        self.assertEqual(
            decision.max_step_s,
            derived_max_step_s(proxy_bodies, system.settings.accuracy_profile),
        )
        self.assertGreater(
            decision.max_step_s,
            derived_max_step_s(system.bodies, system.settings.accuracy_profile),
        )
        self.assertEqual(
            decision.estimated_work_units,
            playback.estimate_work_units(
                system.settings.visible_step_s,
                decision.max_step_s,
                len(decision.physics_indices),
            ),
        )

    def test_explicit_full_simulates_but_does_not_display_moon_at_star_scale(self):
        system = next(
            item for item in load_builtin_solar_systems() if item.id == "builtin-solar-system"
        )
        settings = replace(system.settings, simulation_scope="full_nbody")
        session = playback.SimulationSession.from_bodies(system.bodies)
        decision = session.physics_decision(
            system.bodies,
            system.groups,
            settings,
            0,
            None,
            None,
            settings.visible_step_s,
        )
        moon_index = next(index for index, body in enumerate(system.bodies) if body.id == "moon")

        self.assertFalse(decision.moons_collapsed)
        self.assertIn(moon_index, decision.physics_indices)
        self.assertNotIn(moon_index, decision.display_indices)
        self.assertTrue(all(len(membership) == 1 for membership in decision.physics_memberships))

    def test_explicit_full_skips_hidden_moon_trails_at_star_scale(self):
        bodies = [
            _body("sun", kind="star", mass=10.0),
            _body("earth", kind="planet", position=[10.0, 0.0, 0.0], parent_id="sun"),
            _body("moon", kind="moon", position=[11.0, 0.0, 0.0], parent_id="earth"),
        ]
        settings = SystemSettings(
            simulation_scope="full_nbody",
            trail_sample_interval_s=1.0,
        )
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], settings, 0, None, None, 1.0)
        result_state = job.state.copy()
        result_state.positions_m[:, 0] += 1.0
        result_state.elapsed_s = 1.0

        self.assertTrue(
            session.apply_result(
                playback.SimulationJobResult(
                    job.plan,
                    result_state,
                    [result_state.positions_m.copy()],
                ),
                bodies,
                [],
                settings,
            )
        )
        self.assertEqual(session.trails[1], [(11.0, 0.0)])
        self.assertEqual(session.trails[2], [])

    def test_focused_timing_collapses_moons_only_above_planet_detail(self):
        system = next(
            item for item in load_builtin_solar_systems() if item.id == "builtin-solar-system"
        )

        star_bodies = playback.focused_timing_bodies(
            system.bodies,
            system.groups,
            "body:sun",
            collapse_moons=True,
        )
        planet_bodies = playback.focused_timing_bodies(
            system.bodies,
            system.groups,
            "body:earth",
            collapse_moons=True,
        )

        self.assertNotIn("moon", {body.id for body in star_bodies})
        self.assertIn("moon", {body.id for body in planet_bodies})
        self.assertGreater(
            focused_visible_step_s(star_bodies, "balanced"),
            focused_visible_step_s(planet_bodies, "balanced"),
        )

    def test_planet_focus_expands_moon_and_keeps_host_star_context(self):
        bodies = [
            _body("sun", kind="star", mass=10.0),
            _body("earth", kind="planet", mass=2.0, position=[10.0, 0.0, 0.0], parent_id="sun"),
            _body("moon", kind="moon", position=[11.0, 0.0, 0.0], parent_id="earth"),
        ]
        settings = SystemSettings(simulation_scope="hybrid_focused_context")
        session = playback.SimulationSession.from_bodies(bodies)

        decision = session.physics_decision(
            bodies, [], settings, 1, None, "body:earth", 1.0
        )

        self.assertFalse(decision.moons_collapsed)
        self.assertEqual(decision.physics_indices, [1, 2])
        self.assertEqual(decision.display_indices, [1, 2])
        self.assertEqual(decision.physics_memberships, [[1], [2]])

    def test_proxy_state_conserves_mass_barycenter_and_momentum(self):
        state = SimulationState(
            masses_kg=np.array([10.0, 3.0, 1.0]),
            positions_m=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [14.0, 0.0, 0.0]]),
            velocities_mps=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [5.0, 0.0, 0.0]]),
        )

        proxy = playback.simulation_state_for_memberships(state, [[0], [1, 2]])

        self.assertEqual(proxy.masses_kg.tolist(), [10.0, 4.0])
        self.assertEqual(proxy.positions_m[:, 0].tolist(), [0.0, 11.0])
        self.assertEqual(proxy.velocities_mps[:, 0].tolist(), [0.0, 2.0])
        expanded = playback.expand_membership_position_samples(
            [np.array([[1.0, 0.0, 0.0], [21.0, 0.0, 0.0]])],
            state,
            [[0], [1, 2]],
        )
        self.assertEqual(expanded[0][:, 0].tolist(), [1.0, 20.0, 24.0])

    def test_proxy_result_rigidly_carries_moon_and_filters_its_trail(self):
        bodies = [
            _body("sun", kind="star", mass=10.0),
            _body("earth", kind="planet", mass=3.0, position=[10.0, 0.0, 0.0], velocity=[1.0, 0.0, 0.0], parent_id="sun"),
            _body("moon", kind="moon", mass=1.0, position=[14.0, 0.0, 0.0], velocity=[5.0, 0.0, 0.0], parent_id="earth"),
        ]
        settings = SystemSettings(
            simulation_scope="focused_subsystem",
            trail_sample_interval_s=1.0,
        )
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], settings, 0, None, "body:sun", 1.0)
        result_state = job.state.copy()
        result_state.positions_m += np.array([10.0, 0.0, 0.0])
        result_state.velocities_mps += np.array([2.0, 0.0, 0.0])
        result_state.elapsed_s = 1.0
        result = playback.SimulationJobResult(
            job.plan,
            result_state,
            [result_state.positions_m.copy()],
        )

        self.assertTrue(session.apply_result(result, bodies, [], settings))

        self.assertEqual(bodies[2].position_m[0] - bodies[1].position_m[0], 4.0)
        self.assertEqual(bodies[2].velocity_mps[0] - bodies[1].velocity_mps[0], 4.0)
        self.assertEqual(session.trails[1], [(20.0, 0.0)])
        self.assertEqual(session.trails[2], [])

    def test_work_estimate_uses_absolute_substeps_and_body_count_squared(self):
        self.assertEqual(playback.estimate_work_units(10.0, 3.0, 4), 64)
        self.assertEqual(playback.estimate_work_units(-10.0, 3.0, 4), 64)
        self.assertEqual(playback.estimate_work_units(0.0, 3.0, 4), 0)

    def test_auto_policy_uses_full_for_small_presets_and_overview_for_alpha_centauri(self):
        systems = {system.name: system for system in load_builtin_solar_systems()}

        for name in ("Solar System", "Dwarf Planets"):
            system = systems[name]
            decision = playback.SimulationSession.from_bodies(system.bodies).physics_decision(
                system.bodies, system.groups, system.settings, 0, None, None, system.settings.visible_step_s
            )
            self.assertEqual(decision.policy, "full_nbody")
            self.assertFalse(decision.auto_approximation)

        alpha = systems["Alpha Centauri"]
        decision = playback.SimulationSession.from_bodies(alpha.bodies).physics_decision(
            alpha.bodies, alpha.groups, alpha.settings, 0, None, None, alpha.settings.visible_step_s
        )
        self.assertEqual(decision.policy, "system_overview")
        self.assertTrue(decision.auto_approximation)
        full_work = playback.estimate_work_units(
            alpha.settings.visible_step_s,
            derived_max_step_s(alpha.bodies, alpha.settings.accuracy_profile),
            len(alpha.bodies),
        )
        self.assertGreater(
            full_work * playback.INITIAL_MS_PER_WORK_UNIT,
            playback.AUTO_PHYSICS_BUDGET_MS,
        )

    def test_focused_alpha_centauri_simulates_all_bodies_but_displays_proxima(self):
        alpha = next(system for system in load_builtin_solar_systems() if system.name == "Alpha Centauri")
        target = "body:proxima-centauri"
        focused_indices = focus_target_body_indices(alpha.bodies, alpha.groups, target)
        focused_step = focused_visible_step_s(
            [alpha.bodies[index] for index in focused_indices],
            alpha.settings.accuracy_profile,
        )
        settings = replace(alpha.settings, visible_step_s=focused_step)
        session = playback.SimulationSession.from_bodies(alpha.bodies)

        decision = session.physics_decision(alpha.bodies, alpha.groups, settings, 0, None, target, focused_step)

        self.assertEqual(decision.policy, "full_nbody")
        self.assertEqual(decision.physics_indices, list(range(len(alpha.bodies))))
        self.assertEqual(decision.display_indices, focused_indices)

    def test_applied_auto_approximation_locks_until_state_replacement(self):
        alpha = next(system for system in load_builtin_solar_systems() if system.name == "Alpha Centauri")
        session = playback.SimulationSession.from_bodies(alpha.bodies)
        job = session.create_job(alpha.bodies, alpha.groups, alpha.settings, 0, None, None, alpha.settings.visible_step_s)
        result = playback.run_simulation_job(job)

        self.assertTrue(session.apply_result(result, alpha.bodies, alpha.groups, alpha.settings))
        self.assertTrue(session.auto_approximation_locked)

        target = "body:proxima-centauri"
        focused_indices = focus_target_body_indices(alpha.bodies, alpha.groups, target)
        focused_step = focused_visible_step_s(
            [alpha.bodies[index] for index in focused_indices],
            alpha.settings.accuracy_profile,
        )
        focused_settings = replace(alpha.settings, visible_step_s=focused_step)
        locked_decision = session.physics_decision(
            alpha.bodies, alpha.groups, focused_settings, 0, None, target, focused_step
        )
        self.assertEqual(locked_decision.policy, "hybrid_focused_context")
        self.assertTrue(locked_decision.auto_approximation)

        session.replace_bodies(alpha.bodies)

        self.assertFalse(session.auto_approximation_locked)
        reset_decision = session.physics_decision(
            alpha.bodies, alpha.groups, focused_settings, 0, None, target, focused_step
        )
        self.assertEqual(reset_decision.policy, "full_nbody")

    def test_full_job_timing_calibrates_estimator(self):
        bodies = [_body(str(index), kind="star") for index in range(11)]
        settings = SystemSettings(visible_step_s=DAY, simulation_scope="full_nbody")
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], settings, 0, None, None, DAY)
        result = playback.SimulationJobResult(
            job.plan,
            session.state.copy(),
            [session.state.positions_m.copy()],
            worker_duration_ms=float(job.plan.estimated_work_units),
        )

        self.assertTrue(session.apply_result(result, bodies, [], settings))
        self.assertAlmostEqual(session.ms_per_work_unit, 0.265)

    def test_full_focus_result_updates_hidden_bodies_and_aggregates_inset_trails(self):
        bodies = [
            _body("star", kind="star", position=[0.0, 0.0, 0.0]),
            _body("planet", parent_id="star", position=[1.0, 0.0, 0.0]),
            _body("other", kind="star", position=[100.0, 0.0, 0.0]),
        ]
        settings = SystemSettings(simulation_scope="auto", trail_sample_interval_s=1.0)
        session = playback.SimulationSession.from_bodies(bodies)
        job = session.create_job(bodies, [], settings, 0, None, "body:star", 1.0)
        positions = np.array([[10.0, 0.0, 0.0], [20.0, 0.0, 0.0], [30.0, 0.0, 0.0]])
        result = playback.SimulationJobResult(
            job.plan,
            _state(positions.tolist(), elapsed_s=1.0),
            [positions],
        )

        self.assertEqual(job.plan.effective_policy, "full_nbody")
        self.assertEqual(job.plan.display_indices, [0, 1])
        self.assertTrue(session.apply_result(result, bodies, [], settings))

        self.assertEqual(bodies[2].position_m[0], 30.0)
        self.assertEqual(session.context_trails["star"], [(15.0, 0.0)])
        self.assertEqual(session.context_trails["context-other"], [(30.0, 0.0)])

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
            )

        self.assertEqual(focused_result, expected_result)
        self.assertIsNone(context_result)
        advance.assert_called_once_with(state, 10.0, "post_newtonian", 1.0)

    def test_hybrid_worker_couples_context_then_splits_results(self):
        focused = _state([[1.0, 0.0, 0.0]], elapsed_s=2.0)
        context = _state([[10.0, 0.0, 0.0]], elapsed_s=2.0)
        combined_result = _state([[2.0, 0.0, 0.0], [20.0, 0.0, 0.0]], elapsed_s=3.0)
        combined_sample = combined_result.positions_m.copy()

        with patch(
            "src.playback.advance_with_samples",
            return_value=(combined_result, [combined_sample]),
        ) as advance:
            focused_result, context_result = playback.advance_hybrid_simulations(
                focused,
                context,
                1.0,
                0.25,
            )

        combined_input = advance.call_args.args[0]
        self.assertEqual(combined_input.positions_m.shape, (2, 3))
        self.assertEqual(focused_result[0].positions_m[0][0], 2.0)
        self.assertEqual(context_result[0].positions_m[0][0], 20.0)
        self.assertEqual(focused_result[1][0].shape, (1, 3))
        self.assertEqual(context_result[1][0].shape, (1, 3))
        advance.assert_called_once_with(combined_input, 1.0, "post_newtonian", 0.25)

    def test_planetary_focus_job_keeps_host_star_as_coupled_context(self):
        bodies = [
            _body("sun", kind="star", mass=10.0),
            _body("earth", kind="planet", mass=2.0, position=[10.0, 0.0, 0.0], parent_id="sun"),
            _body("moon", kind="moon", position=[11.0, 0.0, 0.0], parent_id="earth"),
        ]
        settings = SystemSettings(simulation_scope="hybrid_focused_context")
        session = playback.SimulationSession.from_bodies(bodies)

        job = session.create_job(bodies, [], settings, 1, None, "body:earth", 1.0)

        self.assertEqual(job.plan.active_indices, [1, 2])
        self.assertEqual(job.plan.display_indices, [1, 2])
        self.assertEqual(job.plan.context_entity_ids, ["context-sun"])
        self.assertEqual(job.context_state.masses_kg.tolist(), [10.0])
        self.assertEqual(job.plan.estimated_work_units, 9)

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
