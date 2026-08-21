"""The Ochiai suspiciousness metric used by AgentTrace SBFL.

This module deliberately contains only the mathematical invariant.  Spectrum
construction and presentation live elsewhere so the formula remains easy to
audit against the research protocol.
"""

from __future__ import annotations

from math import sqrt


def ochiai(*, ef: int, nf: int, ep: int) -> float:
    """Return Ochiai suspiciousness for one source line.

    ``ef`` is the number of failing tests that execute the line, ``nf`` is the
    number of failing tests that do not execute it, and ``ep`` is the number of
    passing tests that execute it.  A zero denominator has no evidence of
    failure correlation and therefore produces ``0.0``.
    """

    counts = {"ef": ef, "nf": nf, "ep": ep}
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} cannot be negative")

    denominator = sqrt((ef + nf) * (ef + ep))
    if denominator == 0.0:
        return 0.0
    return ef / denominator
