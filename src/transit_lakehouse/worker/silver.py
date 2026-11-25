"""Turn decoded snapshots into bronze and silver rows (D-027).

Each realtime poll is a full picture of the network, and most of it
repeats the poll before (docs/reports/profile-20261118.md). Silver keeps
only what changed:

- ``stop_prediction``: a row when a stop's predicted arrival, departure
  or stop status differs from the same stop in the previous snapshot of
  the feed, or the stop is new.
- ``trip_status``: a row when a trip's status, route, vehicle or
  trip-level delay differs from the previous snapshot, or it is new,
  and a ``REMOVED`` row (stop count 0) when a trip in the previous
  snapshot is missing from this one (D-036). A snapshot that failed to
  decode writes no ``REMOVED`` rows, so a bad file never ends every
  trip at once, and a file that failed as a whole (unreadable or a bad
  envelope) leaves the previous snapshot as the baseline (D-049).
- ``vehicle_position``: a row when a vehicle's own timestamp differs
  from the previous snapshot (two polls of the same report are one
  event), or the vehicle is new. Implausible readings are kept but
  flagged ``quarantined`` with a reason.

"Previous snapshot" is always the file landed just before, never state
accumulated over a run. That makes every row a pure function of two
adjacent raw files, so a replay over any batch boundaries rebuilds the
same rows (the Phase 1 gate).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from google.transit import gtfs_realtime_pb2

from transit_lakehouse.worker.decode import Snapshot

# Feed name prefix → mode. Names come from config/feeds.toml.
MODES = (
    ("buses_", "bus"),
    ("sydneytrains_", "train"),
    ("metro_", "metro"),
    ("lightrail_", "lightrail"),
    ("ferries_", "ferry"),
    ("nswtrains_", "nswtrains"),
    ("service_alerts", "all"),  # one statewide feed for every mode
)

# Quarantine thresholds. Nothing in Sydney's fleet runs at 150 km/h
# (Sydney Trains tops out near 115 km/h), and the box is NSW plus a
# margin. A report more than 10 minutes in the future of its own poll
# is a clock fault.
MAX_SPEED_MPS = 150 / 3.6
NSW_BOX = (-38.0, -28.0, 140.5, 154.0)  # min lat, max lat, min lon, max lon
MAX_FUTURE_S = 600

TU = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate
# trip_status of the row written when a trip leaves the feed (D-036);
# not a GTFS-realtime status, so it cannot collide with one.
REMOVED = "REMOVED"


def mode_of(feed: str) -> str:
    for prefix, mode in MODES:
        if feed.startswith(prefix):
            return mode
    return feed.split("_", 1)[0]


def _ts(epoch: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, UTC) if epoch else None


class Columns:
    """Column-wise row buffer: one list per column, appended in order."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self.data: dict[str, list] = {n: [] for n in names}

    def add(self, *values) -> None:
        for name, value in zip(self.names, values, strict=True):
            self.data[name].append(value)

    def __len__(self) -> int:
        return len(self.data[self.names[0]])

    def clear(self) -> None:
        for values in self.data.values():
            values.clear()


SNAPSHOT_COLUMNS = (
    "feed",
    "mode",
    "kind",
    "source_file",
    "sha12",
    "fetched_at",
    "header_ts",
    "obs_date",
    "stored_bytes",
    "entities",
    "entity_errors",
    "entities_repaired",
    "decode_error",
    "rows_emitted",
    "ingested_at",
)

STOP_COLUMNS = (
    "mode",
    "feed",
    "trip_id",
    "start_date",
    "route_id",
    "direction_id",
    "stop_sequence",
    "stop_id",
    "arrival_time",
    "arrival_delay_s",
    "departure_time",
    "departure_delay_s",
    "stop_status",
    "trip_delay_s",
    "observed_at",
    "fetched_at",
    "obs_date",
    "source_file",
)

TRIP_COLUMNS = (
    "mode",
    "feed",
    "trip_id",
    "start_date",
    "start_time",
    "route_id",
    "direction_id",
    "trip_status",
    "vehicle_id",
    "delay_s",
    "current_delay_s",
    "stop_count",
    "observed_at",
    "fetched_at",
    "obs_date",
    "source_file",
)

