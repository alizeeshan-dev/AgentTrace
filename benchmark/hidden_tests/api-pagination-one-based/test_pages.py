from pagekit import paginate


def test_first_page_starts_at_first_item() -> None:
    assert paginate(["a", "b", "c", "d"], 1, 2) == ["a", "b"]


def test_second_page_follows_first() -> None:
    assert paginate(["a", "b", "c", "d"], 2, 2) == ["c", "d"]
