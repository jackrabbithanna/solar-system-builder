# playback.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK-free simulation playback helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .models import Body
from .physics import SimulationState, advance_with_samples

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
