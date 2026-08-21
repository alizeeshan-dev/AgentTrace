"""Evaluator-only behavioral property for the mean repair task."""

from __future__ import annotations

from agentrace_property_runtime import fail
from hypothesis import given
from hypothesis import strategies as st
from ministats import mean


@given(
    st.lists(
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        max_size=5,
    )
)
def test_mean_matches_the_arithmetic_definition(values: list[float]) -> None:
    expected = None if not values else sum(values) / len(values)
    try:
        observed = mean(values)
    except Exception as error:
        fail(
            input_value=values,
            expected=expected,
            observed={"exception": type(error).__name__},
            exception_type=type(error).__name__,
            location_hints=["ministats/summary.py:7"],
        )
        raise AssertionError("property runtime did not raise") from error
    if observed != expected:
        fail(
            input_value=values,
            expected=expected,
            observed=observed,
            exception_type="AssertionError",
            location_hints=["ministats/summary.py:7"],
        )
