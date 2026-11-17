"""Poll feeds on a fixed cadence, stagger them, and land snapshots."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.config import Feed, Settings
from transit_lakehouse.collector.landing import Lander, PollRecord, sha256_hex
from transit_lakehouse.collector.policy import classify

log = logging.getLogger(__name__)

# Headers worth keeping in the manifest: they are how the 24-hour report
# discovers the real quota behaviour, since TfNSW exposes no usage API.
KEPT_HEADER_HINTS = ("limit", "quota", "retry", "error", "x-")


def keep_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        k.lower(): v
        for k, v in headers.items()
        if any(h in k.lower() for h in KEPT_HEADER_HINTS)
    }


def parse_header(payload: bytes) -> tuple[int | None, int | None, str]:
    """Return (header timestamp, entity count, error) for a payload."""
    msg = gtfs_realtime_pb2.FeedMessage()
    try:
        msg.ParseFromString(payload)
    except DecodeError as exc:
        return None, None, f"decode_error: {exc}"
    ts = msg.header.timestamp if msg.header.HasField("timestamp") else None
    return ts, len(msg.entity), ""


class RateLimiter:
    """Spaces requests across all feeds to stay under N per second."""

    def __init__(self, max_rps: float) -> None:
        self._spacing = 1.0 / max_rps
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._last + self._spacing - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class Collector:
    """Owns the HTTP client, the lander and one task per feed."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.lander = Lander(settings.data_dir)
        self.limiter = RateLimiter(settings.max_rps)
        self.clock = clock
        self._failures: dict[str, int] = {}
        self._client = client or httpx.AsyncClient(
            timeout=settings.timeout_s,
            headers={
                "Authorization": f"apikey {settings.api_key}",
                "Accept": "application/x-google-protobuf",
                "User-Agent": "transit-lakehouse-collector/0.1",
            },
        )
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def poll_once(self, feed: Feed) -> tuple[PollRecord, float]:
        """Fetch one snapshot. Returns the record and a backoff in s."""
        await self.limiter.wait()
        started = time.monotonic()
        status: int | None = None
        headers: dict[str, str] = {}
        payload = b""
        error: str | None = None
        try:
            resp = await self._client.get(feed.url)
            status = resp.status_code
            headers = keep_headers(resp.headers)
            payload = resp.content
            if status != 200:
                error = resp.text[:300]
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
        fetched_at = self.clock()
        latency_ms = (time.monotonic() - started) * 1000

        failures = 0 if status == 200 else self._failures.get(feed.name, 0) + 1
        self._failures[feed.name] = failures
        verdict = classify(status, headers, failures, now=fetched_at)

        rec = PollRecord(
            feed=feed.name,
            fetched_at=fetched_at.isoformat(),
            status=status,
            outcome="new",
            latency_ms=round(latency_ms, 1),
            headers=headers,
            error=error,
        )
        if not verdict.ok:
            rec.outcome = "network_error" if status is None else "http_error"
            rec.error = f"{verdict.reason}: {error or ''}".strip()
            self.lander.record(rec)
            log.warning(
                "%s %s, backing off %.0fs",
                feed.name,
                verdict.reason,
                verdict.backoff_s,
            )
            return rec, verdict.backoff_s

        digest = sha256_hex(payload)
        header_ts, n_entities, parse_err = parse_header(payload)
        rec.size_bytes = len(payload)
        rec.sha256 = digest
        rec.header_timestamp = header_ts
        rec.entity_count = n_entities
        rec.error = parse_err or None
        if self.lander.is_unchanged(feed.name, digest):
            rec.outcome = "unchanged"
        else:
            # Land even if parsing failed: raw bytes are the source of
            # truth and a bad payload is evidence worth keeping.
            rec.path = self.lander.land(feed.name, fetched_at, payload)
            rec.stored_bytes = self.lander.last_stored_bytes
        self.lander.record(rec)
        self._heartbeat()
        return rec, 0.0

    async def run_feed(self, feed: Feed, offset_s: float) -> None:
        """Poll one feed forever on a drift-free schedule."""
        loop = asyncio.get_running_loop()
        next_due = loop.time() + offset_s
        while not self._stop.is_set():
            if await self._sleep_until(next_due):
                return
            _, backoff = await self.poll_once(feed)
            now = loop.time()
            next_due += feed.interval_s
            if backoff:
                next_due = max(next_due, now + backoff)
            if next_due < now:
                # Missed slots stay missed (they count against capture),
                # but realign to the original grid rather than bursting.
                behind = now - next_due
                next_due += (behind // feed.interval_s + 1) * feed.interval_s

    async def run(self) -> None:
        self.settings.check_quota_budget()
        log.info(
            "Starting %d feeds, projected %.0f calls/day of %d quota",
            len(self.settings.feeds),
            self.settings.projected_calls_per_day,
            self.settings.daily_quota,
        )
        tasks = [
            asyncio.create_task(
                self.run_feed(feed, i * self.settings.stagger_s),
                name=feed.name,
            )
            for i, feed in enumerate(self.settings.feeds)
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            await self._client.aclose()

    async def _sleep_until(self, deadline: float) -> bool:
        """Sleep until a loop-time deadline. True if stopped meanwhile."""
        delay = deadline - asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(delay, 0))
        except TimeoutError:
            return False
        return True

    def _heartbeat(self) -> None:
        path: Path = self.settings.data_dir / "_heartbeat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.clock().isoformat(), encoding="utf-8")
