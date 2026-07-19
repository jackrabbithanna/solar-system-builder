# playback.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free simulation playback helpers."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from .analysis_frames import AnalysisFrameSpec, frame_kinematics, transform_state
from .models import Body, ModelError, SolarSystem, SystemGroup, SystemSettings
from .physics import (
    ConservationDiagnostics,
    ConservationDrift,
    SimulationState,
    SimulationSample,
    advance_with_state_samples,
    advance_with_samples,
    compute_conservation_diagnostics,
    compute_conservation_drift,
)
from .scales import (
    OverviewEntity,
    active_body_indices,
    context_overview_entities,
    derived_max_step_s,
    derived_overview_max_step_s,
    focus_overview_entity,
    focus_target_body_indices,
    focused_trail_reference_indices,
    system_overview_entities,
)

TRAIL_POINT_LIMIT = 2000
SimulationMode = Literal["body_detail", "system_overview", "hybrid_focus"]
AUTO_PHYSICS_BUDGET_MS = 200.0
INITIAL_MS_PER_WORK_UNIT = 0.02
CALIBRATION_WEIGHT = 0.25
MIN_CALIBRATION_WORK_UNITS = 100


def detailed_moon_indices(bodies: list[Body], focus_target: str | None) -> set[int]:
    target_type, _, target_id = (focus_target or "").partition(":")
    if target_type != "body":
        return set()
    target = next((body for body in bodies if body.id == target_id), None)
    if target is None or target.kind not in {"planet", "dwarf planet"}:
        return set()
    descendant_indices = focus_target_body_indices(bodies, [], focus_target)
    return {index for index in descendant_indices if bodies[index].kind == "moon"}


def moon_lod_memberships(
    bodies: list[Body],
    indices: list[int],
    detailed_moons: set[int],
) -> tuple[list[int], list[list[int]], bool]:
    index_set = set(indices)
    indices_by_id = {body.id: index for index, body in enumerate(bodies)}
    moons_by_parent: dict[int, list[int]] = {}
    for index in indices:
        body = bodies[index]
        if body.kind != "moon" or index in detailed_moons or body.parent_id is None:
            continue
        parent_index = indices_by_id.get(body.parent_id)
        if parent_index is not None and parent_index in index_set:
            moons_by_parent.setdefault(parent_index, []).append(index)

    representatives: list[int] = []
    memberships: list[list[int]] = []
    collapsed_moons = {index for moon_indices in moons_by_parent.values() for index in moon_indices}
    for index in indices:
        if index in collapsed_moons:
            continue
        representatives.append(index)
        memberships.append([index, *moons_by_parent.get(index, [])])
    return representatives, memberships, bool(collapsed_moons)


def body_display_indices(
    bodies: list[Body],
    groups: list[SystemGroup],
    focus_target: str | None,
) -> list[int]:
    indices = focus_target_body_indices(bodies, groups, focus_target) or list(range(len(bodies)))
    detailed_moons = detailed_moon_indices(bodies, focus_target)
    return [
        index
        for index in indices
        if bodies[index].kind != "moon" or index in detailed_moons
    ]


def proxy_bodies_for_memberships(bodies: list[Body], memberships: list[list[int]]) -> list[Body]:
    proxies: list[Body] = []
    for membership in memberships:
        representative = bodies[membership[0]]
        masses = [bodies[index].mass_kg for index in membership]
        total_mass = sum(masses)
        position = [
            sum(bodies[index].mass_kg * bodies[index].position_m[axis] for index in membership) / total_mass
            for axis in range(3)
        ]
        velocity = [
            sum(bodies[index].mass_kg * bodies[index].velocity_mps[axis] for index in membership) / total_mass
            for axis in range(3)
        ]
        proxies.append(
            replace(
                representative,
                mass_kg=total_mass,
                position_m=position,
                velocity_mps=velocity,
            )
        )
    return proxies


def focused_timing_bodies(
    bodies: list[Body],
    groups: list[SystemGroup],
    focus_target: str,
    *,
    collapse_moons: bool,
) -> list[Body]:
    indices = focus_target_body_indices(bodies, groups, focus_target)
    if not collapse_moons:
        return [bodies[index] for index in indices]
    _representatives, memberships, _collapsed = moon_lod_memberships(
        bodies,
        indices,
        detailed_moon_indices(bodies, focus_target),
    )
    return proxy_bodies_for_memberships(bodies, memberships)


@dataclass(frozen=True)
class PhysicsDecision:
    policy: str
    mode: SimulationMode
    physics_indices: list[int]
    display_indices: list[int]
    max_step_s: float
    estimated_work_units: int
    predicted_duration_ms: float
    auto_approximation: bool = False
    moons_collapsed: bool = False
    physics_memberships: list[list[int]] | None = None


@dataclass(frozen=True)
class SimulationJobPlan:
    mode: SimulationMode
    generation: int
    active_indices: list[int]
    start_elapsed_s: float
    dt_s: float
    max_step_s: float
    physics_mode: str = "post_newtonian"
    integrator: str = "velocity_verlet"
    overview_entity_ids: list[str] | None = None
    context_entity_ids: list[str] | None = None
    display_indices: list[int] | None = None
    effective_policy: str = "full_nbody"
    estimated_work_units: int = 0
    predicted_duration_ms: float = 0.0
    auto_approximation: bool = False
    inset_entity_ids: list[str] | None = None
    inset_body_indices: list[list[int]] | None = None
    moons_collapsed: bool = False
    physics_memberships: list[list[int]] | None = None
    trail_reference_indices: list[int] | None = None
    analysis_frame: AnalysisFrameSpec | None = None


@dataclass(frozen=True)
class SimulationJob:
    plan: SimulationJobPlan
    state: SimulationState
    context_state: SimulationState | None = None
    analysis_system: SolarSystem | None = None
    analysis_base_state: SimulationState | None = None
    analysis_overview_entities: list | None = None


