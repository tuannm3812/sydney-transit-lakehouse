"""Redpanda + Spark Structured Streaming benchmark (D-023, D-041).

Replays offloaded raw hours into Redpanda and builds the same bronze and
silver tables with Spark into a separate lakehouse, so ``worker verify``
can prove the rows match and the report can compare latency, cost and
ops load with the light worker. On demand only: never on the collector
host (docs/13_spark_benchmark.md).
"""
