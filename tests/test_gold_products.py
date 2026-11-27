"""Phase 3 products (D-045): ghost trips, route honesty, bunching, the
hand-check sheet and the map replay export, on one bus route.

Route R1 (operator A1, with realtime) has four stops ten minutes apart:

- B1 08:00 runs 8 min late, so B2 08:10 (on time) follows it two
  minutes behind at every stop: one bunch. B3 08:30 is on time.
- G1 08:20 never appears: a ghost with nothing near its first stop.
- G2 08:45 never appears, but a vehicle without a trip sits at its
  first stop at 08:45: the hand-check reads it as untracked.
- V1 08:50 appears, then leaves the feed with three stops to go.
- Q1 belongs to A2, which publishes no realtime: never a ghost.

Two more routes on the same stops test pairing (D-051):

- R3: O1 08:00 runs 11 min 40 s late and O2 08:10 (on time) overtakes
  it, reaching every stop 100 s ahead: bunched although the timetabled
  gap between them is negative.
- R4: Y1 08:00 runs 5 min late and arrives in the same second as X1
  08:05: the pair is ordered by the timetable, not by trip id.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from google.transit import gtfs_realtime_pb2

from transit_lakehouse.collector.config import Feed
from transit_lakehouse.collector.landing import Lander
from transit_lakehouse.gold import handcheck, publish, replay
from transit_lakehouse.gold.__main__ import build, gold_paths
from transit_lakehouse.worker.runner import run_once
from transit_lakehouse.worker.tables import open_catalog
from transit_lakehouse.worker.timetable import run_timetable

TU_FEED = Feed("buses_tripupdates", "trip_updates", "https://x/tu", 30)
VP_FEED = Feed("buses_vehiclepos", "vehicle_positions", "https://x/vp", 15)
# Wednesday 18 Nov 2026; local midnight is 13:00 UTC on 17 Nov.
ORIGIN = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
DAY = date(2026, 11, 18)
STOPS = {f"S{i}": (-33.87, 151.20 + 0.01 * i) for i in range(1, 5)}
# trip: (route, first departure, delay in the feed)
TRIPS = {
    "B1": ("R1", "08:00", 480),
    "B2": ("R1", "08:10", 0),
    "B3": ("R1", "08:30", 0),
    "G1": ("R1", "08:20", None),
    "G2": ("R1", "08:45", None),
    "V1": ("R1", "08:50", 0),
    "Q1": ("R2", "08:05", None),
    "O1": ("R3", "08:00", 700),
    "O2": ("R3", "08:10", 0),
    "Y1": ("R4", "08:00", 300),
    "X1": ("R4", "08:05", 0),
}
V1_GONE = "08:52"  # V1 leaves the feed here, with S2 to S4 to go


def at(hm: str, plus_min: int = 0) -> datetime:
    h, m = (int(x) for x in hm.split(":"))
    return ORIGIN + timedelta(hours=h, minutes=m + plus_min)


def calls(trip: str) -> list[tuple[int, str, datetime]]:
    first = TRIPS[trip][1]
    return [(i, f"S{i}", at(first, 10 * (i - 1))) for i in range(1, 5)]


def gtfs() -> bytes:
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
        "A1,Test Buses,https://x,Australia/Sydney\n"
        "A2,Quiet Buses,https://x,Australia/Sydney\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_desc,"
        "route_type\n"
        "R1,A1,R1,Sydney Buses Network,700\n"
        "R2,A2,R2,Sydney Buses Network,700\n"
        "R3,A1,R3,Sydney Buses Network,700\n"
        "R4,A1,R4,Sydney Buses Network,700\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(
            f"{s},Stop {s},{lat},{lon}\n" for s, (lat, lon) in STOPS.items()
        ),
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,"
        "friday,saturday,sunday,start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20261101,20261231\n",
        "trips.txt": "route_id,service_id,trip_id,direction_id,"
        "trip_headsign\n"
        + "".join(f"{r},WK,{t},0,Town\n" for t, (r, _, _) in TRIPS.items()),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,"
        "stop_sequence\n"
        + "".join(
            _stop_time_line(t, i, s, when)
            for t in TRIPS
            for i, s, when in calls(t)
        ),
        "feed_info.txt": "feed_publisher_name,feed_version,feed_start_date,"
        "feed_end_date\nTfNSW,v1,20261117,20261231\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _stop_time_line(trip: str, seq: int, stop: str, when: datetime) -> str:
    secs = int((when - ORIGIN).total_seconds())
    hms = f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
    return f"{trip},{hms},{hms},{stop},{seq}\n"


def live(trip: str, t: datetime) -> list[tuple[int, str, datetime]]:
    """The trip's stops not yet passed at ``t``, if it is in the feed."""
    delay = TRIPS[trip][2]
    if delay is None:
        return []
    if trip == "V1" and t >= at(V1_GONE):
        return []
    shifted = [(i, s, w + timedelta(seconds=delay)) for i, s, w in calls(trip)]
    first = shifted[0][2]
    if t < first - timedelta(minutes=5):
        return []  # not in the feed yet
    return [c for c in shifted if t < c[2] + timedelta(seconds=15)]


