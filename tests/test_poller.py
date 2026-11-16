from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from tests.conftest import make_feed_bytes
from transit_lakehouse.collector.config import Feed, Settings
from transit_lakehouse.collector.poller import Collector

NOW = datetime(2026, 11, 16, 3, 0, 5, tzinfo=UTC)


def _collector(settings: Settings) -> Collector:
    client = httpx.AsyncClient(
        headers={"Authorization": f"apikey {settings.api_key}"}
    )
    return Collector(settings, client=client, clock=lambda: NOW)


@respx.mock
async def test_new_then_unchanged(settings: Settings, feed: Feed) -> None:
    body = make_feed_bytes(int(NOW.timestamp()) - 5)
    route = respx.get(feed.url).mock(
        return_value=httpx.Response(200, content=body)
    )
    c = _collector(settings)
    rec1, backoff1 = await c.poll_once(feed)
    rec2, _ = await c.poll_once(feed)
    assert route.calls[0].request.headers["authorization"] == (
        "apikey test-key"
    )
    assert (rec1.outcome, rec2.outcome) == ("new", "unchanged")
    assert backoff1 == 0
    assert rec1.entity_count == 2
    assert rec1.fetched_at == NOW.isoformat()
    landed = list((settings.data_dir / "raw").rglob("*.pb"))
    assert len(landed) == 1
    assert landed[0].read_bytes() == body


@respx.mock
async def test_rate_limit_backs_off_without_landing(
    settings: Settings, feed: Feed
) -> None:
    respx.get(feed.url).mock(
        return_value=httpx.Response(
            403, headers={"X-Error-Detail": "Account Over Rate Limit"}
        )
    )
    rec, backoff = await _collector(settings).poll_once(feed)
    assert rec.outcome == "http_error"
    assert rec.headers["x-error-detail"] == "Account Over Rate Limit"
    assert backoff > 0
    assert not (settings.data_dir / "raw").exists()


@respx.mock
async def test_network_error_is_recorded(
    settings: Settings, feed: Feed
) -> None:
    respx.get(feed.url).mock(side_effect=httpx.ConnectTimeout("boom"))
    rec, backoff = await _collector(settings).poll_once(feed)
    assert rec.outcome == "network_error"
    assert rec.status is None
    assert backoff > 0


@respx.mock
async def test_schedule_keeps_cadence_and_stops(settings: Settings) -> None:
    import asyncio

    fast = Feed(
        "fast", "vehicle_positions", "https://api.example.test/f", 0.05
    )
    respx.get(fast.url).mock(
        return_value=httpx.Response(200, content=make_feed_bytes(1))
    )
    c = _collector(settings)
    task = asyncio.create_task(c.run_feed(fast, offset_s=0))
    await asyncio.sleep(0.32)
    c.stop()
    await asyncio.wait_for(task, timeout=1)
    calls = len(respx.calls)
    assert 5 <= calls <= 8  # ~0.32 / 0.05, no bursts
