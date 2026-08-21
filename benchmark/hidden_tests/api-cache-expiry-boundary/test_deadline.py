from cachekit import is_expired


def test_entry_expires_exactly_at_deadline() -> None:
    assert is_expired(stored_at=100.0, ttl_seconds=30.0, now=130.0)


def test_zero_ttl_expires_immediately() -> None:
    assert is_expired(stored_at=100.0, ttl_seconds=0.0, now=100.0)
