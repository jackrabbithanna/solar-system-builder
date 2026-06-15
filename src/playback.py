# playback.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free simulation playback helpers."""

from __future__ import annotations

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
    effective_simulation_scope,
    system_overview_entities,
)

TRAIL_POINT_LIMIT = 2000
SimulationMode = Literal["body_detail", "system_overview", "hybrid_focus"]


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

    @classmethod
    def from_bodies(cls, bodies: list[Body]) -> "SimulationSession":
        return cls(SimulationState.from_bodies(bodies))

    def replace_bodies(self, bodies: list[Body], *, increment_generation: bool = True) -> None:
        self.state = SimulationState.from_bodies(bodies)
        if increment_generation:
            self.generation += 1
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
        return active_body_indices(
            bodies,
            settings.simulation_scope,
            settings.view_mode,
            selected_index,
            groups,
            focus_group_id,
            focus_target,
        )

    def effective_simulation_scope(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_target: str | None,
    ) -> str:
        return effective_simulation_scope(
            bodies,
            settings.simulation_scope,
            settings.view_mode,
            selected_index,
            groups,
            focus_target,
        )

    def using_system_overview(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_target: str | None,
    ) -> bool:
        return (
            self.effective_simulation_scope(bodies, groups, settings, selected_index, focus_target)
            == "system_overview"
            and len(self.overview_entities(bodies, groups)) > 1
        )

    def using_hybrid_focus(
        self,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
        selected_index: int,
        focus_group_id: str | None,
        focus_target: str | None,
    ) -> bool:
        return (
            self.effective_simulation_scope(bodies, groups, settings, selected_index, focus_target)
            == "hybrid_focused_context"
            and bool(self.active_body_indices(bodies, groups, settings, selected_index, focus_group_id, focus_target))
        )

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
        if self.using_system_overview(bodies, groups, settings, selected_index, focus_target):
            return derived_overview_max_step_s(self.overview_entities(bodies, groups), settings.accuracy_profile)
        if self.using_hybrid_focus(bodies, groups, settings, selected_index, focus_group_id, focus_target):
            return self.focused_max_step_seconds(bodies, groups, settings, selected_index, focus_group_id, focus_target)
        active = self.active_body_indices(bodies, groups, settings, selected_index, focus_group_id, focus_target)
        return derived_max_step_s([bodies[index] for index in active], settings.accuracy_profile)

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
        if self.using_system_overview(bodies, groups, settings, selected_index, focus_target):
            entities = self.overview_entities(bodies, groups)
            state = self._overview_simulation_state(bodies, groups)
            plan = SimulationJobPlan(
                mode="system_overview",
                generation=self.generation,
                active_indices=[],
                start_elapsed_s=state.elapsed_s,
                dt_s=dt_s,
                max_step_s=self.max_step_seconds(bodies, groups, settings, selected_index, focus_group_id, focus_target),
                overview_entity_ids=[entity.id for entity in entities],
            )
            return SimulationJob(plan, state)

        if self.using_hybrid_focus(bodies, groups, settings, selected_index, focus_group_id, focus_target):
            active = self.active_body_indices(bodies, groups, settings, selected_index, focus_group_id, focus_target)
            context_entities = self.context_entities(bodies, groups, focus_target)
            state = simulation_state_for_indices(self.state, active)
            context_state = self._context_simulation_state(bodies, groups, focus_target)
            plan = SimulationJobPlan(
                mode="hybrid_focus",
                generation=self.generation,
                active_indices=active,
                start_elapsed_s=state.elapsed_s,
                dt_s=dt_s,
                max_step_s=self.focused_max_step_seconds(
                    bodies,
                    groups,
                    settings,
                    selected_index,
                    focus_group_id,
                    focus_target,
                ),
                context_max_step_s=self.context_max_step_seconds(
                    bodies,
                    groups,
                    settings,
                    selected_index,
                    focus_group_id,
                    focus_target,
                ),
                context_entity_ids=[entity.id for entity in context_entities],
            )
            return SimulationJob(plan, state, context_state)

        active = self.active_body_indices(bodies, groups, settings, selected_index, focus_group_id, focus_target)
        state = simulation_state_for_indices(self.state, active)
        plan = SimulationJobPlan(
            mode="body_detail",
            generation=self.generation,
            active_indices=active,
            start_elapsed_s=state.elapsed_s,
            dt_s=dt_s,
            max_step_s=self.max_step_seconds(bodies, groups, settings, selected_index, focus_group_id, focus_target),
        )
        return SimulationJob(plan, state)

    def apply_result(
        self,
        result: SimulationJobResult,
        bodies: list[Body],
        groups: list[SystemGroup],
        settings: SystemSettings,
    ) -> bool:
        if not should_apply_generation(result.plan.generation, self.generation):
            return False

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
    if job.plan.mode == "hybrid_focus":
        focused_result, context_result = advance_hybrid_simulations(
            job.state,
            job.context_state,
            job.plan.dt_s,
            job.plan.max_step_s,
            job.plan.context_max_step_s or job.plan.max_step_s,
        )
        state, position_samples = focused_result
        return SimulationJobResult(job.plan, state, position_samples, context_result)

    state, position_samples = advance_with_samples(
        job.state,
        job.plan.dt_s,
        "post_newtonian",
        job.plan.max_step_s,
    )
    return SimulationJobResult(job.plan, state, position_samples)


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
