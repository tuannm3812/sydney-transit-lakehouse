"""The Spark Structured Streaming job: Redpanda → Iceberg (D-023, D-041).

Kafka source (the three ``raw.<kind>`` topics) → group by feed with
``applyInPandasWithState`` (diff.py: the worker's decoder and differ,
state = the feed's last readable file) → ``foreachBatch``, which appends
each table's rows to Iceberg through Spark's own Iceberg writer and logs
latency per snapshot. The catalog is Iceberg's JDBC catalog on a SQLite
file in the worker's layout, so PyIceberg (and so ``worker verify``,
``worker status`` and the gold build) opens the result unchanged.

Each closed data hour is compacted, and old snapshots expired, with
Iceberg's Spark procedures, as ``run --loop`` does with its own code
(D-032), so the ops load is measured too.
"""

# Table names come from the worker's fixed table list, never from data:
# ruff: noqa: S608

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from transit_lakehouse.bench.diff import (
    PRODUCED_COL,
    TABLE_COL,
    FeedState,
    Incoming,
    diff_batch,
    union_arrow_schema,
)
from transit_lakehouse.bench.producer import MAX_MESSAGE_BYTES
from transit_lakehouse.bench.source import kind_of_topic, topic_for
from transit_lakehouse.worker.tables import (
    SORT_KEYS,
    TABLES,
    ensure_tables,
    open_catalog,
)

log = logging.getLogger(__name__)

SPARK_VERSION = "4.1.4"
ICEBERG_VERSION = "1.12.0"
SQLITE_JDBC_VERSION = "3.54.1.0"
PACKAGES = (
    f"org.apache.iceberg:iceberg-spark-runtime-4.1_2.13:{ICEBERG_VERSION}",
    f"org.apache.spark:spark-sql-kafka-0-10_2.13:{SPARK_VERSION}",
    f"org.xerial:sqlite-jdbc:{SQLITE_JDBC_VERSION}",
)
CATALOG = "transit"  # the name PyIceberg's SqlCatalog writes (tables.py)
KINDS = ("trip_updates", "vehicle_positions", "service_alerts")
STATE_DDL = "last_file STRING, last_value BINARY, last_seen STRING"


@dataclass(frozen=True)
class StreamConfig:
    data_dir: Path  # the bench lakehouse goes in data_dir/lakehouse/
    bootstrap: str = "localhost:9092"
    trigger_s: int = 15
    max_offsets: int = 400  # messages per micro-batch, all topics
    shuffle_partitions: int = 4
    driver_memory: str = "3g"
    maintain: bool = True

    @property
    def checkpoint(self) -> Path:
        return self.data_dir / "bench" / "checkpoint"

    @property
    def metrics(self) -> Path:
        return self.data_dir / "bench" / "metrics"


def session(cfg: StreamConfig, app: str = "transit-bench"):
    from pyspark.sql import SparkSession

    root = (cfg.data_dir / "lakehouse").resolve()
    # PyIceberg makes the catalog file and the tables, with the worker's
    # schemas, partitioning and properties; Spark only appends.
    ensure_tables(open_catalog(cfg.data_dir))
    cat = f"spark.sql.catalog.{CATALOG}"
    builder = SparkSession.builder
    if os.environ.get("BENCH_IVY_DIR"):  # jars fetched at image build
        builder = builder.config("spark.jars.ivy", os.environ["BENCH_IVY_DIR"])
    return (
        builder.appName(app)
        .master("local[*]")
        .config("spark.jars.packages", ",".join(PACKAGES))
        .config("spark.driver.memory", cfg.driver_memory)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions."
            "IcebergSparkSessionExtensions",
        )
        .config(cat, "org.apache.iceberg.spark.SparkCatalog")
        .config(f"{cat}.type", "jdbc")
        .config(f"{cat}.uri", f"jdbc:sqlite:{root / 'catalog.db'}")
        .config(f"{cat}.warehouse", str(root / "warehouse"))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", cfg.shuffle_partitions)
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )


def spark_type(arrow_type) -> str:
    import pyarrow as pa

    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "STRING"
    if pa.types.is_timestamp(arrow_type):
        return "TIMESTAMP"
    if pa.types.is_date(arrow_type):
        return "DATE"
    if pa.types.is_int32(arrow_type):
        return "INT"
    if pa.types.is_int64(arrow_type):
        return "BIGINT"
    if pa.types.is_float32(arrow_type):
        return "FLOAT"
    if pa.types.is_float64(arrow_type):
        return "DOUBLE"
    if pa.types.is_boolean(arrow_type):
        return "BOOLEAN"
    raise TypeError(f"no Spark type for {arrow_type}")


