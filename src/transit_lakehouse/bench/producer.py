"""Produce replayed raw snapshots into Redpanda, at a chosen pace.

``speed`` 1 sends each file when its fetch time comes round again
(fetch offsets from the window start), which stands in for the live
collector; 10 sends ten times faster; 0 sends as fast as Redpanda takes
them, for the backfill throughput run. Each message is the landed file's
bytes as stored (gzipped, D-017), keyed by feed, so a feed's files keep
their order in one partition. Headers carry the source path and the seed
flag. The broker stamps each message's time on arrival: the stream's
latency is measured from that stamp (report.py).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from transit_lakehouse.bench.source import Message, topic_for

log = logging.getLogger(__name__)

# The largest bus trip-update file seen is about 3 MB gzipped (8.9 MB
# raw at p95, D-024); 32 MB leaves room without tuning per feed.
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
KINDS = ("trip_updates", "vehicle_positions", "service_alerts")


def producer_config(bootstrap: str) -> dict:
    return {
        "bootstrap.servers": bootstrap,
        "message.max.bytes": MAX_MESSAGE_BYTES,
        "compression.type": "none",  # the files are gzipped already
        "linger.ms": 5,
        "acks": "all",
        "enable.idempotence": True,
    }


def create_topics(bootstrap: str, partitions: int = 4) -> list[str]:
    """One topic per kind, retention long enough for a benchmark day."""
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": bootstrap})
    wanted = [
        NewTopic(
            topic_for(kind),
            num_partitions=partitions,
            replication_factor=1,
            config={
                "max.message.bytes": str(MAX_MESSAGE_BYTES),
                "retention.ms": str(3 * 24 * 3600 * 1000),
            },
        )
        for kind in KINDS
    ]
    existing = set(admin.list_topics(timeout=10).topics)
    todo = [t for t in wanted if t.topic not in existing]
    for topic, fut in admin.create_topics(todo).items():
        fut.result()
        log.info("created topic %s", topic)
    return [t.topic for t in todo]


@dataclass
class ProduceStats:
    messages: int = 0
    seeds: int = 0
    bytes: int = 0
    behind_s: float = 0.0  # worst lag behind the pace
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0


class Pacer:
    """Waits until a message's fetch offset, scaled by ``speed``."""

    def __init__(
        self,
        speed: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.speed, self.clock, self.sleep = speed, clock, sleep
        self.origin = None  # (fetched_at of the first paced message, t0)

    def wait(self, msg: Message) -> float:
        """Sleep until ``msg`` is due; return how late it is (s)."""
        if self.speed <= 0 or msg.seed:
            return 0.0
        if self.origin is None:
            self.origin = (msg.fetched_at, self.clock())
        first, t0 = self.origin
        due = t0 + (msg.fetched_at - first).total_seconds() / self.speed
        ahead = due - self.clock()
        if ahead > 0:
            self.sleep(ahead)
            return 0.0
        return -ahead


def produce(
    messages: Iterable[Message],
    producer,
    speed: float,
    pacer: Pacer | None = None,
) -> ProduceStats:
    """Send every message, paced; flush before returning."""
    stats = ProduceStats()
    pacer = pacer or Pacer(speed)
    started = time.monotonic()

    def done(err, msg) -> None:
        if err is not None:
            stats.failed.append(f"{msg.topic()}: {err}")

    for msg in messages:
        stats.behind_s = max(stats.behind_s, pacer.wait(msg))
        value = msg.value()
        while True:
            try:
                producer.produce(
                    msg.topic,
                    key=msg.feed.encode(),
                    value=value,
                    headers=[
                        ("source_file", msg.source_file.encode()),
                        ("seed", b"1" if msg.seed else b"0"),
                    ],
                    on_delivery=done,
                )
                break
            except BufferError:  # local queue full: let it drain
                producer.poll(0.5)
        producer.poll(0)
        stats.messages += 1
        stats.seeds += msg.seed
        stats.bytes += len(value)
        if stats.messages % 500 == 0:
            log.info(
                "%d messages, %.0f MB, at %s",
                stats.messages,
                stats.bytes / 1e6,
                msg.fetched_at.isoformat(),
            )
    left = producer.flush(120)
    if left:
        stats.failed.append(f"{left} messages not delivered after flush")
    stats.seconds = time.monotonic() - started
    return stats
