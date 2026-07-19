# frame_exports.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Non-mutating relative-frame JSON and CSV export helpers."""

from __future__ import annotations

import csv
import io
from dataclasses import replace

from .analysis_frames import AnalysisFrameSpec, relative_diagnostics
from .models import ModelError, ReferenceOrigin, SolarSystem
from .physics import SimulationState
from .reference_frames import (
    ReferenceFrameTransform,
    origin_for_body,
    origin_for_group,
    origin_for_system,
    recompute_osculating_metadata,
    transform_system_reference_frame,
)
from .standard_frames import shift_epoch


def relative_system_snapshot(
    system: SolarSystem,
    state: SimulationState,
    origin_kind: str,
    origin_id: str | None = None,
) -> SolarSystem:
    """Return an importable inertial snapshot centered at the selected origin."""

    candidate = SolarSystem.from_dict(system.to_dict())
    state.apply_to_bodies(candidate.bodies)
    frame = candidate.reference_frame
    if frame is None:
        raise ModelError("relative export requires reference-frame metadata")
    frame.epoch = shift_epoch(frame.epoch, frame.time_scale, state.elapsed_s)
    if origin_kind == "body":
        position, velocity = origin_for_body(candidate, origin_id or "")
        origin = ReferenceOrigin("body", origin_id)
    elif origin_kind == "group_barycenter":
        position, velocity = origin_for_group(candidate, origin_id or "")
        origin = ReferenceOrigin("group_barycenter", origin_id)
    elif origin_kind == "system_barycenter":
        position, velocity = origin_for_system(candidate)
        origin = ReferenceOrigin("system_barycenter", None)
    elif origin_kind == "fixed":
        position = velocity = (0.0, 0.0, 0.0)
        origin = ReferenceOrigin("custom", "inertial-snapshot-origin")
    else:
        raise ModelError(f"unsupported relative export origin {origin_kind}")
    target = replace(frame, origin=origin)
    exported = transform_system_reference_frame(
        candidate,
        target,
        ReferenceFrameTransform(
            tuple(position),
            tuple(velocity),
        ),
    )
    recompute_osculating_metadata(exported)
    exported.epoch = (
        f"{target.epoch} {target.time_scale}, "
        f"{target.reference_system}/{target.reference_plane}, {target.center_id}"
    )
    exported.validate()
    return exported


def serialize_relative_csv(
    system: SolarSystem,
    state: SimulationState,
    spec: AnalysisFrameSpec,
) -> bytes:
    kinematics, rows = relative_diagnostics(system, state, spec)
    frame = system.reference_frame
    epoch = (
        shift_epoch(frame.epoch, frame.time_scale, state.elapsed_s)
        if frame is not None
        else str(state.elapsed_s)
    )
    scale = frame.time_scale if frame is not None else ""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "body_id",
            "name",
            "mass_kg",
            "epoch",
            "time_scale",
            "analysis_frame",
            "x_m",
            "y_m",
            "z_m",
            "vx_mps",
            "vy_mps",
            "vz_mps",
            "gravity_x_mps2",
            "gravity_y_mps2",
            "gravity_z_mps2",
            "translation_x_mps2",
            "translation_y_mps2",
            "translation_z_mps2",
            "coriolis_x_mps2",
            "coriolis_y_mps2",
            "coriolis_z_mps2",
            "centrifugal_x_mps2",
            "centrifugal_y_mps2",
            "centrifugal_z_mps2",
            "euler_x_mps2",
            "euler_y_mps2",
            "euler_z_mps2",
            "apparent_total_x_mps2",
            "apparent_total_y_mps2",
            "apparent_total_z_mps2",
        )
    )
    for row in rows:
        writer.writerow(
            (
                row.body_id,
                row.name,
                f"{row.mass_kg:.17g}",
                epoch,
                scale,
                spec.label,
                *(_format_vector(row.position_m)),
                *(_format_vector(row.velocity_mps)),
                *(_format_vector(row.gravitational_acceleration_mps2)),
                *(_format_vector(row.translational_acceleration_mps2)),
                *(_format_vector(row.coriolis_acceleration_mps2)),
                *(_format_vector(row.centrifugal_acceleration_mps2)),
                *(_format_vector(row.euler_acceleration_mps2)),
                *(_format_vector(row.total_apparent_acceleration_mps2)),
            )
        )
    return output.getvalue().encode("utf-8")


def _format_vector(vector):
    return tuple(f"{value:.17g}" for value in vector)