@dataclass(frozen=True)
class SimulationJobResult:
    plan: SimulationJobPlan
    state: SimulationState
    position_samples: list[np.ndarray]
    context_result: tuple[SimulationState, list[np.ndarray]] | None = None
    worker_duration_ms: float = 0.0
    state_samples: list[SimulationSample] | None = None
    context_state_samples: list[SimulationSample] | None = None
    analysis_position_samples: list[np.ndarray] | None = None


class SimulationSession:
    """GTK-free owner for playback state, job planning, and result application."""

    def __init__(self, state: SimulationState):
        self.state = state
        self.diagnostic_baseline: ConservationDiagnostics | None = None
        self.generation = 0
        self.trails: list[list[tuple[float, float, float]]] = [[] for _ in state.masses_kg]
        self.overview_trails: dict[str, list[tuple[float, float, float]]] = {}
        self.overview_state: SimulationState | None = None
        self.overview_entity_ids: list[str] = []
        self.context_trails: dict[str, list[tuple[float, float, float]]] = {}
        self.context_state: SimulationState | None = None
        self.context_entity_ids: list[str] = []
        self.last_trail_sample_elapsed_s = state.elapsed_s
        self.auto_approximation_locked = False
        self.ms_per_work_unit = INITIAL_MS_PER_WORK_UNIT
        self.last_effective_policy = "full_nbody"
        self.last_auto_approximation = False
        self.rebase_diagnostics()

    @classmethod
    def from_bodies(cls, bodies: list[Body]) -> "SimulationSession":
        return cls(SimulationState.from_bodies(bodies))

    def replace_bodies(
        self,
        bodies: list[Body],
        *,
        elapsed_s: float | None = None,
        increment_generation: bool = True,
    ) -> None:
        preserved_elapsed_s = self.state.elapsed_s if elapsed_s is None else elapsed_s
        self.state = SimulationState.from_bodies(bodies)
        self.state.elapsed_s = preserved_elapsed_s
        self.rebase_diagnostics()
        if increment_generation:
            self.generation += 1
        self.auto_approximation_locked = False
        self.last_effective_policy = "full_nbody"
        self.last_auto_approximation = False
        self.clear_dynamic(bodies)

    def rebase_diagnostics(
        self,
        bodies: list[Body] | None = None,
        groups: list[SystemGroup] | None = None,
    ) -> None:
        state = self._materialized_state(bodies, groups) if bodies is not None else self.state
        try:
            self.diagnostic_baseline = compute_conservation_diagnostics(state)
        except ValueError:
            self.diagnostic_baseline = None

    def conservation_snapshot(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
    ) -> tuple[ConservationDiagnostics, ConservationDrift | None]:
        current = compute_conservation_diagnostics(
            self._materialized_state(bodies, groups)
        )
        drift = (
            compute_conservation_drift(current, self.diagnostic_baseline)
            if self.diagnostic_baseline is not None
            else None
        )
        return current, drift

    def increment_generation(self) -> None:
        self.generation += 1

    def clear_dynamic(self, bodies: list[Body]) -> None:
        self.trails = [[] for _ in bodies]
        self.overview_trails = {}
        self.overview_state = None
        self.overview_entity_ids = []
        self.context_trails = {}
        self.context_state = None
        self.context_entity_ids = []
        self.last_trail_sample_elapsed_s = self.state.elapsed_s

    def apply_to_bodies(
        self,
        bodies: list[Body],
        groups: list[SystemGroup] | None = None,
    ) -> None:
        self._materialized_state(bodies, groups).apply_to_bodies(bodies)

    def materialize_to_bodies(
        self,
        bodies: list[Body],
        groups: list[SystemGroup] | None = None,
    ) -> None:
        """Commit aggregate overview motion into detailed state and bodies."""

        self.state = self._materialized_state(bodies, groups)
        self.state.apply_to_bodies(bodies)

    def _materialized_state(
        self,
        bodies: list[Body],
        groups: list[SystemGroup] | None,
    ) -> SimulationState:
        state = self.state.copy()
        if groups is not None and self.overview_state is not None:
            memberships = [
                focus_target_body_indices(bodies, groups, f"group:{entity_id}")
                for entity_id in self.overview_entity_ids
            ]
            if memberships and all(memberships):
                merge_membership_state(state, self.overview_state, memberships)
        return state

    def materialized_state(
        self,
        bodies: list[Body],
        groups: list[SystemGroup] | None = None,
    ) -> SimulationState:
        return self._materialized_state(bodies, groups)

    def active_body_indices(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> list[int]:
        return self.physics_decision(
            bodies,
            groups,
            settings,
            selected_index,
            focus_group_id,
            focus_target,
            settings.visible_step_s,
        ).physics_indices

    def display_body_indices(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> list[int]:
        return self.physics_decision(
            bodies,
            groups,
            settings,
            selected_index,
            focus_group_id,
            focus_target,
            settings.visible_step_s,
        ).display_indices

    def effective_simulation_scope(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_target: str | None,
    ) -> str:
        return self.physics_decision(
            bodies,
            groups,
            settings,
            selected_index,
            None,
            focus_target,
            settings.visible_step_s,
        ).policy

    def using_system_overview(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_target: str | None,
    ) -> bool:
        if focus_target is not None:
            return False
        decision = self.physics_decision(
            bodies, groups, settings, selected_index, None, focus_target, settings.visible_step_s
        )
        return decision.mode == "system_overview"

    def using_hybrid_focus(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> bool:
        return bool(focus_target and self.context_entities(bodies, groups, focus_target))

    def overview_entities(self, bodies: list[Body], groups: list[SystemGroup]) -> list[OverviewEntity]:
        return system_overview_entities(bodies, groups)

    def context_entities(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        focus_target: str | None,
    ) -> list[OverviewEntity]:
        return context_overview_entities(bodies, groups, focus_target)

    def overview_positions(self, bodies: list[Body], groups: list[SystemGroup]):
        entities = self.overview_entities(bodies, groups)
        if self.overview_state is not None and self.overview_entity_ids == [entity.id for entity in entities]:
            return self.overview_state.positions_m
        return [entity.position_m for entity in entities]

    def context_positions(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        focus_target: str | None,
    ):
        entities = self.context_entities(bodies, groups, focus_target)
        if self.context_state is not None and self.context_entity_ids == [entity.id for entity in entities]:
            return self.context_state.positions_m
        return [entity.position_m for entity in entities]

    def max_step_seconds(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> float:
        return self.physics_decision(
            bodies,
            groups,
            settings,
            selected_index,
            focus_group_id,
            focus_target,
            settings.visible_step_s,
        ).max_step_s

    def focused_max_step_seconds(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> float:
        active = self.active_body_indices(bodies, groups, settings, selected_index, focus_group_id, focus_target)
        return derived_max_step_s([bodies[index] for index in active], settings.accuracy_profile)

    def create_job(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
        dt_s: float,
        analysis_frame: AnalysisFrameSpec | None = None,
        analysis_system: SolarSystem | None = None,
    ) -> SimulationJob:
        decision = self.physics_decision(
            bodies,
            groups,
            settings,
            selected_index,
            focus_group_id,
            focus_target,
            dt_s,
        )
        common_plan = dict(
            generation=self.generation,
            active_indices=decision.physics_indices,
            dt_s=dt_s,
            max_step_s=decision.max_step_s,
            physics_mode=settings.physics_mode,
            integrator=settings.integrator,
            display_indices=decision.display_indices,
            effective_policy=decision.policy,
            estimated_work_units=decision.estimated_work_units,
            predicted_duration_ms=decision.predicted_duration_ms,
            auto_approximation=decision.auto_approximation,
            moons_collapsed=decision.moons_collapsed,
            physics_memberships=decision.physics_memberships,
            trail_reference_indices=(
                focused_trail_reference_indices(bodies, groups, focus_target)
                if (
                    settings.trail_frame == "focused_parent"
                    and focus_target is not None
                    and not (analysis_frame is not None and analysis_frame.active)
                )
                else None
            ),
            analysis_frame=analysis_frame,
        )
        analysis_active = analysis_frame is not None and analysis_frame.active
        analysis_snapshot = (
            SolarSystem.from_dict(analysis_system.to_dict())
            if analysis_active and analysis_system is not None
            else None
        )
        analysis_base_state = (
            self._materialized_state(bodies, groups) if analysis_snapshot is not None else None
        )

        if decision.mode == "system_overview":
            entities = self.overview_entities(bodies, groups)
            state = self._overview_simulation_state(bodies, groups)
            plan = SimulationJobPlan(
                mode="system_overview",
                start_elapsed_s=state.elapsed_s,
                overview_entity_ids=[entity.id for entity in entities],
                **common_plan,
            )
            return SimulationJob(
                plan,
                state,
                analysis_system=analysis_snapshot,
                analysis_base_state=analysis_base_state,
                analysis_overview_entities=entities if analysis_snapshot is not None else None,
            )

        if decision.mode == "hybrid_focus":
            active = decision.physics_indices
            context_entities = self.context_entities(bodies, groups, focus_target)
            state = simulation_state_for_memberships(
                self.state,
                decision.physics_memberships or [[index] for index in active],
            )
            context_state = self._context_simulation_state(bodies, groups, focus_target)
            plan = SimulationJobPlan(
                mode="hybrid_focus",
                start_elapsed_s=state.elapsed_s,
                context_entity_ids=[entity.id for entity in context_entities],
                **common_plan,
            )
            return SimulationJob(
                plan,
                state,
                context_state,
                analysis_snapshot,
                analysis_base_state,
            )

        active = decision.physics_indices
        state = simulation_state_for_memberships(
            self.state,
            decision.physics_memberships or [[index] for index in active],
        )
        inset_ids, inset_indices = self._inset_memberships(bodies, groups, focus_target)
        plan = SimulationJobPlan(
            mode="body_detail",
            start_elapsed_s=state.elapsed_s,
            inset_entity_ids=inset_ids,
            inset_body_indices=inset_indices,
            **common_plan,
        )
        return SimulationJob(
            plan,
            state,
            analysis_system=analysis_snapshot,
            analysis_base_state=analysis_base_state,
        )

    def physics_decision(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
        dt_s: float,
    ) -> PhysicsDecision:
        all_indices = list(range(len(bodies)))
        display_indices = body_display_indices(bodies, groups, focus_target)
        detailed_moons = detailed_moon_indices(bodies, focus_target)
        auto_indices, auto_memberships, auto_collapsed = moon_lod_memberships(
            bodies,
            all_indices,
            detailed_moons,
        )
        auto_bodies = proxy_bodies_for_memberships(bodies, auto_memberships)
        auto_max_step_s = derived_max_step_s(auto_bodies, settings.accuracy_profile)
        full_work = estimate_work_units(
            dt_s,
            auto_max_step_s,
            len(auto_indices),
            settings.integrator,
        )
        full_duration = full_work * self.ms_per_work_unit

        if settings.simulation_scope == "auto":
            if not self.auto_approximation_locked and full_duration <= AUTO_PHYSICS_BUDGET_MS:
                return PhysicsDecision(
                    policy="full_nbody",
                    mode="body_detail",
                    physics_indices=auto_indices,
                    display_indices=display_indices,
                    max_step_s=auto_max_step_s,
                    estimated_work_units=full_work,
                    predicted_duration_ms=full_duration,
                    moons_collapsed=auto_collapsed,
                    physics_memberships=auto_memberships,
                )
            fallback = self._auto_fallback_policy(bodies, groups, focus_target)
            if fallback == "full_nbody":
                return PhysicsDecision(
                    policy="full_nbody",
                    mode="body_detail",
                    physics_indices=auto_indices,
                    display_indices=display_indices,
                    max_step_s=auto_max_step_s,
                    estimated_work_units=full_work,
                    predicted_duration_ms=full_duration,
                    moons_collapsed=auto_collapsed,
                    physics_memberships=auto_memberships,
                )
            return self._decision_for_policy(
                fallback,
                bodies,
                groups,
                settings,
                selected_index,
                focus_group_id,
                focus_target,
                dt_s,
                display_indices,
                auto_approximation=True,
                allow_moon_lod=True,
            )

        return self._decision_for_policy(
            settings.simulation_scope,
            bodies,
            groups,
            settings,
            selected_index,
            focus_group_id,
            focus_target,
            dt_s,
            display_indices,
            allow_moon_lod=settings.simulation_scope != "full_nbody",
        )

    def _decision_for_policy(
        self,
        policy: str,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
        dt_s: float,
        display_indices: list[int],
        *,
        auto_approximation: bool = False,
        allow_moon_lod: bool,
    ) -> PhysicsDecision:
        if policy == "system_overview" and len(self.overview_entities(bodies, groups)) > 1:
            entities = self.overview_entities(bodies, groups)
            max_step_s = derived_overview_max_step_s(entities, settings.accuracy_profile)
            work = estimate_work_units(
                dt_s,
                max_step_s,
                len(entities),
                settings.integrator,
            )
            return PhysicsDecision(
                policy,
                "system_overview",
                [],
                display_indices,
                max_step_s,
                work,
                work * self.ms_per_work_unit,
                auto_approximation,
            )

        active = active_body_indices(
            bodies,
            policy,
            settings.view_mode,
            selected_index,
            groups,
            focus_group_id,
            focus_target,
        )
        if policy == "full_nbody":
            active = list(range(len(bodies)))
        if allow_moon_lod:
            active, memberships, moons_collapsed = moon_lod_memberships(
                bodies,
                active,
                detailed_moon_indices(bodies, focus_target),
            )
        else:
            memberships = [[index] for index in active]
            moons_collapsed = False
        physics_bodies = proxy_bodies_for_memberships(bodies, memberships)
        max_step_s = derived_max_step_s(physics_bodies, settings.accuracy_profile)
        mode: SimulationMode = "hybrid_focus" if policy == "hybrid_focused_context" else "body_detail"
        simulated_count = len(active)
        if mode == "hybrid_focus":
            simulated_count += len(self.context_entities(bodies, groups, focus_target))
        work = estimate_work_units(
            dt_s,
            max_step_s,
            simulated_count,
            settings.integrator,
        )
        effective_display_indices = (
            display_indices if focus_target is not None or policy == "full_nbody" else active
        )
        return PhysicsDecision(
            policy=policy,
            mode=mode,
            physics_indices=active,
            display_indices=effective_display_indices,
            max_step_s=max_step_s,
            estimated_work_units=work,
            predicted_duration_ms=work * self.ms_per_work_unit,
            auto_approximation=auto_approximation,
            moons_collapsed=moons_collapsed,
            physics_memberships=memberships,
        )

    def _auto_fallback_policy(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        focus_target: str | None,
    ) -> str:
        if focus_target is not None:
            return "hybrid_focused_context" if self.context_entities(bodies, groups, focus_target) else "focused_subsystem"
        if len(self.overview_entities(bodies, groups)) > 1:
            return "system_overview"
        if any(body.kind == "star" and body.parent_id is None for body in bodies):
            return "stellar_overview"
        return "full_nbody"

    def _inset_memberships(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        focus_target: str | None,
    ) -> tuple[list[str], list[list[int]]]:
        focused = focus_overview_entity(bodies, groups, focus_target)
        if focused is None:
            return [], []
        entities = [focused, *self.context_entities(bodies, groups, focus_target)]
        indices = [focus_target_body_indices(bodies, groups, focus_target)]
        group_ids = {group.id for group in groups}
        for entity in entities[1:]:
            if entity.id in group_ids:
                indices.append(focus_target_body_indices(bodies, groups, f"group:{entity.id}"))
            elif entity.id.startswith("context-"):
                indices.append(focus_target_body_indices(bodies, groups, f"body:{entity.id.removeprefix('context-')}"))
            else:
                indices.append([])
        return [entity.id for entity in entities], indices

    def apply_result(
        self,
        result: SimulationJobResult,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
    ) -> bool:
        if not should_apply_generation(result.plan.generation, self.generation):
            return False

        self.last_effective_policy = result.plan.effective_policy
        self.last_auto_approximation = result.plan.auto_approximation
        if result.plan.effective_policy != "full_nbody":
            self.auto_approximation_locked = True
        if (
            result.plan.effective_policy == "full_nbody"
            and result.plan.estimated_work_units >= MIN_CALIBRATION_WORK_UNITS
            and result.worker_duration_ms > 0.0
        ):
            observed = result.worker_duration_ms / result.plan.estimated_work_units
            self.ms_per_work_unit = (
                (1.0 - CALIBRATION_WEIGHT) * self.ms_per_work_unit
                + CALIBRATION_WEIGHT * observed
            )

        if result.plan.mode == "system_overview":
            self.overview_state = result.state
            self.overview_entity_ids = result.plan.overview_entity_ids or []
            self.state.elapsed_s = result.state.elapsed_s
            overview_samples = (
                result.analysis_position_samples
                if result.analysis_position_samples is not None
                else result.position_samples
            )
            self._append_overview_trails(
                result.plan.overview_entity_ids or [],
                overview_samples,
                result.plan.start_elapsed_s,
                result.state.elapsed_s,
                settings.trail_sample_interval_s,
            )
            return True

        memberships = result.plan.physics_memberships or [
            [index] for index in result.plan.active_indices
        ]
        expanded_samples = expand_membership_position_samples(
            result.position_samples,
            self.state,
            memberships,
        )
        merge_membership_state(self.state, result.state, memberships)
        self.overview_trails = {}
        self.overview_state = None
        self.overview_entity_ids = []
        self.state.apply_to_bodies(bodies)
        if result.plan.mode == "hybrid_focus" and result.context_result is not None:
            context_state, context_samples = result.context_result
            self.context_state = context_state
            self.context_entity_ids = result.plan.context_entity_ids or []
            self._append_context_trails(
                result.plan.context_entity_ids or [],
                context_samples,
                result.plan.start_elapsed_s,
                context_state.elapsed_s,
                settings.trail_sample_interval_s,
            )
        elif result.plan.effective_policy == "full_nbody" and result.plan.inset_entity_ids:
            inset_samples = aggregate_position_samples(
                expanded_samples,
                self.state.masses_kg,
                result.plan.inset_body_indices or [],
            )
            self.context_state = None
            self.context_entity_ids = result.plan.inset_entity_ids
            self._append_context_trails(
                result.plan.inset_entity_ids,
                inset_samples,
                result.plan.start_elapsed_s,
                result.state.elapsed_s,
                settings.trail_sample_interval_s,
            )
        detailed_moons = {
            index
            for index in (result.plan.display_indices or [])
            if bodies[index].kind == "moon"
        }
        trail_indices = [
            index
            for index in result.plan.active_indices
            if bodies[index].kind != "moon" or index in detailed_moons
        ]
        reference_indices = result.plan.trail_reference_indices or []
        if len(reference_indices) == 1:
            trail_indices = [
                index for index in trail_indices if index != reference_indices[0]
            ]
        stored_samples = (
            relative_position_samples(
                expanded_samples,
                self.state.masses_kg,
                reference_indices,
            )
            if reference_indices
            else expanded_samples
        )
        if result.analysis_position_samples is not None:
            stored_samples = result.analysis_position_samples
        trail_samples = [sample[trail_indices].copy() for sample in stored_samples]
        self._append_body_trails(
            bodies,
            trail_samples,
            trail_indices,
            result.plan.start_elapsed_s,
            result.state.elapsed_s,
            settings.trail_sample_interval_s,
        )
        return True

    def _overview_simulation_state(self, bodies: list[Body], groups: list[SystemGroup]) -> SimulationState:
        entities = self.overview_entities(bodies, groups)
        entity_ids = [entity.id for entity in entities]
        if self.overview_state is None or self.overview_entity_ids != entity_ids:
            self.overview_entity_ids = entity_ids
            self.overview_state = overview_simulation_state(entities, self.state.elapsed_s)
        return self.overview_state.copy()

    def _context_simulation_state(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        focus_target: str | None,
    ) -> SimulationState | None:
        entities = self.context_entities(bodies, groups, focus_target)
        if not entities:
            return None
        entity_ids = [entity.id for entity in entities]
        if self.context_state is None or self.context_entity_ids != entity_ids:
            self.context_entity_ids = entity_ids
            self.context_state = overview_simulation_state(entities, self.state.elapsed_s)
        return self.context_state.copy()

    def _append_body_trails(
        self,
        bodies: list[Body],
        position_samples,
        active_indices: list[int],
        start_elapsed_s: float,
        end_elapsed_s: float,
        interval_s: float,
    ) -> None:
        self.last_trail_sample_elapsed_s = append_body_trails(
            self.trails,
            bodies,
            position_samples,
            active_indices,
            start_elapsed_s,
            end_elapsed_s,
            self.last_trail_sample_elapsed_s,
            interval_s,
        )

    def _append_overview_trails(
        self,
        entity_ids: list[str],
        position_samples,
        start_elapsed_s: float,
        end_elapsed_s: float,
        interval_s: float,
    ) -> None:
        self.last_trail_sample_elapsed_s = append_entity_trails(
            self.overview_trails,
            entity_ids,
            position_samples,
            start_elapsed_s,
            end_elapsed_s,
            self.last_trail_sample_elapsed_s,
            interval_s,
            update_last_elapsed=True,
        )

    def _append_context_trails(
        self,
        entity_ids: list[str],
        position_samples,
        start_elapsed_s: float,
        end_elapsed_s: float,
        interval_s: float,
    ) -> None:
        append_entity_trails(
            self.context_trails,
            entity_ids,
            position_samples,
            start_elapsed_s,
            end_elapsed_s,
            self.last_trail_sample_elapsed_s,
            interval_s,
            update_last_elapsed=False,
        )


def run_simulation_job(job: SimulationJob) -> SimulationJobResult:
    started = time.perf_counter()
    if job.plan.mode == "hybrid_focus":
        focused_result, context_result, focused_state_samples, context_state_samples = (
            advance_hybrid_simulations_with_state_samples(
                job.state,
                job.context_state,
                job.plan.dt_s,
                job.plan.max_step_s,
                job.plan.physics_mode,
                job.plan.integrator,
            )
        )
        state, position_samples = focused_result
        analysis_samples = _job_analysis_position_samples(
            job,
            focused_state_samples,
        )
        duration_ms = (time.perf_counter() - started) * 1_000.0
        return SimulationJobResult(
            job.plan,
            state,
            position_samples,
            context_result,
            duration_ms,
            focused_state_samples,
            context_state_samples,
            analysis_samples,
        )

    state, state_samples = advance_with_state_samples(
        job.state,
        job.plan.dt_s,
        job.plan.physics_mode,
        job.plan.max_step_s,
        job.plan.integrator,
    )
    position_samples = [sample.positions_m for sample in state_samples]
    analysis_samples = _job_analysis_position_samples(job, state_samples)
    duration_ms = (time.perf_counter() - started) * 1_000.0
    return SimulationJobResult(
        job.plan,
        state,
        position_samples,
        worker_duration_ms=duration_ms,
        state_samples=state_samples,
        analysis_position_samples=analysis_samples,
    )


def _job_analysis_position_samples(
    job: SimulationJob,
    samples: list[SimulationSample] | None,
) -> list[np.ndarray] | None:
    spec = job.plan.analysis_frame
    system = job.analysis_system
    if spec is None or not spec.active or system is None or not samples:
        return None
    if job.plan.mode == "system_overview":
        settings = replace(
            system.settings,
            physics_mode=job.plan.physics_mode,
            integrator=job.plan.integrator,
        )
        return _analysis_overview_position_samples(
            system,
            spec,
            job.analysis_overview_entities or [],
            samples,
            settings,
        )
    if job.analysis_base_state is None:
        return None
    memberships = job.plan.physics_memberships or [
        [index] for index in job.plan.active_indices
    ]
    full_samples = expand_membership_state_samples(
        samples,
        job.analysis_base_state,
        memberships,
    )
    transformed = []
    try:
        for sample in full_samples:
            sample_state = SimulationState(
                job.analysis_base_state.masses_kg,
                sample.positions_m,
                sample.velocities_mps,
                sample.elapsed_s,
            )
            kinematics = frame_kinematics(
                system,
                sample_state,
                spec,
                physics_mode=job.plan.physics_mode,
                include_acceleration=False,
            )
            transformed.append(transform_state(sample_state, kinematics).positions_m)
    except ModelError:
        return []
    return transformed


def advance_hybrid_simulations_with_state_samples(
    focused_state: SimulationState,
    context_state: SimulationState | None,
    dt_s: float,
    focused_max_step_s: float,
    physics_mode: str = "post_newtonian",
    integrator: str = "velocity_verlet",
):
    if context_state is None:
        state, samples = advance_with_state_samples(
            focused_state,
            dt_s,
            physics_mode,
            focused_max_step_s,
            integrator,
        )
        return (state, [sample.positions_m for sample in samples]), None, samples, None

    focused_count = len(focused_state.masses_kg)
    combined_state = SimulationState(
        masses_kg=np.concatenate((focused_state.masses_kg, context_state.masses_kg)),
        positions_m=np.concatenate((focused_state.positions_m, context_state.positions_m)),
        velocities_mps=np.concatenate((focused_state.velocities_mps, context_state.velocities_mps)),
        elapsed_s=focused_state.elapsed_s,
    )
    combined_result, combined_samples = advance_with_state_samples(
        combined_state,
        dt_s,
        physics_mode,
        focused_max_step_s,
        integrator,
    )
    focused_result = SimulationState(
        combined_result.masses_kg[:focused_count].copy(),
        combined_result.positions_m[:focused_count].copy(),
        combined_result.velocities_mps[:focused_count].copy(),
        combined_result.elapsed_s,
    )
    context_result = SimulationState(
        combined_result.masses_kg[focused_count:].copy(),
        combined_result.positions_m[focused_count:].copy(),
        combined_result.velocities_mps[focused_count:].copy(),
        combined_result.elapsed_s,
    )
    focused_samples = [
        SimulationSample(
            sample.elapsed_s,
            sample.positions_m[:focused_count].copy(),
            sample.velocities_mps[:focused_count].copy(),
        )
        for sample in combined_samples
    ]
    context_samples = [
        SimulationSample(
            sample.elapsed_s,
            sample.positions_m[focused_count:].copy(),
            sample.velocities_mps[focused_count:].copy(),
        )
        for sample in combined_samples
    ]
    return (
        (focused_result, [sample.positions_m for sample in focused_samples]),
        (context_result, [sample.positions_m for sample in context_samples]),
        focused_samples,
        context_samples,
    )


def advance_hybrid_simulations(
    focused_state: SimulationState,
    context_state: SimulationState | None,
    dt_s: float,
    focused_max_step_s: float,
    physics_mode: str = "post_newtonian",
    integrator: str = "velocity_verlet",
):
    if context_state is None:
        focused_result = advance_with_samples(
            focused_state,
            dt_s,
            physics_mode,
            focused_max_step_s,
            integrator,
        )
        return focused_result, None

    focused_count = len(focused_state.masses_kg)
    combined_state = SimulationState(
        masses_kg=np.concatenate((focused_state.masses_kg, context_state.masses_kg)),
        positions_m=np.concatenate((focused_state.positions_m, context_state.positions_m)),
        velocities_mps=np.concatenate((focused_state.velocities_mps, context_state.velocities_mps)),
        elapsed_s=focused_state.elapsed_s,
    )
    combined_result, combined_samples = advance_with_samples(
        combined_state,
        dt_s,
        physics_mode,
        focused_max_step_s,
        integrator,
    )
    focused_result = SimulationState(
        masses_kg=combined_result.masses_kg[:focused_count].copy(),
        positions_m=combined_result.positions_m[:focused_count].copy(),
        velocities_mps=combined_result.velocities_mps[:focused_count].copy(),
        elapsed_s=combined_result.elapsed_s,
    )
    context_result = SimulationState(
        masses_kg=combined_result.masses_kg[focused_count:].copy(),
        positions_m=combined_result.positions_m[focused_count:].copy(),
        velocities_mps=combined_result.velocities_mps[focused_count:].copy(),
        elapsed_s=combined_result.elapsed_s,
    )
    focused_samples = [sample[:focused_count].copy() for sample in combined_samples]
    context_samples = [sample[focused_count:].copy() for sample in combined_samples]
    return (focused_result, focused_samples), (context_result, context_samples)


def simulation_state_for_indices(state: SimulationState, active_indices: list[int]) -> SimulationState:
    return SimulationState(
        state.masses_kg[active_indices].copy(),
        state.positions_m[active_indices].copy(),
        state.velocities_mps[active_indices].copy(),
        state.elapsed_s,
    )


def simulation_state_for_memberships(
    state: SimulationState,
    memberships: list[list[int]],
) -> SimulationState:
    masses: list[float] = []
    positions: list[np.ndarray] = []
    velocities: list[np.ndarray] = []
    for membership in memberships:
        member_masses = state.masses_kg[membership]
        total_mass = float(member_masses.sum())
        masses.append(total_mass)
        positions.append(
            np.sum(state.positions_m[membership] * member_masses[:, np.newaxis], axis=0)
            / total_mass
        )
        velocities.append(
            np.sum(state.velocities_mps[membership] * member_masses[:, np.newaxis], axis=0)
            / total_mass
        )
    return SimulationState(
        masses_kg=np.array(masses, dtype=float),
        positions_m=np.array(positions, dtype=float),
        velocities_mps=np.array(velocities, dtype=float),
        elapsed_s=state.elapsed_s,
    )


def merge_active_state(state: SimulationState, active_state: SimulationState, active_indices: list[int]) -> None:
    state.positions_m[active_indices] = active_state.positions_m
    state.velocities_mps[active_indices] = active_state.velocities_mps
    state.elapsed_s = active_state.elapsed_s


def merge_membership_state(
    state: SimulationState,
    membership_state: SimulationState,
    memberships: list[list[int]],
) -> None:
    if len(memberships) != len(membership_state.masses_kg):
        raise ValueError("membership count must match simulation state")
    for membership_index, membership in enumerate(memberships):
        member_masses = state.masses_kg[membership]
        total_mass = float(member_masses.sum())
        old_position = (
            np.sum(state.positions_m[membership] * member_masses[:, np.newaxis], axis=0)
            / total_mass
        )
        old_velocity = (
            np.sum(state.velocities_mps[membership] * member_masses[:, np.newaxis], axis=0)
            / total_mass
        )
        state.positions_m[membership] += membership_state.positions_m[membership_index] - old_position
        state.velocities_mps[membership] += membership_state.velocities_mps[membership_index] - old_velocity
    state.elapsed_s = membership_state.elapsed_s


def expand_membership_position_samples(
    position_samples: list[np.ndarray],
    state: SimulationState,
    memberships: list[list[int]],
) -> list[np.ndarray]:
    if any(len(sample) != len(memberships) for sample in position_samples):
        raise ValueError("membership count must match position samples")
    offsets: list[np.ndarray] = []
    for membership in memberships:
        member_masses = state.masses_kg[membership]
        center = (
            np.sum(state.positions_m[membership] * member_masses[:, np.newaxis], axis=0)
            / float(member_masses.sum())
        )
        offsets.append(state.positions_m[membership] - center)

    expanded_samples: list[np.ndarray] = []
    for sample in position_samples:
        expanded = state.positions_m.copy()
        for membership_index, membership in enumerate(memberships):
            expanded[membership] = sample[membership_index] + offsets[membership_index]
        expanded_samples.append(expanded)
    return expanded_samples


def expand_membership_state_samples(
    samples: list[SimulationSample],
    state: SimulationState,
    memberships: list[list[int]],
) -> list[SimulationSample]:
    if any(len(sample.positions_m) != len(memberships) for sample in samples):
        raise ValueError("membership count must match state samples")
    position_offsets: list[np.ndarray] = []
    velocity_offsets: list[np.ndarray] = []
    for membership in memberships:
        masses = state.masses_kg[membership]
        weights = masses[:, np.newaxis]
        total = float(masses.sum())
        position_center = np.sum(state.positions_m[membership] * weights, axis=0) / total
        velocity_center = np.sum(state.velocities_mps[membership] * weights, axis=0) / total
        position_offsets.append(state.positions_m[membership] - position_center)
        velocity_offsets.append(state.velocities_mps[membership] - velocity_center)
    expanded = []
    for sample in samples:
        positions = state.positions_m.copy()
        velocities = state.velocities_mps.copy()
        for index, membership in enumerate(memberships):
            positions[membership] = sample.positions_m[index] + position_offsets[index]
            velocities[membership] = sample.velocities_mps[index] + velocity_offsets[index]
        expanded.append(SimulationSample(sample.elapsed_s, positions, velocities))
    return expanded


def _analysis_overview_position_samples(
    system: SolarSystem,
    spec: AnalysisFrameSpec,
    entities,
    samples: list[SimulationSample],
    settings: SystemSettings,
) -> list[np.ndarray]:
    if not entities:
        return []
    proxy_bodies = [
        Body(
            entity.name,
            "star",
            entity.mass_kg,
            1.0,
            list(entity.position_m),
            list(entity.velocity_mps),
            entity.color,
            id=entity.id,
        )
        for entity in entities
    ]
    proxy_system = SolarSystem(
        name=system.name,
        epoch=system.epoch,
        bodies=proxy_bodies,
        groups=[],
        settings=settings,
        reference_frame=system.reference_frame,
    )
    mapped = spec
    mapped_origin = _overview_analysis_target(
        system,
        entities,
        spec.origin_kind,
        spec.origin_id,
    )
    if mapped_origin is not None:
        mapped = replace(
            mapped,
            origin_kind=mapped_origin[0],
            origin_id=mapped_origin[1],
        )
    if mapped.rotation_mode == "target_pair" and mapped.secondary_kind is not None:
        mapped_secondary = _overview_analysis_target(
            system,
            entities,
            mapped.secondary_kind,
            mapped.secondary_id,
        )
        if mapped_secondary is not None:
            mapped = replace(
                mapped,
                secondary_kind=mapped_secondary[0],
                secondary_id=mapped_secondary[1],
            )
    transformed = []
    masses = np.array([entity.mass_kg for entity in entities], dtype=float)
    try:
        for sample in samples:
            sample_state = SimulationState(
                masses,
                sample.positions_m,
                sample.velocities_mps,
                sample.elapsed_s,
            )
            kinematics = frame_kinematics(
                proxy_system,
                sample_state,
                mapped,
                physics_mode=settings.physics_mode,
                include_acceleration=False,
            )
            transformed.append(transform_state(sample_state, kinematics).positions_m)
    except ModelError:
        return []
    return transformed


def _overview_analysis_target(system, entities, kind: str, target_id: str | None):
    if kind in {"fixed", "system_barycenter"}:
        return kind, target_id
    target = (
        f"body:{target_id}"
        if kind == "body"
        else f"group:{target_id}"
    )
    target_indices = set(
        focus_target_body_indices(system.bodies, system.groups, target)
    )
    if not target_indices:
        return None
    group_ids = {group.id for group in system.groups}
    for entity in entities:
        if entity.id in group_ids:
            entity_indices = set(
                focus_target_body_indices(
                    system.bodies,
                    system.groups,
                    f"group:{entity.id}",
                )
            )
        elif entity.id.startswith("context-"):
            entity_indices = set(
                focus_target_body_indices(
                    system.bodies,
                    system.groups,
                    f"body:{entity.id.removeprefix('context-')}",
                )
            )
        else:
            entity_indices = set()
        if target_indices <= entity_indices:
            return "body", entity.id
    return None


def overview_simulation_state(
    entities,
    elapsed_s: float,
) -> SimulationState:
    return SimulationState(
        masses_kg=np.array([entity.mass_kg for entity in entities], dtype=float),
        positions_m=np.array([entity.position_m for entity in entities], dtype=float),
        velocities_mps=np.array([entity.velocity_mps for entity in entities], dtype=float),
        elapsed_s=elapsed_s,
    )


def estimate_work_units(
    dt_s: float,
    max_step_s: float,
    body_count: int,
    integrator: str = "velocity_verlet",
) -> int:
    if dt_s == 0.0 or body_count == 0:
        return 0
    substeps = max(1, math.ceil(abs(dt_s) / max_step_s))
    integrator_multiplier = 2 if integrator == "rk4" else 1
    return substeps * body_count * body_count * integrator_multiplier


def aggregate_position_samples(
    position_samples: list[np.ndarray],
    masses_kg: np.ndarray,
    entity_body_indices: list[list[int]],
) -> list[np.ndarray]:
    aggregated: list[np.ndarray] = []
    for positions_m in position_samples:
        entity_positions: list[np.ndarray] = []
        for indices in entity_body_indices:
            if not indices:
                entity_positions.append(np.zeros(3, dtype=float))
                continue
            entity_masses = masses_kg[indices]
            total_mass = float(entity_masses.sum())
            entity_positions.append(
                np.sum(positions_m[indices] * entity_masses[:, np.newaxis], axis=0) / total_mass
            )
        aggregated.append(np.array(entity_positions, dtype=float))
    return aggregated


def relative_position_samples(
    position_samples: list[np.ndarray],
    masses_kg: np.ndarray,
    reference_indices: list[int],
) -> list[np.ndarray]:
    """Translate samples into a moving body or group-barycenter frame."""

    if not reference_indices:
        return [sample.copy() for sample in position_samples]
    indices = np.array(reference_indices, dtype=int)
    if np.any(indices < 0) or np.any(indices >= len(masses_kg)):
        raise ValueError("trail reference index is out of range")
    reference_masses = masses_kg[indices]
    total_mass = float(reference_masses.sum())
    if total_mass <= 0.0:
        raise ValueError("trail reference mass must be positive")
    relative_samples = []
    for positions_m in position_samples:
        center_m = np.sum(
            positions_m[indices] * reference_masses[:, np.newaxis],
            axis=0,
        ) / total_mass
        relative_samples.append(positions_m - center_m)
    return relative_samples


def select_trail_samples(
    position_samples,
    start_elapsed_s: float,
    end_elapsed_s: float,
    last_trail_sample_elapsed_s: float,
    interval_s: float,
) -> tuple[list, float]:
    if not position_samples:
        return [], last_trail_sample_elapsed_s
    sample_count = len(position_samples)
    elapsed_delta = end_elapsed_s - start_elapsed_s
    direction = 1.0 if elapsed_delta >= 0.0 else -1.0
    next_sample_elapsed_s = last_trail_sample_elapsed_s + direction * interval_s

    selected_samples = []
    updated_last_elapsed_s = last_trail_sample_elapsed_s
    for sample_index, positions_m in enumerate(position_samples, start=1):
        sample_elapsed_s = start_elapsed_s + elapsed_delta * sample_index / sample_count
        if direction > 0.0 and sample_elapsed_s + 1.0e-6 < next_sample_elapsed_s:
            continue
        if direction < 0.0 and sample_elapsed_s - 1.0e-6 > next_sample_elapsed_s:
            continue
        selected_samples.append(positions_m)
        updated_last_elapsed_s = sample_elapsed_s
        next_sample_elapsed_s = updated_last_elapsed_s + direction * interval_s
    return selected_samples, updated_last_elapsed_s


def append_body_trails(
    trails: list[list[tuple[float, float, float]]],
    bodies: list[Body],
    position_samples,
    active_indices: list[int],
    start_elapsed_s: float,
    end_elapsed_s: float,
    last_trail_sample_elapsed_s: float,
    interval_s: float,
    *,
    limit: int = TRAIL_POINT_LIMIT,
) -> float:
    selected_samples, updated_last_elapsed_s = select_trail_samples(
        position_samples,
        start_elapsed_s,
        end_elapsed_s,
        last_trail_sample_elapsed_s,
        interval_s,
    )
    if not selected_samples:
        return last_trail_sample_elapsed_s
    for sample_index, body_index in enumerate(active_indices):
        body = bodies[body_index]
        if not body.trail_enabled:
            continue
        trail = trails[body_index]
        for positions_m in selected_samples:
            trail.append(tuple(float(positions_m[sample_index][axis]) for axis in range(3)))
        cap_trail(trail, limit)
    return updated_last_elapsed_s


def append_entity_trails(
    trails: dict[str, list[tuple[float, float, float]]],
    entity_ids: list[str],
    position_samples,
    start_elapsed_s: float,
    end_elapsed_s: float,
    last_trail_sample_elapsed_s: float,
    interval_s: float,
    *,
    update_last_elapsed: bool,
    limit: int = TRAIL_POINT_LIMIT,
) -> float:
    if not position_samples:
        return last_trail_sample_elapsed_s
    selected_samples, updated_last_elapsed_s = select_trail_samples(
        position_samples,
        start_elapsed_s,
        end_elapsed_s,
        last_trail_sample_elapsed_s,
        interval_s,
    )
    for entity_index, entity_id in enumerate(entity_ids):
        trail = trails.setdefault(entity_id, [])
        for positions_m in selected_samples:
            trail.append(tuple(float(positions_m[entity_index][axis]) for axis in range(3)))
        cap_trail(trail, limit)
    return updated_last_elapsed_s if update_last_elapsed and selected_samples else last_trail_sample_elapsed_s


def cap_trail(trail: list[tuple[float, float, float]], limit: int = TRAIL_POINT_LIMIT) -> None:
    if len(trail) > limit:
        del trail[: len(trail) - limit]


def should_apply_generation(result_generation: int, current_generation: int) -> bool:
    return result_generation == current_generation
