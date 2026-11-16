from __future__ import annotations

import random
from datetime import UTC, datetime

from transit_lakehouse.collector.policy import (
    MAX_BACKOFF_S,
    classify,
    exponential_backoff,
    seconds_until_quota_reset,
)

NOW = datetime(2026, 11, 16, 14, 0, tzinfo=UTC)  # 00:00 AEST on the 17th


def test_ok() -> None:
    assert classify(200, {}, 0).ok


def test_backoff_grows_and_caps() -> None:
    rng = random.Random(0)
    small = exponential_backoff(1, rng)
    big = exponential_backoff(20, rng)
    assert 1 <= small <= 2
    assert MAX_BACKOFF_S / 2 <= big <= MAX_BACKOFF_S


def test_429_and_503_back_off() -> None:
    for status in (429, 503):
        v = classify(status, {}, 3, now=NOW, rng=random.Random(1))
        assert not v.ok
        assert v.backoff_s >= 4


def test_retry_after_is_honoured() -> None:
    v = classify(429, {"retry-after": "120"}, 1, now=NOW)
    assert v.backoff_s >= 120


def test_tfnsw_rate_limit_403() -> None:
    v = classify(
        403, {"x-error-detail": "Account Over Rate Limit"}, 1, now=NOW
    )
    assert v.reason == "rate_limited"


def test_quota_403_waits_for_midnight_aest() -> None:
    before = datetime(2026, 11, 16, 13, 0, tzinfo=UTC)  # 23:00 AEST
    v = classify(
        403, {"x-error-detail": "Account Over Quota Limit"}, 1, now=before
    )
    assert v.reason == "quota_exhausted"
    assert 3600 <= v.backoff_s <= 3700


def test_quota_reset_is_fixed_utc_plus_10() -> None:
    # AEST has no daylight saving, even when Sydney is on AEDT.
    assert seconds_until_quota_reset(NOW) == 86400


def test_404_backs_off_long() -> None:
    assert classify(404, {}, 1, now=NOW).backoff_s >= 600
