from querykit import parse_query


def test_value_may_contain_equals() -> None:
    assert parse_query("token=abc=def&mode=safe") == {
        "token": "abc=def",
        "mode": "safe",
    }
