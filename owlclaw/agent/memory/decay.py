"""Shared memory score decay helpers."""

from __future__ import annotations

import math


def time_decay(age_hours: float, half_life_hours: float = 168.0) -> float:
    """Exponential decay; half_life_hours=168 -> weight ~= 0.5 at 7 days."""
    if half_life_hours <= 0:
        raise ValueError("half_life_hours must be > 0")
    if age_hours <= 0:
        return 1.0
    return math.exp(-0.693 * age_hours / half_life_hours)

