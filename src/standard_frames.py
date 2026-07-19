# standard_frames.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Verified astronomical axes and time-scale helpers.

The functions in this module are GTK-free.  Matrices map ICRF Cartesian
components into the requested axes at one epoch; callers decide whether that
orientation remains frozen or is re-evaluated as a moving analysis frame.
"""

from __future__ import annotations

import math

import numpy as np

try:
    import erfa
    from astropy.time import Time, TimeDelta
    from astropy.utils import iers
except ImportError as error:  # pragma: no cover - packaging smoke tests cover this
    raise RuntimeError(
        "Astropy and PyERFA are required for standard reference frames"
    ) from error

from .models import ModelError, REFERENCE_AXES, REFERENCE_TIME_SCALES, SystemReferenceFrame

iers.conf.auto_download = False
iers.conf.iers_degraded_accuracy = "error"

JPL_J2000_OBLIQUITY_ARCSEC = 84381.448

_GALACTIC_FROM_ICRS = np.array(
    (
        (-0.0548755604162154, -0.8734370902348850, -0.4838350155487132),
        (0.4941094278755837, -0.4448296299600112, 0.7469822444972189),
        (-0.8676661490190047, -0.1980763734312015, 0.4559837761750669),
    ),
    dtype=float,
)


def parse_epoch(epoch: str, time_scale: str) -> Time:
    scale = time_scale.strip().upper()
    if scale not in REFERENCE_TIME_SCALES:
        raise ModelError(f"unsupported reference frame time scale {time_scale}")
    if not epoch.strip():
        raise ModelError("reference frame epoch is required")
    try:
        return Time(epoch.strip(), format="iso", scale=scale.lower())
    except Exception as error:
        raise ModelError(
            "reference frame epoch must be an ISO date and time"
        ) from error


def format_epoch(time: Time, time_scale: str | None = None) -> str:
    scale = (time_scale or time.scale).upper()
    converted = getattr(time, scale.lower())
    converted.precision = 6
    value = str(converted.to_value("iso", subfmt="date_hms"))
    return value.removesuffix(".000000")


def convert_epoch(epoch: str, source_scale: str, target_scale: str) -> str:
    if target_scale.upper() not in REFERENCE_TIME_SCALES:
        raise ModelError(f"unsupported reference frame time scale {target_scale}")
    return format_epoch(parse_epoch(epoch, source_scale), target_scale)


def shift_epoch(epoch: str, time_scale: str, elapsed_s: float) -> str:
    if not math.isfinite(elapsed_s):
        raise ModelError("simulation elapsed time must be finite")
    time = parse_epoch(epoch, time_scale) + TimeDelta(elapsed_s, format="sec")
    return format_epoch(time, time_scale)


def epoch_delta_seconds(
    source_epoch: str,
    source_scale: str,
    target_epoch: str,
    target_scale: str,
) -> float:
    source = parse_epoch(source_epoch, source_scale)
    target = parse_epoch(target_epoch, target_scale)
    return float((target.tai - source.tai).to_value("sec"))


def axes_matrix(axes_id: str, epoch: str, time_scale: str) -> tuple[tuple[float, ...], ...]:
    """Return the proper ICRF-to-axes rotation evaluated at ``epoch``."""

    if axes_id not in REFERENCE_AXES:
        raise ModelError(f"unsupported reference axes {axes_id}")
    if axes_id == "custom":
        raise ModelError("custom axes require an explicit rotation matrix")
    time = parse_epoch(epoch, time_scale)
    tt = time.tt
    date1, date2 = float(tt.jd1), float(tt.jd2)

    if axes_id == "icrf":
        matrix = np.identity(3)
    elif axes_id == "fk5_j2000":
        fk5_to_icrs, _fk5_spin = erfa.fk5hip()
        matrix = np.asarray(fk5_to_icrs).T
    elif axes_id == "mean_equator_of_date":
        matrix = erfa.pmat06(date1, date2)
    elif axes_id == "true_equator_of_date":
        matrix = erfa.pnm06a(date1, date2)
    elif axes_id == "jpl_ecliptic_j2000":
        matrix = _rotation_x(-math.radians(JPL_J2000_OBLIQUITY_ARCSEC / 3600.0))
    elif axes_id == "mean_ecliptic_of_date":
        matrix = erfa.ecm06(date1, date2)
    elif axes_id == "true_ecliptic_of_date":
        _dpsi, deps, epsa, _rb, _rp, _rbp, _rn, rbpn = erfa.pn06a(
            date1, date2
        )
        matrix = _rotation_x(-(float(epsa) + float(deps))) @ np.asarray(rbpn)
    elif axes_id == "galactic_iau1958":
        matrix = _GALACTIC_FROM_ICRS
    else:  # pragma: no cover - exhaustive guard
        raise ModelError(f"unsupported reference axes {axes_id}")
    return _proper_matrix(matrix)


def rotation_between_frames(
    source: SystemReferenceFrame,
    target: SystemReferenceFrame,
) -> tuple[tuple[float, ...], ...]:
    if source.axes_id == "custom" or target.axes_id == "custom":
        raise ModelError("automatic transforms require verified source and target axes")
    source_matrix = np.asarray(
        axes_matrix(source.axes_id, source.epoch, source.time_scale), dtype=float
    )
    target_matrix = np.asarray(
        axes_matrix(target.axes_id, target.epoch, target.time_scale), dtype=float
    )
    return _proper_matrix(target_matrix @ source_matrix.T)


def _rotation_x(angle: float) -> np.ndarray:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array(
        ((1.0, 0.0, 0.0), (0.0, cosine, -sine), (0.0, sine, cosine)),
        dtype=float,
    )


def _proper_matrix(matrix) -> tuple[tuple[float, ...], ...]:
    array = np.asarray(matrix, dtype=float)
    if array.shape != (3, 3) or not np.all(np.isfinite(array)):
        raise ModelError("standard frame produced an invalid rotation matrix")
    if not np.allclose(array @ array.T, np.identity(3), atol=2.0e-12, rtol=0.0):
        raise ModelError("standard frame rotation is not orthonormal")
    if not math.isclose(float(np.linalg.det(array)), 1.0, abs_tol=2.0e-12):
        raise ModelError("standard frame rotation must have determinant +1")
    return tuple(tuple(float(value) for value in row) for row in array)
