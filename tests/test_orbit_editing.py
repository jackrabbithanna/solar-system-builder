import math
import unittest

import numpy as np

from src.constants import G
from src.models import Body, DataSource, ModelError, OrbitData, SolarSystem, SystemGroup
from src.orbit_editing import (
    generate_binary_pair_orbit,
    generate_body_orbit,
    generate_group_barycenter_orbit,
)
from src.orbits import group_barycenter
from src.playback import SimulationSession


class OrbitEditingTests(unittest.TestCase):
    def test_body_orbit_updates_model_metadata_and_simulation(self):
        parent = _body("star", mass=10.0, position=[2.0, 3.0, 0.0], velocity=[1.0, 2.0, 0.0])
        body = _body("planet", mass=1.0, parent_id=parent.id)
        system = _system([parent, body])
        simulation = _session_with_dynamic_state(system.bodies)
        orbit = OrbitData(semi_major_axis_m=5.0, eccentricity=0.0)
        source = DataSource(source_name="Test catalog")

        result = generate_body_orbit(system, simulation, body.id, orbit, source)

        self.assertIs(result, body)
        self.assertIs(body.orbit, orbit)
        self.assertIs(body.data_source, source)
        self.assertEqual(body.position_m, [7.0, 3.0, 0.0])
        expected_speed = math.sqrt(G * (parent.mass_kg + body.mass_kg) / 5.0)
        self.assertAlmostEqual(body.velocity_mps[0], 1.0)
        self.assertAlmostEqual(body.velocity_mps[1], 2.0 + expected_speed)
        _assert_rebuilt(self, simulation, system.bodies)

    def test_group_barycenter_orbit_preserves_internal_state(self):
        target = _body("target", mass=100.0)
        first = _body("first", mass=3.0, position=[-2.0, 1.0, 0.0], velocity=[0.0, -1.0, 0.0])
        second = _body("second", mass=1.0, position=[6.0, 1.0, 0.0], velocity=[0.0, 3.0, 0.0])
        group = SystemGroup("Pair", "binary_system", [first.id, second.id], id="pair")
        system = _system([target, first, second], [group])
        simulation = _session_with_dynamic_state(system.bodies)
        orbit = OrbitData(semi_major_axis_m=20.0, eccentricity=0.0)
        source = DataSource(source_name="Group source")
        original_separation = second.position_m[0] - first.position_m[0]
        original_velocity_delta = second.velocity_mps[1] - first.velocity_mps[1]

        result = generate_group_barycenter_orbit(
            system,
            simulation,
            group.id,
            "body",
            target.id,
            orbit,
            source,
        )

        center = group_barycenter(system.bodies, system.groups, group.id)
        self.assertIs(result, group)
        self.assertAlmostEqual(center.position_m[0], 20.0)
        self.assertAlmostEqual(center.position_m[1], 0.0)
        self.assertAlmostEqual(second.position_m[0] - first.position_m[0], original_separation)
        self.assertAlmostEqual(second.velocity_mps[1] - first.velocity_mps[1], original_velocity_delta)
        self.assertIs(group.orbit, orbit)
        self.assertEqual(group.orbit_target_type, "body")
        self.assertEqual(group.orbit_target_id, target.id)
        self.assertIs(group.data_source, source)
        _assert_rebuilt(self, simulation, system.bodies)

    def test_group_barycenter_orbit_accepts_group_target(self):
        target_body = _body("target", mass=100.0, position=[3.0, 0.0, 0.0])
        orbiter = _body("orbiter", mass=2.0, position=[50.0, 0.0, 0.0])
        target_group = SystemGroup("Target", "planetary_system", [target_body.id], id="target-group")
        orbiting_group = SystemGroup("Orbiter", "planetary_system", [orbiter.id], id="orbiting-group")
        system = _system([target_body, orbiter], [target_group, orbiting_group])
        simulation = SimulationSession.from_bodies(system.bodies)

        generate_group_barycenter_orbit(
            system,
            simulation,
            orbiting_group.id,
            "group",
            target_group.id,
            OrbitData(semi_major_axis_m=20.0, eccentricity=0.0),
            None,
        )

        center = group_barycenter(system.bodies, system.groups, orbiting_group.id)
        self.assertAlmostEqual(center.position_m[0], 23.0)
        self.assertEqual(orbiting_group.orbit_target_type, "group")
        self.assertEqual(orbiting_group.orbit_target_id, target_group.id)
        self.assertEqual(simulation.generation, 1)

    def test_binary_pair_preserves_barycenter_and_target_metadata(self):
        first = _body("first", mass=3.0, position=[-1.0, 4.0, 0.0], velocity=[1.0, 0.0, 0.0])
        second = _body("second", mass=1.0, position=[3.0, 4.0, 0.0], velocity=[1.0, 0.0, 0.0])
        group = SystemGroup(
            "Pair",
            "binary_system",
            [first.id, second.id],
            id="pair",
            orbit_target_type="body",
            orbit_target_id="external",
        )
        external = _body("external", mass=100.0, position=[100.0, 0.0, 0.0])
        system = _system([first, second, external], [group])
        simulation = _session_with_dynamic_state(system.bodies)
        original_center = group_barycenter(system.bodies, system.groups, group.id)
        orbit = OrbitData(semi_major_axis_m=8.0, eccentricity=0.0)

        result = generate_binary_pair_orbit(system, simulation, group.id, orbit, None)

        center = group_barycenter(system.bodies, system.groups, group.id)
        self.assertIs(result, group)
        np.testing.assert_allclose(center.position_m, original_center.position_m)
        np.testing.assert_allclose(center.velocity_mps, original_center.velocity_mps)
        self.assertAlmostEqual(second.position_m[0] - first.position_m[0], 8.0)
        self.assertIs(group.orbit, orbit)
        self.assertIsNone(group.data_source)
        self.assertEqual(group.orbit_target_type, "body")
        self.assertEqual(group.orbit_target_id, external.id)
        _assert_rebuilt(self, simulation, system.bodies)

    def test_body_failure_does_not_mutate_model_or_simulation(self):
        body = _body("planet")
        system = _system([body])
        simulation = SimulationSession.from_bodies(system.bodies)
        before = system.to_dict()

        with self.assertRaisesRegex(ModelError, "does not have a parent"):
            generate_body_orbit(
                system,
                simulation,
                body.id,
                OrbitData(semi_major_axis_m=5.0),
                None,
            )

        self.assertEqual(system.to_dict(), before)
        self.assertEqual(simulation.generation, 0)

    def test_group_target_failure_does_not_mutate_model_or_simulation(self):
        first = _body("first", mass=3.0)
        second = _body("second", position=[1.0, 0.0, 0.0])
        group = SystemGroup("Pair", "binary_system", [first.id, second.id], id="pair")
        system = _system([first, second], [group])
        simulation = SimulationSession.from_bodies(system.bodies)
        before = system.to_dict()

        with self.assertRaisesRegex(ModelError, "inside itself"):
            generate_group_barycenter_orbit(
                system,
                simulation,
                group.id,
                "body",
                first.id,
                OrbitData(semi_major_axis_m=5.0),
                None,
            )

        self.assertEqual(system.to_dict(), before)
        self.assertEqual(simulation.generation, 0)

    def test_invalid_binary_group_does_not_mutate_model_or_simulation(self):
        bodies = [_body("first"), _body("second"), _body("third")]
        group = SystemGroup("Triple", "stellar_system", [body.id for body in bodies], id="triple")
        system = _system(bodies, [group])
        simulation = SimulationSession.from_bodies(system.bodies)
        before = system.to_dict()

        with self.assertRaisesRegex(ModelError, "exactly two direct bodies"):
            generate_binary_pair_orbit(
                system,
                simulation,
                group.id,
                OrbitData(semi_major_axis_m=5.0),
                None,
            )

        self.assertEqual(system.to_dict(), before)
        self.assertEqual(simulation.generation, 0)


