from listkit import flatten_groups


def test_items_within_each_group_keep_their_order() -> None:
    assert flatten_groups([["a", "b"], ["c", "d"]]) == ["a", "b", "c", "d"]
