"""Synthetic traces through the speed engine. Shapes live in EPSG:32611; the
engine receives lon/lat like the feeds send, so every point is converted."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import LineString, Point

from poller.base import BaseData, Segment, Shape, Stop
from poller.feeds import parse_gtfs_rt, parse_metro_message
from poller.model import TO_WGS, Observation
from poller.shape_index import ShapeIndex, ShapeMapping
from poller.speed import SpeedEngine
from poller.store import Store

X0, Y0 = 370000.0, 3768000.0  # inside UTM zone 11N, near Santa Monica


def straight_world() -> tuple[BaseData, ShapeIndex]:
    """One 1,000 m straight shape along x, split into two 500 m segments, one stop at 250 m."""
    seg1 = Segment(1, 100, "primary", "Test Blvd", 500, LineString([(X0, Y0), (X0 + 500, Y0)]))
    seg2 = Segment(2, 100, "primary", "Test Blvd", 500, LineString([(X0 + 500, Y0), (X0 + 1000, Y0)]))
    shape = Shape("bbb", "s1", "4099", "2", LineString([(X0, Y0), (X0 + 1000, Y0)]))
    base = BaseData(segments={1: seg1, 2: seg2}, shapes={shape.key: shape},
                    stops=[Stop("bbb", "st1", Point(X0 + 250, Y0))], trips={("bbb", "t1"): ("s1", "4099")})
    idx = ShapeIndex(built_utc="test")
    idx.mappings[shape.key] = ShapeMapping(1000.0, 1.0, 0, [(1, 0.0, 500.0), (2, 500.0, 1000.0)], [250.0])
    return base, idx


def obs(x_m: float, ts: int, trip_id: str = "t1", route_id: str = "2", vid: str = "v1") -> Observation:
    lon, lat = TO_WGS.transform(X0 + x_m, Y0)
    return Observation("bbb", vid, trip_id, route_id, ts, lon, lat)


def test_constant_speed_lands_on_the_right_segments() -> None:
    base, idx = straight_world()
    eng = SpeedEngine(base, idx)
    assert eng.ingest(obs(400, 1000)) == (None, [])
    result, rows = eng.ingest(obs(700, 1030))  # 300 m in 30 s = 36 km/h, crossing the segment boundary at 500
    assert result is not None and result.reason == "kept" and result.speed_kmh is not None
    assert abs(result.speed_kmh - 36.0) < 0.05
    assert sorted((r.segment_id, round(r.weight), round(r.speed_kmh, 1)) for r in rows) == [(1, 100, 36.0), (2, 200, 36.0)]


def test_dwell_at_a_stop_is_excluded() -> None:
    base, idx = straight_world()
    eng = SpeedEngine(base, idx)
    eng.ingest(obs(100, 1000))
    result, rows = eng.ingest(obs(240, 1030))  # ends 10 m before the stop at 250
    assert result is not None and result.reason == "dwell" and rows == []


def test_gap_over_120_s_is_dropped() -> None:
    base, idx = straight_world()
    eng = SpeedEngine(base, idx)
    eng.ingest(obs(400, 1000))
    result, rows = eng.ingest(obs(900, 1121))
    assert result is not None and result.reason == "gap" and rows == []
    # the new position becomes the reference for the next pair
    result, rows = eng.ingest(obs(960, 1131))  # 60 m in 10 s = 21.6 km/h
    assert result is not None and result.reason == "kept" and [r.segment_id for r in rows] == [2]


def test_short_backwards_and_too_fast() -> None:
    base, idx = straight_world()
    eng = SpeedEngine(base, idx)
    eng.ingest(obs(400, 1000))
    assert eng.ingest(obs(405, 1010))[0].reason == "short"  # type: ignore[union-attr]
    assert eng.ingest(obs(350, 1020))[0].reason == "backwards"  # type: ignore[union-attr]
    assert eng.ingest(obs(990, 1030))[0].reason == "too_fast"  # type: ignore[union-attr]  # 640 m in 10 s


def test_big_blue_bus_short_name_join_without_trip() -> None:
    base, idx = straight_world()
    eng = SpeedEngine(base, idx)
    # trip id unknown to the GTFS, route_id carries the short name "2": resolve through the route's shapes
    eng.ingest(obs(600, 1000, trip_id="999999", route_id="2"))
    result, rows = eng.ingest(obs(700, 1020, trip_id="999999", route_id="2"))
    assert result is not None and result.reason == "kept" and result.shape_id == "bbb|s1"
    assert [r.segment_id for r in rows] == [2]
    # an unknown route resolves to nothing
    eng.ingest(obs(600, 1000, trip_id="999999", route_id="zz", vid="v2"))
    assert eng.ingest(obs(700, 1020, trip_id="999999", route_id="zz", vid="v2"))[0].reason == "no_shape"  # type: ignore[union-attr]


def test_store_is_idempotent(tmp_path: Path) -> None:
    base, idx = straight_world()
    eng = SpeedEngine(base, idx)
    eng.ingest(obs(400, 1000))
    result, rows = eng.ingest(obs(700, 1030))
    assert result is not None
    store = Store(tmp_path / "h.sqlite")
    for _ in range(2):
        store.write([obs(400, 1000), obs(700, 1030)], [result], rows)
    assert store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
    assert store.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2
    assert store.conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0] == 1
    latest = store.latest_positions()
    assert len(latest) == 1 and latest[0].ts == 1030
    speeds = store.segment_speeds(0)
    assert sorted((s, round(v, 1), n) for s, v, n, _ in speeds) == [(1, 36.0, 1), (2, 36.0, 1)]
    # a restarted engine seeded from the store continues the trace without a duplicate pair
    eng2 = SpeedEngine(base, idx)
    eng2.seed(latest)
    assert eng2.ingest(obs(700, 1030)) == (None, [])
    assert eng2.ingest(obs(800, 1040))[0].reason == "kept"  # type: ignore[union-attr]


def test_metro_message_parses_like_the_live_feed() -> None:
    raw = ('{"id": "5816", "vehicle": {"trip": {"tripId": "10014005111601-JUNE26", "routeId": "14-13201", "directionId": 1},'
           ' "position": {"latitude": 34.041782, "longitude": -118.26183, "bearing": 210.0, "speed": 7.15},'
           ' "timestamp": "1793927623", "vehicle": {"id": "5816"}}, "route_code": "14"}')
    o = parse_metro_message("metro", raw)
    assert o == Observation("metro", "5816", "10014005111601-JUNE26", "14-13201", 1793927623, -118.26183, 34.041782, 210.0)
    assert parse_metro_message("metro", "Error: nope") is None


def test_gtfs_rt_parse_keeps_route_short_name_as_sent() -> None:
    from google.transit import gtfs_realtime_pb2 as rt
    msg = rt.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = 1700000000
    ent = msg.entity.add()
    ent.id = "1"
    ent.vehicle.vehicle.id = "1349"
    ent.vehicle.trip.trip_id = "18030"
    ent.vehicle.trip.route_id = "2"
    ent.vehicle.position.latitude = 34.06
    ent.vehicle.position.longitude = -118.44
    ent.vehicle.timestamp = 1700000005
    out = parse_gtfs_rt("bbb", msg.SerializeToString())
    assert len(out) == 1 and (out[0].agency, out[0].vehicle_id, out[0].trip_id, out[0].route_id, out[0].ts, out[0].bearing) == ("bbb", "1349", "18030", "2", 1700000005, None)
    assert abs(out[0].lon + 118.44) < 1e-5 and abs(out[0].lat - 34.06) < 1e-5  # protobuf floats are 32-bit


def test_direction_is_chosen_from_the_movement_not_the_trip() -> None:
    base, idx = straight_world()
    reverse = Shape("bbb", "s1r", "4099", "2", LineString([(X0 + 1000, Y0), (X0, Y0)]))
    base.shapes[reverse.key] = reverse
    base.__post_init__()
    idx.mappings[reverse.key] = ShapeMapping(1000.0, 1.0, 0, [(2, 0.0, 500.0), (1, 500.0, 1000.0)], [750.0])
    eng = SpeedEngine(base, idx)
    eng.ingest(obs(600, 1000, trip_id="unknown"))
    result, _ = eng.ingest(obs(700, 1020, trip_id="unknown"))  # moving +x: the forward shape
    assert result is not None and (result.reason, result.shape_id) == ("kept", "bbb|s1")
    eng.ingest(obs(700, 2000, trip_id="unknown", vid="v2"))
    result, _ = eng.ingest(obs(600, 2020, trip_id="unknown", vid="v2"))  # moving -x: the reverse shape
    assert result is not None and (result.reason, result.shape_id) == ("kept", "bbb|s1r")