ALERT_COLUMNS = (
    "mode",
    "feed",
    "alert_id",
    "event",
    "content_hash",
    "cause",
    "effect",
    "severity_level",
    "header_text",
    "description_text",
    "url",
    "active_start",
    "active_end",
    "active_periods",
    "observed_at",
    "fetched_at",
    "obs_date",
    "source_file",
)

ALERT_SCOPE_COLUMNS = (
    "mode",
    "feed",
    "alert_id",
    "content_hash",
    "agency_id",
    "route_id",
    "route_type",
    "stop_id",
    "trip_id",
    "start_date",
    "direction_id",
    "fetched_at",
    "obs_date",
    "source_file",
)

VEHICLE_COLUMNS = (
    "mode",
    "feed",
    "vehicle_id",
    "vehicle_label",
    "trip_id",
    "start_date",
    "route_id",
    "direction_id",
    "latitude",
    "longitude",
    "bearing",
    "speed_mps",
    "occupancy_status",
    "current_stop_sequence",
    "stop_id",
    "current_status",
    "event_ts",
    "observed_at",
    "fetched_at",
    "obs_date",
    "quarantined",
    "quarantine_reason",
    "source_file",
)


def _text(value) -> str | None:
    """English text of a TranslatedString, else its first translation."""
    best = None
    for tr in value.translation:
        if tr.language in ("en", "en-AU", "en-au"):
            return tr.text
        if best is None:
            best = tr.text
    return best


def _enum_name(enum, value: int) -> str:
    return enum.Name(value) if value in enum.values() else str(value)


def _trip_key(ent: gtfs_realtime_pb2.FeedEntity, trip) -> tuple[str, str]:
    # Added trips can arrive without a trip_id; the entity id is then the
    # only stable handle within the feed.
    return (trip.trip_id or f"entity:{ent.id}", trip.start_date)


@dataclass
class Rows:
    """Rows emitted from one batch, per table."""

    snapshots: Columns = field(
        default_factory=lambda: Columns(SNAPSHOT_COLUMNS)
    )
    stops: Columns = field(default_factory=lambda: Columns(STOP_COLUMNS))
    trips: Columns = field(default_factory=lambda: Columns(TRIP_COLUMNS))
    vehicles: Columns = field(default_factory=lambda: Columns(VEHICLE_COLUMNS))
    alerts: Columns = field(default_factory=lambda: Columns(ALERT_COLUMNS))
    scopes: Columns = field(
        default_factory=lambda: Columns(ALERT_SCOPE_COLUMNS)
    )

    def by_table(self) -> dict[str, Columns]:
        return {
            "bronze.snapshot": self.snapshots,
            "silver.stop_prediction": self.stops,
            "silver.trip_status": self.trips,
            "silver.vehicle_position": self.vehicles,
            "silver.alert": self.alerts,
            "silver.alert_scope": self.scopes,
        }

    def __len__(self) -> int:
        return sum(len(c) for c in self.by_table().values())


