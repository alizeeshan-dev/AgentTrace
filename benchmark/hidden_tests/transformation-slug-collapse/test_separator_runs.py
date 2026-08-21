from slugkit import to_slug


def test_long_separator_run_collapses_once() -> None:
    assert to_slug("alpha___beta") == "alpha-beta"


def test_mixed_separator_run_collapses_once() -> None:
    assert to_slug("alpha _  beta") == "alpha-beta"
