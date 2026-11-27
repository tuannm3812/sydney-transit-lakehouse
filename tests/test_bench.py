"""The Spark benchmark's per-feed step, without Spark or Kafka.

The stream applies the worker's own decoder and differ to messages from
Redpanda (bench/diff.py). These tests feed it the messages the producer
would send for a replayed window and check that the rows equal the live
worker's under ``worker verify``, however the messages are cut into
micro-batches and whichever Python worker picks a batch up.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from tests.test_replay import FEEDS, _bucket, _live
from tests.test_worker import T0, TU_FEED
from transit_lakehouse.bench import diff
from transit_lakehouse.bench.producer import Pacer, produce
from transit_lakehouse.bench.report import summarise
from transit_lakehouse.bench.source import Message, stream_hours
from transit_lakehouse.worker import replay as rp
from transit_lakehouse.worker.tables import (
    TABLES,
    commit,
    ensure_tables,
    open_catalog,
)

START, END = T0 + timedelta(hours=1), T0 + timedelta(hours=3)


def _messages(tmp_path: Path) -> tuple[Path, list[tuple[Message, bytes]]]:
    live = _live(tmp_path)
    bucket = _bucket(live, tmp_path)
    scratch = tmp_path / "scratch"
    out = [
        (m, m.value())
        for m in stream_hours(rp.LocalTars(bucket), FEEDS, START, END, scratch)
    ]
    return live, out


def _run(
    msgs: list[tuple[Message, bytes]], rng: random.Random | None
) -> dict[str, pa.Table]:
    """Diff each feed's messages in random micro-batches, carrying only
    the saved state between them (and sometimes losing the cache)."""
    tables: dict[str, list[pa.Table]] = {}
    for feed in FEEDS:
        mine = [(m, v) for m, v in msgs if m.feed == feed.name]
        state = diff.FeedState()
        i = 0
        while i < len(mine):
            n = rng.randint(1, 9) if rng else len(mine)
            if rng and rng.random() < 0.5:
                diff._CACHE.clear()  # another Python worker
            batch = [
                diff.Incoming(m.source_file, v, m.seed, None)
                for m, v in mine[i : i + n]
            ]
            rows, state = diff.diff_batch(feed.name, feed.kind, batch, state)
            tables.setdefault(feed.name, []).append(rows)
            i += n
    return {k: pa.concat_tables(v) for k, v in tables.items()}


def _lakehouse(root: Path, by_feed: dict[str, pa.Table]):
    """Write the union rows to a lakehouse, split per table, as the
    stream's sink does."""
    catalog = open_catalog(root, env={})
    handles = ensure_tables(catalog)
    stamp = datetime.now(UTC)
    for table in by_feed.values():
        for name, cols in TABLES.items():
            part = table.filter(pc.equal(table[diff.TABLE_COL], name))
            data = {
                c: part[c].to_pylist() for c in cols if c in part.column_names
            }
            if name == "bronze.snapshot":
                data["ingested_at"] = [stamp] * part.num_rows
            if part.num_rows:
                commit(name, handles[name], data, {})
    return catalog


def test_messages_are_seeded_then_in_fetch_order(tmp_path: Path) -> None:
    _, msgs = _messages(tmp_path)
    seeds = [m for m, _ in msgs if m.seed]
    assert [m.feed for m in seeds] == [f.name for f in FEEDS]
    assert all(m.fetched_at < START for m in seeds)
    rest = [m for m, _ in msgs if not m.seed]
    assert rest == sorted(rest, key=lambda m: (m.fetched_at, m.feed))
    assert all(START <= m.fetched_at < END for m in rest)
    # Only one hour is unpacked at a time, and none is left behind.
    assert not list((tmp_path / "scratch").rglob("*.pb.gz"))


@pytest.mark.parametrize("seed", [None, 1, 2, 3])
def test_stream_rows_equal_the_live_worker(tmp_path: Path, seed) -> None:
    live, msgs = _messages(tmp_path)
    diff._CACHE.clear()
    rng = random.Random(seed) if seed is not None else None
    bench = _lakehouse(tmp_path / "bench", _run(msgs, rng))
    compared, diffs = rp.verify(
        open_catalog(live, env={}), bench, FEEDS, START, END
    )
    assert compared > 0 and diffs == []