def tu_snapshot(t: datetime) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for trip in TRIPS:
        left = live(trip, t)
        if not left:
            continue
        ent = msg.entity.add()
        ent.id = trip
        tu = ent.trip_update
        tu.trip.trip_id = trip
        tu.trip.start_date = "20261118"
        for i, stop, when in left:
            stu = tu.stop_time_update.add()
            stu.stop_sequence = i
            stu.stop_id = stop
            stu.arrival.delay = TRIPS[trip][2]
            stu.arrival.time = int(when.timestamp())
    return msg.SerializeToString()


def position_of(trip: str, t: datetime) -> tuple[float, float] | None:
    left = live(trip, t)
    if not left:
        return None
    _, stop, _ = left[0]
    return STOPS[stop]


def vp_snapshot(t: datetime) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = int(t.timestamp())
    for n, trip in enumerate(("B1", "B2", "B3", "V1"), start=1):
        pos = position_of(trip, t)
        if pos is None:
            continue
        ent = msg.entity.add()
        ent.id = f"v{n}"
        v = ent.vehicle
        v.vehicle.id = f"bus{n}"
        v.trip.trip_id = trip
        v.trip.start_date = "20261118"
        v.position.latitude, v.position.longitude = pos
        v.timestamp = int(t.timestamp())
    if at("08:43") <= t <= at("08:47"):  # no trip logged on, at S1
        ent = msg.entity.add()
        ent.id = "v9"
        v = ent.vehicle
        v.vehicle.id = "bus9"
        v.position.latitude, v.position.longitude = STOPS["S1"]
        v.position.longitude += (t - at("08:43")).seconds * 1e-6
        v.timestamp = int(t.timestamp())
    return msg.SerializeToString()


def build_products(data_dir: Path) -> Path:
    """Land the route's snapshots, run the worker and build gold."""
    lander = Lander(data_dir)
    lander.land(
        "static_gtfs",
        datetime(2026, 11, 17, 7, 45, tzinfo=UTC),
        gtfs(),
        compress=False,
    )
    t = datetime(2026, 11, 17, 20, 45, tzinfo=UTC)
    while t <= datetime(2026, 11, 17, 22, 50, tzinfo=UTC):
        lander.land(TU_FEED.name, t, tu_snapshot(t))
        lander.land(VP_FEED.name, t, vp_snapshot(t))
        t += timedelta(seconds=60)
    catalog = open_catalog(data_dir, env={})
    run_timetable(data_dir, catalog)
    run_once(data_dir, catalog, [TU_FEED, VP_FEED])
    assert build(data_dir, None, {"outcome_grace_minutes": 20}, None) == 0
    return data_dir


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory) -> Path:
    return build_products(tmp_path_factory.mktemp("gold_products") / "data")


@pytest.fixture(scope="module")
def db(data_dir: Path) -> Path:
    return gold_paths(data_dir)["db"]


def query(db: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(db)) as con:
        return con.execute(sql).fetchall()


def test_ghost_trips_are_missing_or_vanished(db: Path) -> None:
    got = query(
        db,
        "select trip_id, ghost_kind, local_hour, day_type, stops_done"
        " from gold.fct_ghost_trip order by trip_id",
    )
    # Q1's operator has no realtime (D-038): not a ghost.
    assert got == [
        ("G1", "missing", 8, "weekday", None),
        ("G2", "missing", 8, "weekday", None),
        ("V1", "vanished", 8, "weekday", 1),
    ]


def test_route_honesty_counts_only_judged_trips(db: Path) -> None:
    got = query(
        db,
        "select route_id, trips_scheduled, trips_judged, trips_ran,"
        " trips_missing, trips_vanished, ran_share, ghost_share"
        " from gold.mart_route_honesty where service_date = '2026-11-18'"
        " and route_id in ('R1', 'R2') order by route_id",
    )
    assert got == [
        ("R1", 6, 6, 3, 2, 1, 0.5, 0.5),
        ("R2", 1, 0, 0, 0, 0, None, None),
    ]


