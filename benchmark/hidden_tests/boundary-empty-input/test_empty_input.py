from ministats import mean


def test_empty_input_has_no_mean() -> None:
    assert mean([]) is None


def test_result_remains_numeric_for_nonempty_input() -> None:
    assert mean([-2.0, 2.0]) == 0.0