def test_a_resent_file_is_applied_once(tmp_path: Path) -> None:
    _, msgs = _messages(tmp_path)
    tu = [(m, v) for m, v in msgs if m.feed == TU_FEED.name]
    once = _run(tu, None)[TU_FEED.name]
    diff._CACHE.clear()
    state = diff.FeedState()
    rows = []
    for chunk in (tu[:5], tu[3:]):  # files 3 and 4 arrive twice
        batch = [
            diff.Incoming(m.source_file, v, m.seed, None) for m, v in chunk
        ]
        out, state = diff.diff_batch(
            TU_FEED.name, "trip_updates", batch, state
        )
        rows.append(out)
    twice = pa.concat_tables(rows)
    assert twice.num_rows == once.num_rows


def test_union_schema_holds_every_table_column() -> None:
    names = set(diff.union_arrow_schema().names)
    for cols in TABLES.values():
        assert set(cols) <= names
    assert {diff.TABLE_COL, diff.PRODUCED_COL} <= names


def test_pacer_waits_for_each_fetch_offset() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    pacer = Pacer(2.0, clock=lambda: now[0], sleep=sleep)

    def msg(sec: int, seed: bool = False) -> Message:
        at = T0 + timedelta(seconds=sec)
        return Message("f", "trip_updates", "x", at, Path("x"), seed)

    assert pacer.wait(msg(-50, seed=True)) == 0  # seeds go at once
    pacer.wait(msg(0))
    pacer.wait(msg(30))  # due 15 s later at speed 2
    assert slept == [15.0]
    now[0] += 40  # the producer fell behind
    assert pacer.wait(msg(60)) == pytest.approx(25.0)
    assert Pacer(0).wait(msg(10_000)) == 0


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def produce(self, topic, key, value, headers, on_delivery) -> None:
        self.sent.append((topic, key, len(value), dict(headers)))

    def poll(self, _t) -> None:
        return None

    def flush(self, _t) -> int:
        return 0


def test_produce_keys_by_feed_with_headers(tmp_path: Path) -> None:
    _, msgs = _messages(tmp_path)
    paths = []
    for i, (m, v) in enumerate(msgs):
        p = tmp_path / f"m{i}"
        p.write_bytes(v)
        paths.append(
            Message(m.feed, m.kind, m.source_file, m.fetched_at, p, m.seed)
        )
    fake = _FakeProducer()
    stats = produce(paths, fake, speed=0)
    assert stats.messages == len(msgs) and stats.seeds == len(FEEDS)
    topic, key, _, headers = fake.sent[0]
    assert topic == "raw.trip_updates" and key == TU_FEED.name.encode()
    assert headers["seed"] == b"1"
    assert headers["source_file"].startswith(b"raw/feed=")


def test_report_summarises_latency(tmp_path: Path) -> None:
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    t = datetime(2026, 12, 3, tzinfo=UTC)
    with (metrics / "snapshots.jsonl").open("w") as fh:
        for i in range(20):
            fh.write(
                json.dumps(
                    {
                        "batch_id": i,
                        "feed": "buses_tripupdates",
                        "source_file": f"f{i}",
                        "fetched_at": t.isoformat(),
                        "produced_at": (t + timedelta(seconds=i)).isoformat(),
                        "ingested_at": (
                            t + timedelta(seconds=i + 5)
                        ).isoformat(),
                        "committed_at": (
                            t + timedelta(seconds=i + 8)
                        ).isoformat(),
                    }
                )
                + "\n"
            )
    with (metrics / "batches.jsonl").open("w") as fh:
        fh.write(
            json.dumps(
                {
                    "batch_id": 0,
                    "seconds": 3.0,
                    "rows": {"bronze.snapshot": 20},
                }
            )
            + "\n"
        )
    stats = tmp_path / "stats.jsonl"
    stats.write_text(
        json.dumps(
            {
                "Name": "bench-spark",
                "MemUsage": "2GiB / 15GiB",
                "CPUPerc": "150%",
            }
        )
        + "\n"
    )
    text = summarise(metrics, stats)
    assert "| buses_tripupdates | 20 | 8.0 s | 8.0 s | 8.0 s | 5.0 s |" in text
    assert "bench-spark" in text and "2,147 MB" in text


