"""Summary statistics over a list of numbers.

Kept dependency-free (no numpy) so the tool installs with just requests+pyyaml.
"""

from __future__ import annotations

import math
from statistics import median, mean, pstdev
from typing import List, Optional, Dict


def percentile(values: List[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile, matching numpy's default method.

    `pct` is in [0, 100]. Returns None for an empty list.
    """
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[int(rank)])
    frac = rank - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def summarize(values: List[float]) -> Optional[Dict[str, float]]:
    """Return median/mean/p95/min/max/stdev for a list, or None if empty."""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return {
        "median": float(median(clean)),
        "mean": float(mean(clean)),
        "p95": percentile(clean, 95),
        "min": float(min(clean)),
        "max": float(max(clean)),
        # population stdev: we measure every run, not a sample of them
        "stdev": float(pstdev(clean)) if len(clean) > 1 else 0.0,
        "n": len(clean),
    }
