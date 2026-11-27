"""Gold layer CLI: refresh the silver views, run dbt, record results.

    python -m transit_lakehouse.gold build             # views + dbt build
    python -m transit_lakehouse.gold build --since 2026-11-17 \\
        --vars '{"outcome_grace_minutes": 20}'
    python -m transit_lakehouse.gold views             # views only
    python -m transit_lakehouse.gold dq                # last run's results
    python -m transit_lakehouse.gold handcheck --service-date 2026-11-24
    python -m transit_lakehouse.gold publish           # dashboard extract
    python -m transit_lakehouse.gold replay --day 2026-11-24 \\
        --out dashboard/static/replay-map/data

Reads the lakehouse under ``DATA_DIR`` (as the worker writes it) and
writes ``DATA_DIR/gold/transit.duckdb``. dbt's target and logs go under
``DATA_DIR/gold/`` too, so the package folder stays read-only. Needs no
TfNSW key and no network.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

import duckdb

from transit_lakehouse.gold import dq, handcheck, publish, replay
from transit_lakehouse.gold.views import refresh_views
from transit_lakehouse.worker.tables import open_catalog

PROJECT_DIR = Path(__file__).with_name("dbt")
log = logging.getLogger("transit_lakehouse.gold")


def gold_paths(data_dir: Path) -> dict[str, Path]:
    # Absolute, because dbt resolves a relative target path against the
    # project folder, not the working directory.
    root = data_dir.resolve() / "gold"
    return {
        "root": root,
        "db": root / "transit.duckdb",
        "target": root / "target",
        "logs": root / "logs",
    }


def build_views(data_dir: Path, since: date | None) -> list:
    paths = gold_paths(data_dir)
    paths["root"].mkdir(parents=True, exist_ok=True)
    catalog = open_catalog(data_dir)
    with duckdb.connect(str(paths["db"])) as con:
        return refresh_views(con, catalog, since)


def dbt_args(
    command: str,
    data_dir: Path,
    variables: dict,
    select: list[str] | None = None,
) -> list[str]:
    paths = gold_paths(data_dir)
    args = [
        command,
        "--project-dir",
        str(PROJECT_DIR),
        "--profiles-dir",
        str(PROJECT_DIR),
        "--target-path",
        str(paths["target"]),
        "--log-path",
        str(paths["logs"]),
    ]
    if variables:
        args += ["--vars", json.dumps(variables)]
    if select:
        args += ["--select", *select]
    return args


def run_dbt(args: list[str], db: Path) -> bool:
    from dbt.cli.main import dbtRunner  # deferred: heavy import

    os.environ["GOLD_DUCKDB_PATH"] = str(db)
    res = dbtRunner().invoke(args)
    return bool(res.success)


def build(
    data_dir: Path,
    since: date | None,
    variables: dict,
    select: list[str] | None,
) -> int:
    paths = gold_paths(data_dir)
    views = build_views(data_dir, since)
    for v in views:
        log.info("view %s over %d files", v.name, v.files)
    ok = run_dbt(dbt_args("build", data_dir, variables, select), paths["db"])
    results = paths["target"] / "run_results.json"
    if results.exists():
        window = (
            variables.get("service_date_from"),
            variables.get("service_date_to"),
        )
        with duckdb.connect(str(paths["db"])) as con:
            n = dq.record(con, results, window)
        log.info("recorded %d results in %s", n, dq.DQ_TABLE)
    return 0 if ok else 1


def last_results(data_dir: Path) -> str:
    db = gold_paths(data_dir)["db"]
    if not db.exists():
        return "no gold database yet"
    with duckdb.connect(str(db), read_only=True) as con:
        rows = con.execute(
            f"SELECT resource_type, node, status, failures FROM {dq.DQ_TABLE}"  # noqa: S608
            f" WHERE run_id = (SELECT arg_max(run_id, run_at)"
            f" FROM {dq.DQ_TABLE}) ORDER BY resource_type, node"
        ).fetchall()
    lines = []
    for kind, node, status, fails in rows:
        shown = "" if fails is None else fails
        lines.append(f"{kind:<6} {status:<5} {shown:>8}  {node}")
    return "\n".join(lines)


def run_handcheck(args: argparse.Namespace) -> int:
    """Write the hand-check sheet for one service day (D-045)."""
    db = gold_paths(args.data_dir)["db"]
    with duckdb.connect(str(db), read_only=True) as con:
        trips = handcheck.sample(con, args.service_date, args.n, args.seed)
        evidence = handcheck.gather(con, args.data_dir, trips)
    if not evidence:
        log.warning("no ghost trips on %s", args.service_date)
        return 1
    out = args.out or (
        args.data_dir / "gold" / f"handcheck-{args.service_date}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(handcheck.to_markdown(evidence, args.service_date))
    out.with_suffix(".json").write_text(handcheck.to_json(evidence))
    print(f"{len(evidence)} trips -> {out}")
    return 0


def run_publish(args: argparse.Namespace) -> int:
    """Write the dashboard's public Parquet extract (D-045)."""
    db = gold_paths(args.data_dir)["db"]
    out = args.out or gold_paths(args.data_dir)["root"] / "public"
    with duckdb.connect(str(db), read_only=True) as con:
        rows = publish.publish(con, out, args.date_from, args.date_to)
        for name, n in rows.items():
            print(f"{name}: {n} rows")
    return 0


