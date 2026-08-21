from hypothesis import given
from hypothesis import strategies as st
from windowkit import sliding_windows


@given(
    values=st.lists(st.integers(), min_size=1, max_size=20),
    size=st.integers(min_value=1, max_value=20),
)
def test_valid_window_count(values: list[int], size: int) -> None:
    windows = sliding_windows(values, size)
    assert len(windows) == max(len(values) - size + 1, 0)
