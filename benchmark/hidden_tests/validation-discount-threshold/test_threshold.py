from pricing import discount_rate


def test_threshold_itself_receives_discount() -> None:
    assert discount_rate(100.0) == 0.1