def output_ddl() -> str:
    return ", ".join(
        f"`{f.name}` {spark_type(f.type)}" for f in union_arrow_schema()
    )


def _group_step(key, frames, state):
    """applyInPandasWithState: one feed's messages of one micro-batch."""
    import pandas as pd

    (feed,) = key
    batch = pd.concat(list(frames), ignore_index=True)
    batch = batch.sort_values(["partition", "offset"])
    kind = kind_of_topic(batch["topic"].iloc[0])
    saved = FeedState()
    if state.exists:
        last_file, last_value, last_seen = state.get
        saved = FeedState(last_file, bytes(last_value), last_seen)
    msgs = (
        Incoming(
            r.source_file,
            bytes(r.value),
            r.seed == "1",
            r.produced_at.to_pydatetime(),
        )
        for r in batch.itertuples(index=False)
    )
    rows, new = diff_batch(feed, kind, msgs, saved)
    if new.last_file is not None:
        state.update((new.last_file, new.last_value, new.last_seen))
    if rows.num_rows:
        yield rows.to_pandas(types_mapper=_pandas_type)


def _pandas_type(arrow_type):
    """Nullable pandas types, so an int column with nulls stays an int
    (the default turns it into float64, which Spark then rejects)."""
    import pandas as pd
    import pyarrow as pa

    return {
        pa.int32(): pd.Int32Dtype(),
        pa.int64(): pd.Int64Dtype(),
        pa.bool_(): pd.BooleanDtype(),
        pa.float32(): pd.Float32Dtype(),
        pa.float64(): pd.Float64Dtype(),
        pa.string(): pd.StringDtype(),
        pa.large_string(): pd.StringDtype(),
    }.get(arrow_type)