def test_check_lists_missing_hours(tmp_path: Path) -> None:
    from transit_lakehouse.bench.source import missing_hours

    live = _live(tmp_path)
    bucket = _bucket(live, tmp_path)
    source = rp.LocalTars(bucket)
    assert missing_hours(source, FEEDS, START, END) == []
    gone = missing_hours(source, FEEDS, START, END + timedelta(hours=1))
    assert len(gone) == len(FEEDS) and all("hour=05" in k for k in gone)


def test_a_resent_failed_file_is_applied_once(tmp_path: Path) -> None:
    _, msgs = _messages(tmp_path)
    tu = [(m, v) for m, v in msgs if m.feed == TU_FEED.name]
    bad = tu[3][0]
    broken = diff.Incoming(
        bad.source_file.replace(".pb.gz", "0.pb.gz"), b"not gzip", False, None
    )

    def batch(chunk):
        return [
            diff.Incoming(m.source_file, v, m.seed, None) for m, v in chunk
        ]

    diff._CACHE.clear()
    first, state = diff.diff_batch(
        TU_FEED.name,
        "trip_updates",
        [*batch(tu[:4]), broken],
        diff.FeedState(),
    )
    assert state.last_seen == broken.source_file
    assert state.last_file == bad.source_file  # the readable baseline
    again, _ = diff.diff_batch(
        TU_FEED.name, "trip_updates", [broken, *batch(tu[4:5])], state
    )
    files = again.filter(pc.equal(again[diff.TABLE_COL], "bronze.snapshot"))[
        "source_file"
    ].to_pylist()
    assert files == [tu[4][0].source_file]


def test_union_rows_keep_their_types_through_pandas(tmp_path: Path) -> None:
    """The stream hands Spark pandas frames; nullable ints must stay
    ints and timestamps UTC, or Spark rejects the batch."""
    from transit_lakehouse.bench.stream import _pandas_type, output_ddl

    _, msgs = _messages(tmp_path)
    diff._CACHE.clear()
    union = _run(msgs, None)[TU_FEED.name]
    frame = union.to_pandas(types_mapper=_pandas_type)
    back = pa.Table.from_pandas(
        frame, schema=union.schema, preserve_index=False
    )
    assert back.equals(union)
    ddl = output_ddl()
    assert "`delay_s` INT" in ddl and "`fetched_at` TIMESTAMP" in ddl
    assert ddl.count("`") == 2 * len(diff.union_arrow_schema())


class _FakeSpark:
    def __init__(self) -> None:
        self.sql_seen: list[str] = []

    def sql(self, text: str):
        self.sql_seen.append(text)
        return self

    def collect(self) -> list:
        return []


def test_compaction_runs_once_per_closed_data_hour(tmp_path: Path) -> None:
    from transit_lakehouse.bench.stream import Sink, StreamConfig

    spark = _FakeSpark()
    sink = Sink(spark, StreamConfig(data_dir=tmp_path))
    t = datetime(2026, 12, 2, 20, 10, tzinfo=UTC)
    sink.maintain(t)  # first hour seen: nothing is closed yet
    sink.maintain(t + timedelta(minutes=40))
    assert spark.sql_seen == []
    sink.maintain(t + timedelta(hours=1))  # 21:10: hour 20 is closed
    rewrites = [q for q in spark.sql_seen if "rewrite_data_files" in q]
    assert len(rewrites) == len(TABLES)
    assert all(
        "fetched_at >= TIMESTAMP '2026-12-02 20:00:00'" in q
        and "fetched_at < TIMESTAMP '2026-12-02 21:00:00'" in q
        for q in rewrites
    )
    spark.sql_seen.clear()
    sink.maintain(t + timedelta(hours=1, minutes=30))  # still hour 21
    assert spark.sql_seen == []
    sink.maintain(t + timedelta(hours=4))  # flat out: 21, 22, 23 closed
    hours = [q for q in spark.sql_seen if "rewrite_data_files" in q]
    assert len(hours) == 3 * len(TABLES)
