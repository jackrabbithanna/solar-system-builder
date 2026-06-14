# scales.py
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Scale, unit, and playback policy helpers."""

from __future__ import annotations

import math

from .constants import AU, DAY, G, YEAR
from .models import Body

LIGHT_YEAR = 299_792_458.0 * YEAR

TIME_UNITS: tuple[tuple[str, str, float], ...] = (
    ("Days", "days", DAY),
    ("Years", "years", YEAR),
    ("Decades", "decades", 10.0 * YEAR),
    ("Centuries", "centuries", 100.0 * YEAR),
)

DISTANCE_UNITS: tuple[tuple[str, str, float], ...] = (
    ("km", "km", 1_000.0),
    ("AU", "AU", AU),
    ("kAU", "kAU", 1_000.0 * AU),
    ("ly", "ly", LIGHT_YEAR),
)

ACCURACY_LABELS: tuple[tuple[str, str], ...] = (
    ("High", "high"),
    ("Balanced", "balanced"),
    ("Fast", "fast"),
)

VIEW_MODE_LABELS: tuple[tuple[str, str], ...] = (
    ("Fit System", "fit_system"),
    ("Follow Selected", "follow_selected"),
    ("Log Overview", "log_overview"),
)

_PERIOD_FRACTIONS = {
    "high": 1.0 / 120.0,
    "balanced": 1.0 / 80.0,
    "fast": 1.0 / 32.0,
}

_PROFILE_CLAMPS_S = {
    "high": (0.125 * DAY, 7.0 * DAY),
    "balanced": (0.25 * DAY, 30.0 * DAY),
    "fast": (0.5 * DAY, 120.0 * DAY),
}


def unit_index(items: tuple[tuple[str, str, float], ...] | tuple[tuple[str, str], ...], value: str) -> int:
    return next((index for index, item in enumerate(items) if item[1] == value), 0)


def time_unit_for_seconds(seconds: float) -> str:
    abs_seconds = abs(seconds)
    if abs_seconds >= 100.0 * YEAR:
        return "centuries"
    if abs_seconds >= 10.0 * YEAR:
        return "decades"
    if abs_seconds >= YEAR:
        return "years"
    return "days"


def unit_factor(items: tuple[tuple[str, str, float], ...], value: str) -> float:
    return next((item[2] for item in items if item[1] == value), items[0][2])


def format_elapsed_time(seconds: float) -> str:
    abs_seconds = abs(seconds)
    sign = "-" if seconds < 0.0 else ""
    if abs_seconds >= 100.0 * YEAR:
        return f"{sign}{abs_seconds / (100.0 * YEAR):,.2f} centuries"
    if abs_seconds >= 2.0 * YEAR:
        return f"{sign}{abs_seconds / YEAR:,.2f} years"
    return f"{sign}{abs_seconds / DAY:,.2f} days"


def derived_max_step_s(bodies: list[Body], accuracy_profile: str) -> float:
    fraction = _PERIOD_FRACTIONS.get(accuracy_profile, _PERIOD_FRACTIONS["balanced"])
    lower, upper = _PROFILE_CLAMPS_S.get(accuracy_profile, _PROFILE_CLAMPS_S["balanced"])
    shortest_period = _shortest_parent_orbit_period_s(bodies)
    if shortest_period is None:
        return DAY
    return max(lower, min(upper, shortest_period * fraction))


def recommended_trail_sample_interval_s(visible_step_s: float) -> float:
    return max(DAY, abs(visible_step_s))


def _shortest_parent_orbit_period_s(bodies: list[Body]) -> float | None:
    bodies_by_id = {body.id: body for body in bodies}
    shortest: float | None = None
    for body in bodies:
        if body.parent_id is None:
            continue
        parent = bodies_by_id.get(body.parent_id)
        if parent is None:
            continue
        radius_m = math.dist(body.position_m, parent.position_m)
        if radius_m <= 0.0 or parent.mass_kg <= 0.0:
            continue
        period_s = math.tau * math.sqrt(radius_m**3 / (G * parent.mass_kg))
        if math.isfinite(period_s):
            shortest = period_s if shortest is None else min(shortest, period_s)
    return shortest
