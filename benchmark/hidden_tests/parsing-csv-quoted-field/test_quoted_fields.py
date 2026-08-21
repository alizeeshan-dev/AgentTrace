from csvkit import parse_row


def test_quoted_comma_stays_in_one_field() -> None:
    assert parse_row('"Lovelace, Ada",37,London') == [
        "Lovelace, Ada",
        "37",
        "London",
    ]


def test_escaped_quote_is_decoded() -> None:
    assert parse_row('"Ada ""Countess""",37') == ['Ada "Countess"', "37"]
