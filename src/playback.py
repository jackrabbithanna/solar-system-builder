# playback.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free simulation playback helpers."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .models import Body, SystemGroup, SystemSettings
from .physics import SimulationState, advance_with_samples
from .scales import (
    OverviewEntity,
    active_body_indices,
    context_overview_entities,
    derived_max_step_s,
    derived_overview_max_step_s,
    focus_overview_entity,
    focus_target_body_indices,
    system_overview_entities,
)

TRAIL_POINT_LIMIT = 2000
SimulationMode = Literal["body_detail", "system_overview", "hybrid_focus"]
AUTO_PHYSICS_BUDGET_MS = 200.0
INITIAL_MS_PER_WORK_UNIT = 0.02
CALIBRATION_WEIGHT = 0.25
MIN_CALIBRATION_WORK_UNITS = 100


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


@dataclass(frozen=True)
class SimulationJobPlan:
    mode: SimulationMode
    generation: int
    active_indices: list[int]
    start_elapsed_s: float
    dt_s: float
    max_step_s: float
    context_max_step_s: float | None = None
    overview_entity_ids: list[str] | None = None
    context_entity_ids: list[str] | None = None
    display_indices: list[int] | None = None
    effective_policy: str = "full_nbody"
    estimated_work_units: int = 0
    predicted_duration_ms: float = 0.0
    auto_approximation: bool = False
    inset_entity_ids: list[str] | None = None
    inset_body_indices: list[list[int]] | None = None


@dataclass(frozen=True)
class SimulationJob:
    plan: SimulationJobPlan
    state: SimulationState
    context_state: SimulationState | None = None


@dataclass(frozen=True)
class SimulationJobResult:
    plan: SimulationJobPlan
    state: SimulationState
    position_samples: list[np.ndarray]
    context_result: tuple[SimulationState, list[np.ndarray]] | None = None
    worker_duration_ms: float = 0.0