def run_replay(args: argparse.Namespace) -> int:
    """Export map replay files for each Sydney day asked (D-045)."""
    db = gold_paths(args.data_dir)["db"]
    args.out.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db), read_only=True) as con:
        for day in args.day:
            entry = replay.export_day(
                con, day, args.out, args.step, args.mode or None
            )
            replay.write_index(args.out, entry)
            print(
                f"{day}: {len(entry['hours'])} hours,"
                f" {entry['bunches']} bunches -> {args.out}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transit_lakehouse.gold")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "data")),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("build", "views"):
        p = sub.add_parser(name)
        p.add_argument(
            "--since",
            type=date.fromisoformat,
            help="only read silver files from this UTC poll date on",
        )
    b = sub.choices["build"]
    b.add_argument("--vars", default="{}", help="dbt vars, as JSON")
    b.add_argument("--select", nargs="+", help="dbt node selection")
    sub.add_parser("dq", help="results of the last build")
    h = sub.add_parser("handcheck", help="ghost trip hand-check sheet")
    h.add_argument("--service-date", type=date.fromisoformat, required=True)
    h.add_argument("-n", type=int, default=20)
    h.add_argument("--seed", default="phase3")
    h.add_argument("--out", type=Path)
    pub = sub.add_parser("publish", help="the dashboard's public extract")
    pub.add_argument("--out", type=Path, help="default DATA_DIR/gold/public")
    pub.add_argument(
        "--from",
        dest="date_from",
        type=date.fromisoformat,
        help="first service day to publish (dated tables)",
    )
    pub.add_argument(
        "--to",
        dest="date_to",
        type=date.fromisoformat,
        help="last service day to publish (dated tables)",
    )
    r = sub.add_parser("replay", help="map replay files for the dashboard")
    r.add_argument(
        "--day",
        type=date.fromisoformat,
        nargs="+",
        required=True,
        help="Sydney local dates",
    )
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--step", type=int, default=30, help="seconds")
    r.add_argument("--mode", action="append", help="repeat; default all")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.cmd == "views":
        for v in build_views(args.data_dir, args.since):
            print(f"{v.name}: {v.files} files, {v.columns} columns")
        return 0
    if args.cmd == "dq":
        print(last_results(args.data_dir))
        return 0
    if args.cmd == "handcheck":
        return run_handcheck(args)
    if args.cmd == "publish":
        return run_publish(args)
    if args.cmd == "replay":
        return run_replay(args)
    return build(args.data_dir, args.since, json.loads(args.vars), args.select)


if __name__ == "__main__":
    sys.exit(main())
