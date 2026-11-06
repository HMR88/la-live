import json
from pathlib import Path

import numpy as np

from cameras.config import Camera
from cameras.counting import A, B, Line, Track, count_tracks, first_crossing, track_direction
from cameras.sample import Sample
from cameras.snapshot import CameraStore, DetectorReading, nearby_detector_readings, write_cameras
from cameras.speed import CalibrationFile, Calibration, direction_speed_kmh, track_speed_kmh

LINE = Line((0, 100), (200, 100))  # horizontal, positive side below (y > 100)


def track(tid, pts):
    return Track(tid, [(float(t), float(x), float(y)) for t, x, y in pts])


def test_crossing_per_direction():
    down = track(1, [(0, 50, 80), (1, 50, 95), (2, 50, 105), (3, 50, 120)])
    up = track(2, [(0, 150, 130), (1, 150, 110), (2, 150, 90)])
    assert first_crossing(LINE, down) == A
    assert first_crossing(LINE, up) == B
    r = count_tracks(LINE, [down, up], duration_s=15.0, frames=225)
    assert (r.a.count, r.b.count) == (1, 1)
    assert r.a.vehicles_per_min == 4.0 and r.b.vehicles_per_min == 4.0
    assert r.tracks == 2 and r.frames == 225
    assert r.a.flow_px_s == round(40 / 3, 1) and r.b.flow_px_s == 20.0
    assert r.density_all == round(7 / 225, 2)


def test_touching_track_does_not_count():
    touch = track(3, [(0, 50, 80), (1, 50, 100), (2, 50, 90), (3, 50, 70)])
    assert first_crossing(LINE, touch) == 0
    r = count_tracks(LINE, [touch], 10.0, 150)
    assert (r.a.count, r.b.count) == (0, 0)
    assert r.direction_by_track[3] == B  # it moved up 10 px, labelled for density only
    assert r.b.density == round(4 / 150, 2) and r.a.density == 0.0


def test_track_crossing_twice_counts_once():
    jitter = track(4, [(0, 50, 90), (1, 50, 110), (2, 50, 95), (3, 50, 112), (4, 50, 130)])
    assert first_crossing(LINE, jitter) == A
    r = count_tracks(LINE, [jitter], 10.0, 150)
    assert (r.a.count, r.b.count) == (1, 0)


def test_crossing_beyond_the_segment_end_does_not_count():
    beside = track(5, [(0, 250, 80), (1, 250, 120)])
    assert first_crossing(LINE, beside) == 0
    assert track_direction(LINE, beside) == A


def test_short_track_has_no_direction():
    still = track(6, [(0, 50, 150), (1, 50, 155)])
    assert track_direction(LINE, still) == 0


def test_line_from_fractions():
    line = Line.from_fractions([[0.0, 0.5], [1.0, 0.5]], 1280, 720)
    assert line.p0 == (0.0, 360.0) and line.p1 == (1280.0, 360.0)
    assert line.side((10, 400)) > 0


def test_speed_through_synthetic_calibration():
    # image 1000x1000 maps 1:1 onto a 100 m square, so 10 px = 1 m
    cal = Calibration.from_points(CalibrationFile.model_validate({
        "crs": "EPSG:26911",
        "points": [{"px": [0, 0], "ground": [500000, 3760000]}, {"px": [1000, 0], "ground": [500100, 3760000]},
                   {"px": [1000, 1000], "ground": [500100, 3759900]}, {"px": [0, 1000], "ground": [500000, 3759900]}]}).points)
    t = track(1, [(0, 100, 100), (2, 100, 400)])  # 300 px = 30 m in 2 s = 15 m/s = 54 km/h
    v = track_speed_kmh(cal, t)
    assert v is not None and abs(v - 54.0) < 1e-6
    assert track_speed_kmh(cal, track(2, [(0, 0, 0), (0.5, 0, 100)])) is None
    assert direction_speed_kmh(cal, [t], {1: A}, A) == 54.0
    assert direction_speed_kmh(cal, [t], {1: A}, B) is None