class SimulationSession:
    """GTK-free owner for playback state, job planning, and result application."""

    def __init__(self, state: SimulationState):
        self.state = state
        self.generation = 0
        self.trails: list[list[tuple[float, float]]] = [[] for _ in state.masses_kg]
        self.overview_trails: dict[str, list[tuple[float, float]]] = {}
        self.overview_state: SimulationState | None = None
        self.overview_entity_ids: list[str] = []
        self.context_trails: dict[str, list[tuple[float, float]]] = {}
        self.context_state: SimulationState | None = None
        self.context_entity_ids: list[str] = []
        self.last_trail_sample_elapsed_s = state.elapsed_s
        self.auto_approximation_locked = False
        self.ms_per_work_unit = INITIAL_MS_PER_WORK_UNIT
        self.last_effective_policy = "full_nbody"
        self.last_auto_approximation = False

    @classmethod
    def from_bodies(cls, bodies: list[Body]) -> "SimulationSession":
        return cls(SimulationState.from_bodies(bodies))

    def replace_bodies(self, bodies: list[Body], *, increment_generation: bool = True) -> None:
        self.state = SimulationState.from_bodies(bodies)
        if increment_generation:
            self.generation += 1
        self.auto_approximation_locked = False
        self.last_effective_policy = "full_nbody"
        self.last_auto_approximation = False
        self.clear_dynamic(bodies)

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

    def apply_to_bodies(self, bodies: list[Body]) -> None:
        self.state.apply_to_bodies(bodies)

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

    def context_max_step_seconds(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> float:
        entities = self.context_entities(bodies, groups, focus_target)
        if not entities:
            return self.focused_max_step_seconds(bodies, groups, settings, selected_index, focus_group_id, focus_target)
        return derived_overview_max_step_s(entities, settings.accuracy_profile)

    def create_job(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
        dt_s: float,
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
            display_indices=decision.display_indices,
            effective_policy=decision.policy,
            estimated_work_units=decision.estimated_work_units,
            predicted_duration_ms=decision.predicted_duration_ms,
            auto_approximation=decision.auto_approximation,
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
            return SimulationJob(plan, state)

        if decision.mode == "hybrid_focus":
            active = decision.physics_indices
            context_entities = self.context_entities(bodies, groups, focus_target)
            state = simulation_state_for_indices(self.state, active)
            context_state = self._context_simulation_state(bodies, groups, focus_target)
            plan = SimulationJobPlan(
                mode="hybrid_focus",
                start_elapsed_s=state.elapsed_s,
                context_max_step_s=self.context_max_step_seconds(
                    bodies,
                    groups,
                    settings,
                    selected_index,
                    focus_group_id,
                    focus_target,
                ),
                context_entity_ids=[entity.id for entity in context_entities],
                **common_plan,
            )
            return SimulationJob(plan, state, context_state)

        active = decision.physics_indices
        state = simulation_state_for_indices(self.state, active)
        inset_ids, inset_indices = self._inset_memberships(bodies, groups, focus_target)
        plan = SimulationJobPlan(
            mode="body_detail",
            start_elapsed_s=state.elapsed_s,
            inset_entity_ids=inset_ids,
            inset_body_indices=inset_indices,
            **common_plan,
        )
        return SimulationJob(plan, state)

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
        display_indices = focus_target_body_indices(bodies, groups, focus_target) or all_indices
        full_max_step_s = derived_max_step_s(bodies, settings.accuracy_profile)
        full_work = estimate_work_units(dt_s, full_max_step_s, len(bodies))
        full_duration = full_work * self.ms_per_work_unit

        if settings.simulation_scope == "auto":
            if not self.auto_approximation_locked and full_duration <= AUTO_PHYSICS_BUDGET_MS:
                return PhysicsDecision(
                    "full_nbody",
                    "body_detail",
                    all_indices,
                    display_indices,
                    full_max_step_s,
                    full_work,
                    full_duration,
                )
            fallback = self._auto_fallback_policy(bodies, groups, focus_target)
            if fallback == "full_nbody":
                return PhysicsDecision(
                    "full_nbody",
                    "body_detail",
                    all_indices,
                    display_indices,
                    full_max_step_s,
                    full_work,
                    full_duration,
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
    ) -> PhysicsDecision:
        if policy == "system_overview" and len(self.overview_entities(bodies, groups)) > 1:
            entities = self.overview_entities(bodies, groups)
            max_step_s = derived_overview_max_step_s(entities, settings.accuracy_profile)
            work = estimate_work_units(dt_s, max_step_s, len(entities))
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
        max_step_s = derived_max_step_s([bodies[index] for index in active], settings.accuracy_profile)
        work = estimate_work_units(dt_s, max_step_s, len(active))
        mode: SimulationMode = "hybrid_focus" if policy == "hybrid_focused_context" else "body_detail"
        return PhysicsDecision(
            policy,
            mode,
            active,
            display_indices if focus_target else active,
            max_step_s,
            work,
            work * self.ms_per_work_unit,
            auto_approximation,
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
            self._append_overview_trails(
                result.plan.overview_entity_ids or [],
                result.position_samples,
                result.plan.start_elapsed_s,
                result.state.elapsed_s,
                settings.trail_sample_interval_s,
            )
            return True

        merge_active_state(self.state, result.state, result.plan.active_indices)
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
                result.position_samples,
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
        self._append_body_trails(
            bodies,
            result.position_samples,
            result.plan.active_indices,
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
        focused_result, context_result = advance_hybrid_simulations(
            job.state,
            job.context_state,
            job.plan.dt_s,
            job.plan.max_step_s,
            job.plan.context_max_step_s or job.plan.max_step_s,
        )
        state, position_samples = focused_result
        duration_ms = (time.perf_counter() - started) * 1_000.0
        return SimulationJobResult(job.plan, state, position_samples, context_result, duration_ms)

    state, position_samples = advance_with_samples(
        job.state,
        job.plan.dt_s,
        "post_newtonian",
        job.plan.max_step_s,
    )
    duration_ms = (time.perf_counter() - started) * 1_000.0
    return SimulationJobResult(job.plan, state, position_samples, worker_duration_ms=duration_ms)


def advance_hybrid_simulations(
    focused_state: SimulationState,
    context_state: SimulationState | None,
    dt_s: float,
    focused_max_step_s: float,
    context_max_step_s: float,
):
    focused_result = advance_with_samples(
        focused_state,
        dt_s,
        "post_newtonian",
        focused_max_step_s,
    )
    if context_state is None:
        return focused_result, None
    context_result = advance_with_samples(
        context_state,
        dt_s,
        "post_newtonian",
        context_max_step_s,
    )
    return focused_result, context_result


def simulation_state_for_indices(state: SimulationState, active_indices: list[int]) -> SimulationState:
    return SimulationState(
        state.masses_kg[active_indices].copy(),
        state.positions_m[active_indices].copy(),
        state.velocities_mps[active_indices].copy(),
        state.elapsed_s,
    )


def merge_active_state(state: SimulationState, active_state: SimulationState, active_indices: list[int]) -> None:
    state.positions_m[active_indices] = active_state.positions_m
    state.velocities_mps[active_indices] = active_state.velocities_mps
    state.elapsed_s = active_state.elapsed_s


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


def estimate_work_units(dt_s: float, max_step_s: float, body_count: int) -> int:
    if dt_s == 0.0 or body_count == 0:
        return 0
    substeps = max(1, math.ceil(abs(dt_s) / max_step_s))
    return substeps * body_count * body_count


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
    trails: list[list[tuple[float, float]]],
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
            trail.append((float(positions_m[sample_index][0]), float(positions_m[sample_index][1])))
        cap_trail(trail, limit)
    return updated_last_elapsed_s


def append_entity_trails(
    trails: dict[str, list[tuple[float, float]]],
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
            trail.append((float(positions_m[entity_index][0]), float(positions_m[entity_index][1])))
        cap_trail(trail, limit)
    return updated_last_elapsed_s if update_last_elapsed and selected_samples else last_trail_sample_elapsed_s


def cap_trail(trail: list[tuple[float, float]], limit: int = TRAIL_POINT_LIMIT) -> None:
    if len(trail) > limit:
        del trail[: len(trail) - limit]


def should_apply_generation(result_generation: int, current_generation: int) -> bool:
    return result_generation == current_generation