def test_a_pair_bunched_at_every_stop_is_one_bunch(db: Path) -> None:
    got = query(
        db,
        "select lead_trip_id, follow_trip_id, stops_paired,"
        " stops_bunched, min_gap_s, scheduled_gap_s, local_hour"
        " from gold.fct_bunching_event where route_id = 'R1'",
    )
    assert got == [("B1", "B2", 4, 4, 120, 600, 8)]


def test_an_overtake_is_bunched_and_stays_one_pair(db: Path) -> None:
    # O2 reaches every stop 100 s before O1, which was timetabled 10 min
    # earlier: the gap is a third of the timetabled 600 s or less, so it
    # is bunched, and the pair keeps its timetable order (D-051).
    got = query(
        db,
        "select prev_trip_id, trip_id, observed_headway_s,"
        " scheduled_headway_s, overtaken, bunched from gold.fct_headway"
        " where route_id = 'R3' order by stop_sequence",
    )
    assert got == [("O2", "O1", 100, -600, True, True)] * 4
    events = query(
        db,
        "select lead_trip_id, follow_trip_id, stops_paired,"
        " stops_bunched, stops_overtaken, scheduled_gap_s"
        " from gold.fct_bunching_event where route_id = 'R3'",
    )
    assert events == [("O1", "O2", 4, 4, 4, 600)]


def test_a_same_second_tie_follows_the_timetable(db: Path) -> None:
    # Y1 (08:00, 5 min late) and X1 (08:05) reach each stop together.
    # X1 sorts first by trip id; the timetable puts Y1 first (D-051).
    got = query(
        db,
        "select prev_trip_id, trip_id, observed_headway_s,"
        " scheduled_headway_s, overtaken, bunched from gold.fct_headway"
        " where route_id = 'R4' order by stop_sequence",
    )
    assert got == [("Y1", "X1", 0, 300, False, True)] * 4


def test_hotspots_carry_the_stop_and_its_share(db: Path) -> None:
    got = query(
        db,
        "select stop_id, stop_name, headways, bunched, bunched_share,"
        " stop_lat is not null from gold.mart_bunching_hotspot"
        " where stop_id = 'S1' and route_id = 'R1' order by local_hour",
    )
    # At S1: B1->B2 is bunched (2 of 10 min); B2->B3 and B3->V1 are
    # not (20 of 20 min).
    assert got == [("S1", "Stop S1", 3, 1, 0.3333, True)]


def test_handcheck_reads_each_trip(data_dir: Path, db: Path) -> None:
    with duckdb.connect(str(db)) as con:
        trips = handcheck.sample(con, DAY, n=20)
        evidence = handcheck.gather(con, data_dir, trips)
    by_trip = {e.trip_id: e for e in evidence}
    assert set(by_trip) == {"G1", "G2", "V1"}
    # missing trips take the first turn
    assert evidence[0].ghost_kind == "missing"
    g1 = by_trip["G1"]
    assert g1.raw_files_scanned > 0
    assert g1.raw_files_with_trip == 0
    assert g1.reading == "consistent_ghost"
    assert by_trip["G2"].untracked_nearby == 1
    assert by_trip["G2"].reading == "untracked_vehicle"
    v1 = by_trip["V1"]
    assert v1.raw_files_with_trip > 0
    assert v1.raw_after_vanish == 0
    assert v1.reading == "consistent_vanish"
    sheet = handcheck.to_markdown(evidence, DAY)
    assert sheet.count("\n| ") == 4  # header row + 3 trips
    assert "Readings: consistent_ghost 1" in sheet


def test_handcheck_sample_is_fixed_by_its_seed(db: Path) -> None:
    with duckdb.connect(str(db)) as con:
        a = [t["trip_id"] for t in handcheck.sample(con, DAY, 2, "s1")]
        b = [t["trip_id"] for t in handcheck.sample(con, DAY, 2, "s1")]
    assert a == b
    assert len(a) == 2


def test_raw_mention_of_a_missing_trip_is_a_pipeline_miss() -> None:
    ev = handcheck.Evidence(
        trip_id="X",
        start_date="20261118",
        service_date="2026-11-18",
        mode="bus",
        route="R1",
        agency="A",
        headsign="",
        ghost_kind="missing",
        confidence="high",
        sched_start_local="08:00",
        sched_start_utc=at("08:00"),
        sched_end_utc=at("08:30"),
        vanished_at=None,
        stop_count=4,
        stops_done=None,
        route_trips_seen=3,
        raw_files_scanned=10,
        raw_files_with_trip=2,
    )
    assert handcheck.reading(ev)[0] == "pipeline_miss"
    ev.raw_files_scanned = 0
    assert handcheck.reading(ev)[0] == "no_raw"


