from app.services.rate_limit import SlidingWindowRateLimiter


def test_sliding_window_blocks_and_reports_retry_time() -> None:
    limiter = SlidingWindowRateLimiter()

    assert limiter.consume("login:user", limit=2, window_seconds=60, now=100).allowed
    assert limiter.consume("login:user", limit=2, window_seconds=60, now=110).allowed
    blocked = limiter.consume("login:user", limit=2, window_seconds=60, now=120)

    assert blocked.allowed is False
    assert blocked.retry_after_seconds == 41
    assert limiter.consume("login:user", limit=2, window_seconds=60, now=161).allowed


def test_sliding_window_keys_and_clear_are_independent() -> None:
    limiter = SlidingWindowRateLimiter()
    assert limiter.consume("one", limit=1, window_seconds=60, now=1).allowed
    assert limiter.consume("two", limit=1, window_seconds=60, now=1).allowed
    assert not limiter.consume("one", limit=1, window_seconds=60, now=2).allowed

    limiter.clear("one")

    assert limiter.consume("one", limit=1, window_seconds=60, now=2).allowed
    assert not limiter.consume("two", limit=1, window_seconds=60, now=2).allowed
