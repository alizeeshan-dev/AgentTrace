from booking import can_reserve


def test_reservation_filling_capacity_is_allowed() -> None:
    assert can_reserve(current=5, requested=3, capacity=8)