def _body(
    body_id: str,
    *,
    mass: float = 1.0,
    position: list[float] | None = None,
    velocity: list[float] | None = None,
    parent_id: str | None = None,
) -> Body:
    return Body(
        id=body_id,
        name=body_id.title(),
        kind="star" if parent_id is None else "planet",
        mass_kg=mass,
        radius_m=1.0,
        position_m=position or [0.0, 0.0, 0.0],
        velocity_mps=velocity or [0.0, 0.0, 0.0],
        color="#ffffff",
        parent_id=parent_id,
    )


def _system(bodies: list[Body], groups: list[SystemGroup] | None = None) -> SolarSystem:
    return SolarSystem("Test System", "J2000", bodies, id="test-system", groups=groups or [])


def _session_with_dynamic_state(bodies: list[Body]) -> SimulationSession:
    simulation = SimulationSession.from_bodies(bodies)
    simulation.trails[0].append((1.0, 1.0, 1.0))
    simulation.overview_trails["overview"] = [(2.0, 2.0, 2.0)]
    simulation.context_trails["context"] = [(3.0, 3.0, 3.0)]
    simulation.overview_state = simulation.state.copy()
    simulation.context_state = simulation.state.copy()
    return simulation


def _assert_rebuilt(test: unittest.TestCase, simulation: SimulationSession, bodies: list[Body]) -> None:
    test.assertEqual(simulation.generation, 1)
    test.assertEqual(simulation.trails, [[] for _body in bodies])
    test.assertEqual(simulation.overview_trails, {})
    test.assertEqual(simulation.context_trails, {})
    test.assertIsNone(simulation.overview_state)
    test.assertIsNone(simulation.context_state)
    np.testing.assert_allclose(simulation.state.positions_m, [body.position_m for body in bodies])
    np.testing.assert_allclose(simulation.state.velocities_mps, [body.velocity_mps for body in bodies])


if __name__ == "__main__":
    unittest.main()