class Sink:
    """foreachBatch: write each table, then log what landed when."""

    def __init__(self, spark, cfg: StreamConfig) -> None:
        self.spark, self.cfg = spark, cfg
        cfg.metrics.mkdir(parents=True, exist_ok=True)
        self.done = self._committed_batches()
        self.compacted: datetime | None = None  # last data hour compacted

    def _committed_batches(self) -> set[int]:
        path = self.cfg.metrics / "batches.jsonl"
        if not path.exists():
            return set()
        with path.open() as fh:
            return {json.loads(line)["batch_id"] for line in fh if line}

    def __call__(self, df, batch_id: int) -> None:
        from pyspark.sql import functions as F  # noqa: N812

        if batch_id in self.done:
            # A batch replayed after a restart whose writes all landed.
            # (A crash between two tables' writes can still duplicate the
            # first table's rows: one Iceberg commit per table.)
            log.info("batch %d already committed, skipped", batch_id)
            return
        started = time.monotonic()
        df = df.persist()
        counts = {
            r[TABLE_COL]: r["count"]
            for r in df.groupBy(TABLE_COL).count().collect()
        }
        stamp = datetime.now(UTC)
        for name, cols in TABLES.items():
            if not counts.get(name):
                continue
            part = df.filter(F.col(TABLE_COL) == name)
            if name == "bronze.snapshot":
                part = part.withColumn("ingested_at", F.lit(stamp))
            (
                part.select(*cols)
                .sortWithinPartitions(*SORT_KEYS[name])
                .writeTo(f"{CATALOG}.{name}")
                .append()
            )
        committed = datetime.now(UTC)
        snaps = (
            df.filter(F.col(TABLE_COL) == "bronze.snapshot")
            .select("feed", "source_file", "fetched_at", PRODUCED_COL)
            .collect()
        )
        df.unpersist()
        with (self.cfg.metrics / "snapshots.jsonl").open("a") as fh:
            for r in snaps:
                fh.write(
                    json.dumps(
                        {
                            "batch_id": batch_id,
                            "feed": r["feed"],
                            "source_file": r["source_file"],
                            "fetched_at": _iso(r["fetched_at"]),
                            "produced_at": _iso(r[PRODUCED_COL]),
                            "ingested_at": _iso(stamp),
                            "committed_at": _iso(committed),
                        }
                    )
                    + "\n"
                )
        with (self.cfg.metrics / "batches.jsonl").open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "batch_id": batch_id,
                        "committed_at": _iso(committed),
                        "seconds": round(time.monotonic() - started, 3),
                        "rows": counts,
                    }
                )
                + "\n"
            )
        self.done.add(batch_id)
        newest = max(
            (_utc(r["fetched_at"]) for r in snaps if r["fetched_at"]),
            default=None,
        )
        if self.cfg.maintain and newest is not None:
            self.maintain(newest)

    def maintain(self, newest: datetime) -> None:
        """Compact each data hour once it is closed, then expire.

        As the worker's ``run --loop`` does (D-032): an hour is closed
        when a later fetch time has been committed, which in a paced run
        is the wall-clock hour and in a flat-out run comes round faster.
        Only that hour's files are rewritten (``where`` on
        ``fetched_at``), never the whole ``(obs_date, mode)`` partition.
        Snapshots older than 24 h by the wall clock are expired, as the
        worker's ``EXPIRE_AFTER``."""
        current = newest.replace(minute=0, second=0, microsecond=0)
        if self.compacted is None:
            # The first hour seen is closed once the next one starts.
            self.compacted = current - timedelta(hours=1)
            return
        todo = []
        hour = self.compacted + timedelta(hours=1)
        while hour < current:
            todo.append(hour)
            hour += timedelta(hours=1)
        if not todo:
            return
        started = time.monotonic()
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        for hour in todo:
            lo, hi = hour, hour + timedelta(hours=1)
            where = (
                f"fetched_at >= TIMESTAMP '{lo:%Y-%m-%d %H:%M:%S}' AND "
                f"fetched_at < TIMESTAMP '{hi:%Y-%m-%d %H:%M:%S}'"
            )
            for name in TABLES:
                self.spark.sql(
                    f"CALL {CATALOG}.system.rewrite_data_files("
                    f"table => '{CATALOG}.{name}', where => \"{where}\", "
                    "options => map('min-input-files', '4'))"
                ).collect()
        for name in TABLES:
            self.spark.sql(
                f"CALL {CATALOG}.system.expire_snapshots("
                f"table => '{CATALOG}.{name}', "
                f"older_than => TIMESTAMP '{cutoff:%Y-%m-%d %H:%M:%S}', "
                "retain_last => 1)"
            ).collect()
        self.compacted = todo[-1]
        with (self.cfg.metrics / "maintenance.jsonl").open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "at": _iso(datetime.now(UTC)),
                        "hours": [_iso(h) for h in todo],
                        "seconds": round(time.monotonic() - started, 3),
                    }
                )
                + "\n"
            )


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:  # Spark hands back naive UTC (session tz)
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def build_query(spark, cfg: StreamConfig):
    from pyspark.sql import functions as F  # noqa: N812
    from pyspark.sql.streaming.state import GroupStateTimeout

    def header(name: str):
        return F.expr(
            f"CAST(filter(headers, h -> h.key = '{name}')[0].value AS STRING)"
        )

    raw = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", cfg.bootstrap)
        .option("subscribe", ",".join(topic_for(k) for k in KINDS))
        .option("startingOffsets", "earliest")
        .option("includeHeaders", "true")
        .option("maxOffsetsPerTrigger", cfg.max_offsets)
        .option("failOnDataLoss", "true")
        .option("kafka.max.partition.fetch.bytes", MAX_MESSAGE_BYTES)
        .option("kafka.fetch.max.bytes", 2 * MAX_MESSAGE_BYTES)
        .load()
        .select(
            F.col("key").cast("string").alias("feed"),
            "topic",
            "partition",
            "offset",
            F.col("timestamp").alias("produced_at"),
            header("source_file").alias("source_file"),
            header("seed").alias("seed"),
            "value",
        )
    )
    rows = raw.groupBy("feed").applyInPandasWithState(
        _group_step,
        outputStructType=output_ddl(),
        stateStructType=STATE_DDL,
        outputMode="append",
        timeoutConf=GroupStateTimeout.NoTimeout,
    )
    return (
        rows.writeStream.foreachBatch(Sink(spark, cfg))
        .option("checkpointLocation", str(cfg.checkpoint.resolve()))
        .trigger(processingTime=f"{cfg.trigger_s} seconds")
        .start()
    )


def run(cfg: StreamConfig, idle_stop_s: float | None = None) -> None:
    """Run until stopped, or until no data arrives for ``idle_stop_s``."""
    spark = session(cfg)
    spark.sparkContext.setLogLevel("WARN")
    query = build_query(spark, cfg)
    last_busy = time.monotonic()
    seen = None
    try:
        while query.isActive:
            query.awaitTermination(10)
            progress = query.lastProgress
            if progress and progress["batchId"] != seen:
                seen = progress["batchId"]
                log.info(
                    "batch %s: %s messages in %s ms",
                    progress["batchId"],
                    progress["numInputRows"],
                    progress["durationMs"].get("triggerExecution"),
                )
            status = query.status
            if status["isTriggerActive"] or status["isDataAvailable"]:
                last_busy = time.monotonic()
            if (
                idle_stop_s is not None
                and time.monotonic() - last_busy > idle_stop_s
            ):
                log.info("no new messages for %.0f s: stopping", idle_stop_s)
                break
    finally:
        query.stop()
        spark.stop()
    if query.exception():
        raise RuntimeError(str(query.exception()))