def test_replay_writes_hours_tracks_and_bunches(
    db: Path, tmp_path: Path
) -> None:
    out = tmp_path / "replay"
    # Not through main(): it opens the file read-only, which DuckDB
    # refuses while dbt's connection is open in this process.
    with duckdb.connect(str(db)) as con:
        replay.write_index(out, replay.export_day(con, DAY, out, 60))
    index = json.loads((out / "index.json").read_text())
    [entry] = index["days"]
    assert entry["day"] == "2026-11-18"
    assert [h["hour"] for h in entry["hours"]] == [8, 9]
    hour8 = json.loads((out / "2026-11-18" / "08.json").read_text())
    assert hour8["t0"] == int(at("08:00").timestamp())
    tracks = {(v["v"], v["t"]): v for v in hour8["vehicles"]}
    assert ("bus9", "") in tracks  # the untracked vehicle is kept
    b2 = tracks[("bus2", "B2")]
    assert b2["r"] == "R1"
    assert b2["h"] == "Town"
    secs = [p[2] for p in b2["p"]]
    assert secs == sorted(secs)
    assert all(0 <= s < 3600 for s in secs)
    bunches = json.loads((out / "2026-11-18" / "bunches.json").read_text())
    assert sorted(
        (b["lead_trip_id"], b["follow_trip_id"]) for b in bunches
    ) == [("B1", "B2"), ("O1", "O2"), ("Y1", "X1")]


def test_publish_writes_only_the_public_tables(
    db: Path, tmp_path: Path
) -> None:
    out = tmp_path / "public"
    with duckdb.connect(str(db)) as con:
        rows = publish.publish(con, out)
    assert sorted(p.name for p in out.iterdir()) == sorted(
        f"{name}.parquet" for name in publish.TABLES
    )
    assert rows["ghost_trips"] == 3
    assert rows["bunching_events"] == 3
    assert rows["quality"] > 0
    # Parquet any DuckDB reads, whatever the gold file's storage version
    [(kinds,)] = duckdb.sql(
        f"select list(distinct ghost_kind order by ghost_kind)"
        f" from read_parquet('{out / 'ghost_trips.parquet'}')"
    ).fetchall()
    assert kinds == ["missing", "vanished"]


def test_publish_window_drops_edge_days(db: Path, tmp_path: Path) -> None:
    # A build's first and last days are only partly captured; the site
    # publishes complete days only. Undated tables are kept whole.
    out = tmp_path / "public"
    with duckdb.connect(str(db)) as con:
        inside = publish.publish(
            con, out, date(2026, 11, 18), date(2026, 11, 18)
        )
        after = publish.publish(con, tmp_path / "after", date(2026, 11, 19))
    assert inside["ghost_trips"] == 3
    assert inside["service_days"] == 1
    assert after["ghost_trips"] == 0
    assert after["service_days"] == 0
    assert after["bunching_events"] == 0
    assert after["quality"] == inside["quality"] > 0
    assert after["bunching_hotspots"] == inside["bunching_hotspots"]


def test_bunching_by_hour_keeps_every_gap(db: Path, tmp_path: Path) -> None:
    # The hot-spot map drops stop-hours with fewer than 3 gaps; the
    # by-hour chart must not, or quiet routes vanish from it (D-051).
    # R3 and R4 have one gap per stop, so only R1 is on the map.
    out = tmp_path / "public"
    with duckdb.connect(str(db)) as con:
        publish.publish(con, out)
        [(all_gaps, all_bunched)] = con.execute(
            "select count(*), count(*) filter (where bunched)"
            " from gold.fct_headway where scheduled_headway_s <> 0"
        ).fetchall()
    [(gaps, bunched)] = duckdb.sql(
        "select sum(headways), sum(bunched)"
        f" from read_parquet('{out / 'bunching_by_hour.parquet'}')"
    ).fetchall()
    assert (gaps, bunched) == (all_gaps, all_bunched)
    [(on_map,)] = duckdb.sql(
        "select list(distinct route_id order by route_id)"
        f" from read_parquet('{out / 'bunching_hotspots.parquet'}')"
    ).fetchall()
    assert on_map == ["R1"]


def test_replay_hours_follow_daylight_saving() -> None:
    con = duckdb.connect()
    # Sydney leaves daylight saving on 5 Apr 2026 (25 hours) and
    # starts it on 4 Oct 2026 (23 hours).
    assert len(replay.hour_bounds(con, date(2026, 4, 5))) == 25
    assert len(replay.hour_bounds(con, date(2026, 10, 4))) == 23
    assert len(replay.hour_bounds(con, DAY)) == 24


if __name__ == "__main__":
    # CI builds the dashboard against this synthetic route, to check the
    # pages compile; it is never published (dashboard/README.md).
    import sys

    print(build_products(Path(sys.argv[1])))
