from mapkit import deep_merge


def test_nested_override_preserves_siblings() -> None:
    base = {"database": {"host": "db", "port": 5432}, "debug": False}
    override = {"database": {"port": 6432}}
    assert deep_merge(base, override) == {
        "database": {"host": "db", "port": 6432},
        "debug": False,
    }


def test_inputs_are_not_mutated() -> None:
    base = {"service": {"retries": 2}}
    override = {"service": {"timeout": 5}}
    deep_merge(base, override)
    assert base == {"service": {"retries": 2}}
    assert override == {"service": {"timeout": 5}}
