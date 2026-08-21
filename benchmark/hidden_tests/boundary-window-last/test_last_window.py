from windowkit import sliding_windows


def test_all_windows_include_the_final_valid_start() -> None:
    assert sliding_windows([1, 2, 3, 4], 2) == [[1, 2], [2, 3], [3, 4]]


def test_window_equal_to_input_length() -> None:
    assert sliding_windows([1, 2], 2) == [[1, 2]]