CAM = Camera(cctv_id=101, name="I-10 : (101) Mcclure West", route="I-10", direction="East", postmile=35.248,
             stream_url="https://wzmedia.dot.ca.gov/D7/CCTV-101.stream/playlist.m3u8", image_url="https://x/y.jpg",
             image_minutes=2, segment_id=1790, lon=-118.4962, lat=34.0115)


def test_geojson_writer_and_store(tmp_path: Path):
    down = track(1, [(0, 50, 80), (1, 50, 120)])
    r = count_tracks(LINE, [down], 15.0, 225)
    s = Sample(cctv_id=101, sampled_at=1_794_000_000, status="ok", seconds_requested=15, model="yolo11s", model_version="8.4.203",
               device="mps", resolution="1280x720", fps=15.0, frames=225, duration_s=15.0, open_s=1.5, decode_s=0.5, infer_s=7.0, result=r)
    n = write_cameras(tmp_path / "cameras.geojson", [CAM], {101: s}, now=1_794_000_030)
    assert n == 1
    gj = json.loads((tmp_path / "cameras.geojson").read_text())
    f = gj["features"][0]
    assert f["geometry"] == {"type": "Point", "coordinates": [-118.4962, 34.0115]}
    p = f["properties"]
    assert p["cctv_id"] == 101 and p["status"] == "ok" and p["sampled_at"] == "2026-11-06T21:20:00Z" and p["age_s"] == 30
    assert p["a"] == {"label": "A", "count": 1, "vehicles_per_min": 4.0, "flow_px_s": 40.0, "density": round(2 / 225, 2)}
    assert p["b"]["count"] == 0 and p["vehicles_per_min"] == 4.0
    assert p["speed_kmh"] == {"a": None, "b": None} and p["speed_status"] == "uncalibrated"
    assert p["model"] == "yolo11s" and p["model_version"] == "8.4.203"
    pending = json.loads((tmp_path / "cameras.geojson").read_text()) if write_cameras(tmp_path / "cameras.geojson", [CAM], {}, 1) else None
    assert pending is not None and pending["features"][0]["properties"]["status"] == "pending"

    store = CameraStore(tmp_path / "cameras.sqlite")
    store.write(s, [DetectorReading(718371, "route_pm", "10", "W", 35.1, 11, 40.2, 1_794_000_000)])
    store.write(s, [])  # same key again: ignored
    assert store.latest() == {101: (1_794_000_000, "ok")}
    rows = store.conn.execute("SELECT a_count, b_count, a_vpm, speed_status FROM samples").fetchall()
    assert rows == [(1, 0, 4.0, "uncalibrated")]
    assert store.conn.execute("SELECT COUNT(*) FROM detector_readings").fetchone() == (1,)
    store.close()


def test_nearby_detector_readings(tmp_path: Path):
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"station_id": 1, "route": "10", "direction": "E", "postmile": 35.0, "segment_id": 1790, "volume_30s": 12, "speed_kmh": 90.0, "ts": 5}},
        {"type": "Feature", "geometry": None, "properties": {"station_id": 2, "route": "10", "direction": "W", "postmile": 34.5, "segment_id": 9, "volume_30s": 7, "speed_kmh": None, "ts": 5}},
        {"type": "Feature", "geometry": None, "properties": {"station_id": 3, "route": "405", "direction": "N", "postmile": 35.2, "segment_id": 8, "volume_30s": 7, "speed_kmh": None, "ts": 5}},
        {"type": "Feature", "geometry": None, "properties": {"station_id": 4, "route": "10", "direction": "W", "postmile": 33.0, "segment_id": 7, "volume_30s": 7, "speed_kmh": None, "ts": 5}},
    ]}
    (tmp_path / "detectors.geojson").write_text(json.dumps(gj))
    got = nearby_detector_readings(tmp_path / "detectors.geojson", CAM)
    assert [(d.station_id, d.match) for d in got] == [(1, "segment"), (2, "route_pm")]
    assert nearby_detector_readings(tmp_path / "missing.geojson", CAM) == []