class FeedDiffer:
    """Change-only state for one feed: the previous snapshot's values."""

    def __init__(self, feed: str, kind: str) -> None:
        self.feed = feed
        self.kind = kind
        self.mode = mode_of(feed)
        self.prev_stops: dict[tuple, tuple] = {}
        self.prev_trips: dict[tuple, tuple] = {}
        # start time, route and direction of each trip, for REMOVED rows
        self.prev_trip_info: dict[tuple, tuple] = {}
        self.prev_vehicles: dict[str, tuple] = {}
        self.prev_alerts: dict[str, str] = {}

    def seed(self, snap: Snapshot) -> None:
        """Load a snapshot as 'previous' without emitting rows."""
        self.apply(snap, None, ingested_at=None)

    def apply(
        self,
        snap: Snapshot,
        out: Rows | None,
        ingested_at: datetime | None,
        emit: Callable[[str], bool] = lambda table: True,
    ) -> None:
        """Diff ``snap`` against the previous one and emit changed rows.

        ``emit(table)`` says whether rows for a table are wanted. A table
        that already holds this file (its watermark is past it) gets
        none, but the state still advances.

        A file that failed as a whole is recorded in ``bronze.snapshot``
        only, and the previous snapshot stays the baseline (D-049):
        diffing against an empty file would end every trip and alert,
        then re-emit them all as new on the next good file.
        """
        if snap.failed:
            emitted = 0
        elif self.kind == "trip_updates":
            emitted = self._trip_updates(snap, out, emit)
        elif self.kind == "vehicle_positions":
            emitted = self._vehicles(snap, out, emit)
        elif self.kind == "service_alerts":
            emitted = self._alerts(snap, out, emit)
        else:
            emitted = 0
        if out is not None and emit("bronze.snapshot"):
            out.snapshots.add(
                self.feed,
                self.mode,
                self.kind,
                snap.source_file,
                snap.sha12,
                snap.fetched_at,
                _ts(snap.header_ts),
                snap.fetched_at.date(),
                snap.stored_bytes,
                len(snap.entities),
                snap.entity_errors,
                snap.entities_repaired,
                snap.decode_error or None,
                emitted,
                ingested_at,
            )

    def _trip_updates(self, snap: Snapshot, out: Rows | None, emit) -> int:
        stops: dict[tuple, tuple] = {}
        trips: dict[tuple, tuple] = {}
        info: dict[tuple, tuple] = {}
        observed = _ts(snap.header_ts)
        obs_date = snap.fetched_at.date()
        want_stops = out is not None and emit("silver.stop_prediction")
        want_trips = out is not None and emit("silver.trip_status")
        emitted = 0
        for ent in snap.entities:
            if not ent.HasField("trip_update"):
                continue
            tu = ent.trip_update
            trip = tu.trip
            tkey = _trip_key(ent, trip)
            direction = (
                trip.direction_id if trip.HasField("direction_id") else None
            )
            ts_observed = _ts(tu.timestamp) if tu.timestamp else observed
            parsed = [_stop_values(stu) for stu in tu.stop_time_update]
            base = trip_delay(parsed)
            for stu, value in zip(tu.stop_time_update, parsed, strict=True):
                seq = (
                    stu.stop_sequence
                    if stu.HasField("stop_sequence")
                    else None
                )
                skey = (*tkey, seq, stu.stop_id)
                change = change_key(value, base)
                stops[skey] = change
                if want_stops and self.prev_stops.get(skey) != change:
                    emitted += 1
                    out.stops.add(
                        self.mode,
                        self.feed,
                        tkey[0],
                        tkey[1] or None,
                        trip.route_id or None,
                        direction,
                        seq,
                        stu.stop_id or None,
                        _ts(value[0]),
                        value[1],
                        _ts(value[2]),
                        value[3],
                        _enum_name(TU.ScheduleRelationship, value[4]),
                        base,
                        ts_observed,
                        snap.fetched_at,
                        obs_date,
                        snap.source_file,
                    )
            tvalue = (
                trip.schedule_relationship,
                trip.route_id,
                tu.vehicle.id,
                tu.delay if tu.HasField("delay") else None,
                trip.start_time,
                len(tu.stop_time_update),
                base,
            )
            trips[tkey] = tvalue
            info[tkey] = (trip.start_time, trip.route_id, direction)
            if want_trips and self.prev_trips.get(tkey) != tvalue:
                emitted += 1
                out.trips.add(
                    self.mode,
                    self.feed,
                    tkey[0],
                    tkey[1] or None,
                    trip.start_time or None,
                    trip.route_id or None,
                    direction,
                    _enum_name(
                        gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship,
                        tvalue[0],
                    ),
                    tvalue[2] or None,
                    tvalue[3],
                    tvalue[6],
                    tvalue[5],
                    ts_observed,
                    snap.fetched_at,
                    obs_date,
                    snap.source_file,
                )
        if want_trips and not snap.decode_error:
            for tkey in sorted(set(self.prev_trips) - set(trips)):
                # status, route, vehicle, delay, start, stops, base
                _, _, vehicle, delay, _, _, base = self.prev_trips[tkey]
                start_time, route_id, direction = self.prev_trip_info[tkey]
                emitted += 1
                out.trips.add(
                    self.mode,
                    self.feed,
                    tkey[0],
                    tkey[1] or None,
                    start_time or None,
                    route_id or None,
                    direction,
                    REMOVED,
                    vehicle or None,
                    delay,
                    base,
                    0,
                    observed,
                    snap.fetched_at,
                    obs_date,
                    snap.source_file,
                )
        self.prev_stops, self.prev_trips = stops, trips
        self.prev_trip_info = info
        return emitted

    def _alerts(self, snap: Snapshot, out: Rows | None, emit) -> int:
        """One row per alert when it appears, changes or is removed.

        An alert's version is the sha256 of its serialised content, so
        ``silver.alert`` is a type-2 history: each row is valid until
        the next row for the same alert_id. Scope rows (the routes,
        stops and trips it affects) are written with each new version.
        """
        current: dict[str, str] = {}
        observed = _ts(snap.header_ts)
        obs_date = snap.fetched_at.date()
        want = out is not None and emit("silver.alert")
        want_scope = out is not None and emit("silver.alert_scope")
        emitted = 0
        for ent in snap.entities:
            if not ent.HasField("alert") or ent.is_deleted:
                continue
            alert = ent.alert
            digest = hashlib.sha256(
                alert.SerializeToString(deterministic=True)
            ).hexdigest()[:16]
            current[ent.id] = digest
            before = self.prev_alerts.get(ent.id)
            if before == digest:
                continue
            event = "new" if before is None else "changed"
            if want:
                emitted += 1
                periods = [
                    (p.start or None, p.end or None)
                    for p in alert.active_period
                ]
                starts = [a for a, _ in periods if a]
                ends = [b for _, b in periods if b]
                out.alerts.add(
                    self.mode,
                    self.feed,
                    ent.id,
                    event,
                    digest,
                    _enum_name(gtfs_realtime_pb2.Alert.Cause, alert.cause)
                    if alert.HasField("cause")
                    else None,
                    _enum_name(gtfs_realtime_pb2.Alert.Effect, alert.effect)
                    if alert.HasField("effect")
                    else None,
                    _enum_name(
                        gtfs_realtime_pb2.Alert.SeverityLevel,
                        alert.severity_level,
                    )
                    if alert.HasField("severity_level")
                    else None,
                    _text(alert.header_text),
                    _text(alert.description_text),
                    _text(alert.url),
                    _ts(min(starts)) if starts else None,
                    _ts(max(ends)) if ends else None,
                    json.dumps(periods, separators=(",", ":")),
                    observed,
                    snap.fetched_at,
                    obs_date,
                    snap.source_file,
                )
            if want_scope:
                for sel in alert.informed_entity:
                    trip = sel.trip if sel.HasField("trip") else None
                    emitted += 1
                    out.scopes.add(
                        self.mode,
                        self.feed,
                        ent.id,
                        digest,
                        sel.agency_id or None,
                        sel.route_id or None,
                        sel.route_type if sel.HasField("route_type") else None,
                        sel.stop_id or None,
                        (trip.trip_id or None) if trip else None,
                        (trip.start_date or None) if trip else None,
                        sel.direction_id
                        if sel.HasField("direction_id")
                        else None,
                        snap.fetched_at,
                        obs_date,
                        snap.source_file,
                    )
        if want:
            for alert_id in sorted(set(self.prev_alerts) - set(current)):
                emitted += 1
                out.alerts.add(
                    self.mode,
                    self.feed,
                    alert_id,
                    "removed",
                    *([None] * 10),
                    observed,
                    snap.fetched_at,
                    obs_date,
                    snap.source_file,
                )
        self.prev_alerts = current
        return emitted

    def _vehicles(self, snap: Snapshot, out: Rows | None, emit) -> int:
        seen: dict[str, tuple] = {}
        observed = _ts(snap.header_ts)
        obs_date = snap.fetched_at.date()
        want = out is not None and emit("silver.vehicle_position")
        emitted = 0
        for ent in snap.entities:
            if not ent.HasField("vehicle"):
                continue
            v = ent.vehicle
            vid = v.vehicle.id or f"entity:{ent.id}"
            pos = v.position if v.HasField("position") else None
            lat = pos.latitude if pos else None
            lon = pos.longitude if pos else None
            # Without its own timestamp, a report is new when it moved.
            value = (v.timestamp,) if v.timestamp else (None, lat, lon)
            seen[vid] = value
            if not want or self.prev_vehicles.get(vid) == value:
                continue
            speed = pos.speed if pos and pos.HasField("speed") else None
            reason = quarantine_reason(
                lat, lon, speed, v.timestamp, snap.fetched_at
            )
            trip = v.trip if v.HasField("trip") else None
            emitted += 1
            out.vehicles.add(
                self.mode,
                self.feed,
                vid,
                v.vehicle.label or None,
                (trip.trip_id or None) if trip else None,
                (trip.start_date or None) if trip else None,
                (trip.route_id or None) if trip else None,
                trip.direction_id
                if trip and trip.HasField("direction_id")
                else None,
                lat,
                lon,
                pos.bearing if pos and pos.HasField("bearing") else None,
                speed,
                _enum_name(
                    gtfs_realtime_pb2.VehiclePosition.OccupancyStatus,
                    v.occupancy_status,
                )
                if v.HasField("occupancy_status")
                else None,
                v.current_stop_sequence
                if v.HasField("current_stop_sequence")
                else None,
                v.stop_id or None,
                _enum_name(
                    gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus,
                    v.current_status,
                )
                if v.HasField("current_status")
                else None,
                _ts(v.timestamp),
                observed,
                snap.fetched_at,
                obs_date,
                reason is not None,
                reason,
                snap.source_file,
            )
        self.prev_vehicles = seen
        return emitted


