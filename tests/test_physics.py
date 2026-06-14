import unittest

import numpy as np

from src.constants import AU, DAY, G, SOLAR_MASS, YEAR
from src.models import Body
from src.physics import SimulationState, acceleration, advance, advance_with_samples, step
from src.presets import load_builtin_solar_system, load_builtin_solar_systems
from src.scales import (
    LIGHT_YEAR,
    active_body_indices,
    context_overview_entities,
    derived_max_step_s,
    derived_overview_max_step_s,
    distance_between_bodies_m,
    effective_simulation_scope,
    focused_visible_step_s,
    focus_target_body_indices,
    format_distance,
    format_elapsed_time,
    recommended_trail_sample_interval_s,
    system_overview_entities,
)


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

        self.assertLess(max(mercury_distances), 0.50)
        self.assertGreater(min(mercury_distances), 0.29)

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

    def test_advance_with_samples_matches_advance(self):
        start = SimulationState.from_bodies(load_builtin_solar_system().bodies)

        sampled, samples = advance_with_samples(start, 30 * DAY, "post_newtonian")
        advanced = advance(start, 30 * DAY, "post_newtonian")

        self.assertEqual(len(samples), 30)
        self.assertTrue(np.array_equal(sampled.positions_m, advanced.positions_m))
        self.assertTrue(np.array_equal(sampled.velocities_mps, advanced.velocities_mps))
        self.assertEqual(sampled.elapsed_s, advanced.elapsed_s)
        self.assertTrue(np.array_equal(samples[-1], sampled.positions_m))

    def test_advance_with_samples_handles_negative_time(self):
        start = SimulationState.from_bodies(load_builtin_solar_system().bodies)

        sampled, samples = advance_with_samples(start, -3 * DAY, "post_newtonian")
        advanced = advance(start, -3 * DAY, "post_newtonian")

        self.assertEqual(len(samples), 3)
        self.assertTrue(np.array_equal(sampled.positions_m, advanced.positions_m))
        self.assertEqual(sampled.elapsed_s, advanced.elapsed_s)
        self.assertLess(sampled.elapsed_s, start.elapsed_s)

    def test_advance_with_samples_zero_duration_returns_no_samples(self):
        start = SimulationState.from_bodies(load_builtin_solar_system().bodies)

        sampled, samples = advance_with_samples(start, 0.0, "post_newtonian")

        self.assertEqual(samples, [])
        self.assertIsNot(sampled, start)
        self.assertTrue(np.array_equal(sampled.positions_m, start.positions_m))
        self.assertEqual(sampled.elapsed_s, start.elapsed_s)

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

    def test_alpha_centauri_preset_contents_and_stars_move(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )
        self.assertEqual(alpha_centauri.name, "Alpha Centauri")
        body_ids = {body.id for body in alpha_centauri.bodies}
        self.assertIn("proxima-centauri-b", body_ids)
        self.assertIn("proxima-centauri-d", body_ids)
        self.assertIn("proxima-centauri-c-candidate", body_ids)
        self.assertIn("alpha-centauri-a-candidate", body_ids)
        bodies_by_id = {body.id: body for body in alpha_centauri.bodies}
        self.assertEqual(bodies_by_id["proxima-centauri-b"].parent_id, "proxima-centauri")
        self.assertEqual(bodies_by_id["proxima-centauri-d"].parent_id, "proxima-centauri")
        self.assertEqual(bodies_by_id["proxima-centauri-c-candidate"].parent_id, "proxima-centauri")
        self.assertEqual(bodies_by_id["alpha-centauri-a-candidate"].parent_id, "alpha-centauri-a")

        start = SimulationState.from_bodies(alpha_centauri.bodies)
        state = start

        for _ in range(10):
            state = advance(state, DAY, "post_newtonian")

        star_indices = [
            index
            for index, body in enumerate(alpha_centauri.bodies)
            if body.kind == "star"
        ]
        self.assertEqual(len(star_indices), 3)
        separations_au = [
            np.linalg.norm(start.positions_m[first] - start.positions_m[second]) / AU
            for first in star_indices
            for second in star_indices
            if first < second
        ]
        self.assertGreater(max(separations_au), 10_000.0)
        self.assertGreater(min(separations_au), 10.0)
        for index in star_indices:
            movement = np.linalg.norm(state.positions_m[index] - start.positions_m[index])
            self.assertGreater(movement, 1.0e8)

    def test_alpha_centauri_auto_scope_uses_system_overview(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        scope = effective_simulation_scope(
            alpha_centauri.bodies,
            alpha_centauri.settings.simulation_scope,
            alpha_centauri.settings.view_mode,
            0,
            alpha_centauri.groups,
        )
        entities = system_overview_entities(alpha_centauri.bodies, alpha_centauri.groups)

        self.assertEqual(scope, "system_overview")
        self.assertEqual(
            {entity.id for entity in entities},
            {"alpha-centauri-ab-system", "proxima-centauri-system"},
        )

    def test_alpha_centauri_explicit_stellar_overview_uses_individual_stars(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        active_indices = active_body_indices(
            alpha_centauri.bodies,
            "stellar_overview",
            alpha_centauri.settings.view_mode,
            0,
        )
        active_ids = {alpha_centauri.bodies[index].id for index in active_indices}

        self.assertEqual(
            active_ids,
            {"alpha-centauri-a", "alpha-centauri-b", "proxima-centauri"},
        )

    def test_alpha_centauri_focused_scope_selects_local_children(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )
        proxima_index = next(
            index
            for index, body in enumerate(alpha_centauri.bodies)
            if body.id == "proxima-centauri-b"
        )

        active_indices = active_body_indices(
            alpha_centauri.bodies,
            "focused_subsystem",
            alpha_centauri.settings.view_mode,
            proxima_index,
            alpha_centauri.groups,
            None,
        )
        active_ids = {alpha_centauri.bodies[index].id for index in active_indices}

        self.assertEqual(
            active_ids,
            {
                "proxima-centauri",
                "proxima-centauri-b",
                "proxima-centauri-d",
                "proxima-centauri-c-candidate",
            },
        )

    def test_alpha_centauri_focused_ab_group_selects_binary_and_children(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        active_indices = active_body_indices(
            alpha_centauri.bodies,
            "focused_subsystem",
            alpha_centauri.settings.view_mode,
            0,
            alpha_centauri.groups,
            "alpha-centauri-ab-system",
        )
        active_ids = {alpha_centauri.bodies[index].id for index in active_indices}

        self.assertEqual(
            active_ids,
            {
                "alpha-centauri-a",
                "alpha-centauri-b",
                "alpha-centauri-a-candidate",
            },
        )

    def test_alpha_centauri_focused_proxima_group_selects_proxima_children(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        active_indices = active_body_indices(
            alpha_centauri.bodies,
            "focused_subsystem",
            alpha_centauri.settings.view_mode,
            0,
            alpha_centauri.groups,
            "proxima-centauri-system",
        )
        active_ids = {alpha_centauri.bodies[index].id for index in active_indices}

        self.assertEqual(
            active_ids,
            {
                "proxima-centauri",
                "proxima-centauri-b",
                "proxima-centauri-d",
                "proxima-centauri-c-candidate",
            },
        )

    def test_alpha_centauri_body_focus_selects_star_and_planets(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        active_indices = focus_target_body_indices(
            alpha_centauri.bodies,
            alpha_centauri.groups,
            "body:proxima-centauri",
        )
        active_ids = {alpha_centauri.bodies[index].id for index in active_indices}

        self.assertEqual(
            active_ids,
            {
                "proxima-centauri",
                "proxima-centauri-b",
                "proxima-centauri-d",
                "proxima-centauri-c-candidate",
            },
        )

    def test_alpha_centauri_body_focus_supports_star_without_group(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        active_indices = active_body_indices(
            alpha_centauri.bodies,
            "hybrid_focused_context",
            "follow_selected",
            0,
            alpha_centauri.groups,
            None,
            "body:alpha-centauri-a",
        )
        active_ids = {alpha_centauri.bodies[index].id for index in active_indices}

        self.assertEqual(active_ids, {"alpha-centauri-a", "alpha-centauri-a-candidate"})

    def test_body_focus_descends_recursively_for_future_moons(self):
        bodies = [
            Body("Star", "star", SOLAR_MASS, 1, [0, 0, 0], [0, 0, 0], "#fff", id="star"),
            Body("Planet", "planet", 1.0e24, 1, [AU, 0, 0], [0, 1, 0], "#00f", id="planet", parent_id="star"),
            Body("Moon", "moon", 1.0e22, 1, [AU + 1.0e8, 0, 0], [0, 1, 0], "#aaa", id="moon", parent_id="planet"),
        ]

        active_indices = focus_target_body_indices(bodies, [], "body:star")
        active_ids = {bodies[index].id for index in active_indices}

        self.assertEqual(active_ids, {"star", "planet", "moon"})

    def test_auto_scope_uses_hybrid_when_focus_target_exists(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        scope = effective_simulation_scope(
            alpha_centauri.bodies,
            "auto",
            "follow_selected",
            0,
            alpha_centauri.groups,
            "body:proxima-centauri",
        )

        self.assertEqual(scope, "hybrid_focused_context")

    def test_context_entities_exclude_focused_target(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )

        entities = context_overview_entities(
            alpha_centauri.bodies,
            alpha_centauri.groups,
            "body:proxima-centauri",
        )

        self.assertEqual({entity.id for entity in entities}, {"alpha-centauri-ab-system"})

    def test_focused_visible_step_uses_local_orbits(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )
        proxima_bodies = [
            alpha_centauri.bodies[index]
            for index in focus_target_body_indices(
                alpha_centauri.bodies,
                alpha_centauri.groups,
                "body:proxima-centauri",
            )
        ]
        all_entities = system_overview_entities(alpha_centauri.bodies, alpha_centauri.groups)

        focused_step = focused_visible_step_s(proxima_bodies, "balanced")
        overview_step = derived_overview_max_step_s(all_entities, "balanced")

        self.assertGreaterEqual(focused_step, 7.0 * DAY)
        self.assertLess(focused_step, YEAR)
        self.assertGreater(overview_step, focused_step)

    def test_alpha_centauri_overview_policy_ignores_planetary_timestep(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )
        full_step = derived_max_step_s(alpha_centauri.bodies, "fast")
        overview_bodies = [
            alpha_centauri.bodies[index]
            for index in active_body_indices(
                alpha_centauri.bodies,
                "stellar_overview",
                alpha_centauri.settings.view_mode,
                0,
            )
        ]
        overview_step = derived_max_step_s(overview_bodies, "fast")

        self.assertEqual(full_step, 0.5 * DAY)
        self.assertGreaterEqual(overview_step, 30.0 * DAY)
        self.assertGreater(overview_step, full_step)

    def test_alpha_centauri_system_overview_entities_are_group_barycenters(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )
        entities = system_overview_entities(alpha_centauri.bodies, alpha_centauri.groups)
        entities_by_id = {entity.id: entity for entity in entities}

        self.assertEqual(set(entities_by_id), {"alpha-centauri-ab-system", "proxima-centauri-system"})
        ab = entities_by_id["alpha-centauri-ab-system"]
        proxima = entities_by_id["proxima-centauri-system"]
        body_by_id = {body.id: body for body in alpha_centauri.bodies}
        expected_ab_mass = (
            body_by_id["alpha-centauri-a"].mass_kg
            + body_by_id["alpha-centauri-b"].mass_kg
            + body_by_id["alpha-centauri-a-candidate"].mass_kg
        )
        expected_proxima_mass = (
            body_by_id["proxima-centauri"].mass_kg
            + body_by_id["proxima-centauri-b"].mass_kg
            + body_by_id["proxima-centauri-d"].mass_kg
            + body_by_id["proxima-centauri-c-candidate"].mass_kg
        )

        self.assertTrue(np.isclose(ab.mass_kg, expected_ab_mass))
        self.assertTrue(np.isclose(proxima.mass_kg, expected_proxima_mass))
        self.assertLess(abs(ab.position_m[0]), 0.1 * AU)
        self.assertGreater(proxima.position_m[1], 12_000.0 * AU)

    def test_alpha_centauri_system_overview_policy_allows_deep_time_steps(self):
        alpha_centauri = next(
            system
            for system in load_builtin_solar_systems()
            if system.id == "builtin-binary-system"
        )
        entities = system_overview_entities(alpha_centauri.bodies, alpha_centauri.groups)
        overview_step = derived_overview_max_step_s(entities, "fast")

        self.assertEqual(overview_step, 1_000.0 * YEAR)
        self.assertLess((30.0 * 100.0 * YEAR) / overview_step, 4.0)

    def test_balanced_policy_keeps_solar_system_near_day_scale(self):
        max_step_s = derived_max_step_s(load_builtin_solar_system().bodies, "balanced")

        self.assertGreaterEqual(max_step_s, 0.25 * DAY)
        self.assertLessEqual(max_step_s, 1.5 * DAY)

    def test_policy_allows_larger_outer_system_steps(self):
        bodies = [
            Body("Sun", "star", SOLAR_MASS, 1, [0, 0, 0], [0, 0, 0], "#fff", id="sun"),
            Body(
                "Outer",
                "planet",
                1.0e24,
                1,
                [100.0 * AU, 0, 0],
                [0, (G * SOLAR_MASS / (100.0 * AU)) ** 0.5, 0],
                "#00f",
                id="outer",
                parent_id="sun",
            ),
        ]

        balanced = derived_max_step_s(bodies, "balanced")
        fast = derived_max_step_s(bodies, "fast")

        self.assertGreater(balanced, DAY)
        self.assertGreater(fast, balanced)

    def test_policy_falls_back_without_parent_orbits(self):
        bodies = [Body("Body", "body", 1.0, 1, [0, 0, 0], [0, 0, 0], "#fff")]

        self.assertEqual(derived_max_step_s(bodies, "balanced"), DAY)

    def test_scale_formatting_helpers(self):
        self.assertEqual(format_elapsed_time(3 * DAY), "3.00 days")
        self.assertEqual(format_elapsed_time(2 * YEAR), "2.00 years")
        self.assertEqual(format_elapsed_time(100 * YEAR), "1.00 centuries")
        self.assertEqual(format_elapsed_time(1_000 * YEAR), "1.00 millennia")
        self.assertEqual(format_elapsed_time(1_000_000 * YEAR), "1.00 Myr")
        self.assertEqual(recommended_trail_sample_interval_s(90 * DAY), 90 * DAY)

    def test_distance_formatting_uses_au_then_light_years(self):
        self.assertEqual(format_distance(0.0), "0.0000 AU")
        self.assertEqual(format_distance(39.48 * AU), "39.48 AU")
        self.assertEqual(format_distance(1_500.0 * AU), "1,500.0 AU")
        self.assertEqual(format_distance(10_000.0 * AU), "0.1581 ly")
        self.assertEqual(format_distance(2.0 * LIGHT_YEAR), "2.0000 ly")

    def test_distance_between_bodies_uses_3d_positions(self):
        body = Body("A", "body", 1.0, 1.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], "#fff")
        other = Body("B", "body", 1.0, 1.0, [3.0, 4.0, 12.0], [0.0, 0.0, 0.0], "#fff")

        self.assertEqual(distance_between_bodies_m(body, other), 13.0)


if __name__ == "__main__":
    unittest.main()
