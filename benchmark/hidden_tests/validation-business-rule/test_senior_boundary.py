from loyalty import qualifies_for_senior_rate


def test_customer_qualifies_on_sixty_fifth_birthday() -> None:
    assert qualifies_for_senior_rate(65, True)


def test_customer_below_threshold_does_not_qualify() -> None:
    assert not qualifies_for_senior_rate(64, True)
