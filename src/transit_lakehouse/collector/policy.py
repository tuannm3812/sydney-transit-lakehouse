"""Decide what a poll result means and how long to back off.

TfNSW signals limits in more than one way (checked 2026-11-16 against
the Open Data Hub "API Basics" page and forum threads, see D-004):

* 403 with ``X-Error-Detail: Account Over Rate Limit`` (5 req/s)
* 403 with ``X-Error-Detail: Account Over Quota Limit`` (60k/day)
* 503 "Your API quota or rate limit has been exceeded" (gateway)
* 401 can also appear when over limits, not only for a bad key

429 is not documented but is handled the same way, since gateways change.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

# TfNSW resets the daily counter at midnight AEST. AEST is a fixed
# UTC+10 (no daylight saving), unlike Sydney's civil time.
AEST = timezone(timedelta(hours=10), "AEST")

BASE_BACKOFF_S = 2.0
MAX_BACKOFF_S = 300.0
AUTH_BACKOFF_S = 300.0
# A lone 401 is usually a gateway blip, not a bad key: on 2026-11-19
# four feeds each got one 401 while the other eight kept getting 200s
# on the same key. Retry at normal cadence until this many in a row.
AUTH_GRACE_FAILURES = 3
NOT_FOUND_BACKOFF_S = 900.0
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Verdict:
    """Classification of one response."""

    ok: bool
    reason: str
    backoff_s: float = 0.0


def seconds_until_quota_reset(now: datetime) -> float:
    """Seconds until the next midnight AEST."""
    local = now.astimezone(AEST)
    nxt = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (nxt - local).total_seconds()


def exponential_backoff(
    failures: int, rng: random.Random | None = None
) -> float:
    """Exponential backoff with equal jitter, capped."""
    rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
    ceiling = min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2 ** max(failures - 1, 0))
    return ceiling / 2 + rng.uniform(0, ceiling / 2)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form; rare enough to ignore


def classify(
    status: int | None,
    headers: dict[str, str],
    failures: int,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Verdict:
    """Map an HTTP outcome to ok/backoff.

    Args:
        status: HTTP status, or None for a network error.
        headers: Response headers, lower-cased keys.
        failures: Consecutive failures including this one.
        now: Current time, injectable for tests.
        rng: Random source, injectable for tests.
    """
    now = now or datetime.now(UTC)
    if status == 200:
        return Verdict(ok=True, reason="ok")
    if status == 304:
        # Answer to a conditional request: the file has not changed.
        return Verdict(ok=True, reason="not_modified")

    detail = headers.get("x-error-detail", "").lower()
    if status == 403 and "quota" in detail:
        # Retrying before the reset only burns goodwill; wait it out.
        wait = seconds_until_quota_reset(now) + 30
        return Verdict(False, "quota_exhausted", wait)

    backoff = exponential_backoff(failures, rng)
    retry_after = parse_retry_after(headers.get("retry-after"))
    if retry_after is not None:
        backoff = max(backoff, retry_after)

    if status == 403 and "rate" in detail:
        return Verdict(False, "rate_limited", backoff)
    if status in RETRYABLE_STATUSES:
        return Verdict(False, f"http_{status}", backoff)
    if status is None:
        return Verdict(False, "network_error", backoff)
    if status == 401:
        # see D-031: back off hard only once the 401s repeat.
        if failures < AUTH_GRACE_FAILURES:
            return Verdict(False, "unauthorised", backoff)
        return Verdict(False, "unauthorised", max(backoff, AUTH_BACKOFF_S))
    if status == 404:
        return Verdict(False, "not_found", NOT_FOUND_BACKOFF_S)
    return Verdict(False, f"http_{status}", max(backoff, 60.0))
