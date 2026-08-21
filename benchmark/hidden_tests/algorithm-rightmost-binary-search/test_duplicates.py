from searchkit import rightmost_index


def test_returns_final_duplicate() -> None:
    assert rightmost_index([1, 2, 2, 2, 4], 2) == 3


def test_all_values_equal() -> None:
    assert rightmost_index([7, 7, 7, 7], 7) == 3