def _stop_values(stu) -> tuple:
    """(arrival time, arrival delay, departure time, departure delay,
    stop status) of one stop-time update, None where absent."""
    arr = stu.arrival if stu.HasField("arrival") else None
    dep = stu.departure if stu.HasField("departure") else None
    return (
        arr.time if arr and arr.HasField("time") else None,
        arr.delay if arr and arr.HasField("delay") else None,
        dep.time if dep and dep.HasField("time") else None,
        dep.delay if dep and dep.HasField("delay") else None,
        stu.schedule_relationship,
    )


def trip_delay(stops: list[tuple]) -> int | None:
    """The trip's current delay: its first listed stop's delay."""
    for _, arr_delay, _, dep_delay, _ in stops:
        if arr_delay is not None:
            return arr_delay
        if dep_delay is not None:
            return dep_delay
    return None


def change_key(value: tuple, base: int | None) -> tuple:
    """What must differ for a stop prediction to count as changed.

    When a bus runs late, TfNSW moves every remaining stop by the same
    delay. Storing each of them again is most of the bus volume
    (docs/reports/worker-dryrun-20261119.md). So a stop is compared by
    its implied scheduled time (time - delay) and its delay *relative
    to the trip's current delay*; a uniform shift changes only the trip
    delay, which ``trip_status.current_delay_s`` records. Lossless: the
    prediction at any moment is the stop's latest row shifted by the
    change in trip delay since that row (``trip_delay_s``).
    """
    arr_time, arr_delay, dep_time, dep_delay, status = value
    if base is None:
        return value

    def part(time, delay):
        if delay is None:
            return (time, None)
        sched = None if time is None else time - delay
        return (sched, delay - base)

    return (*part(arr_time, arr_delay), *part(dep_time, dep_delay), status)


def quarantine_reason(
    lat: float | None,
    lon: float | None,
    speed: float | None,
    event_ts: int,
    fetched_at: datetime,
) -> str | None:
    """Why a position is implausible, or None when it looks fine."""
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return "no_position"
    lo_lat, hi_lat, lo_lon, hi_lon = NSW_BOX
    if not (lo_lat <= lat <= hi_lat and lo_lon <= lon <= hi_lon):
        return "outside_nsw"
    if speed is not None and speed > MAX_SPEED_MPS:
        return "speed_over_150kmh"
    if event_ts and event_ts - fetched_at.timestamp() > MAX_FUTURE_S:
        return "timestamp_in_future"
    return None
