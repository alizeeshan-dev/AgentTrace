from intervalkit import merge_intervals


def test_contained_interval_does_not_shrink_merged_end() -> None:
    assert merge_intervals([(1, 10), (2, 3), (8, 12)]) == [(1, 12)]
