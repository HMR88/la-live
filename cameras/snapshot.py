"""Outputs of the sampler: live/cameras.geojson and history/cameras.sqlite. Nothing the poller writes."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cameras.config import Camera
from cameras.sample import Sample

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  cctv_id INTEGER NOT NULL, sampled_at INTEGER NOT NULL, status TEXT NOT NULL, resolution TEXT, fps REAL,
  frames INTEGER, duration_s REAL, open_s REAL, decode_s REAL, infer_s REAL,
  a_label TEXT, a_count INTEGER, a_vpm REAL, a_flow REAL, a_density REAL,
  b_label TEXT, b_count INTEGER, b_vpm REAL, b_flow REAL, b_density REAL,
  density_all REAL, tracks INTEGER, speed_a REAL, speed_b REAL, speed_status TEXT,
  model TEXT, model_version TEXT, device TEXT, error TEXT,
  PRIMARY KEY (cctv_id, sampled_at));
CREATE TABLE IF NOT EXISTS detector_readings (
  cctv_id INTEGER NOT NULL, sampled_at INTEGER NOT NULL, station_id INTEGER NOT NULL, match TEXT NOT NULL,
  route TEXT, direction TEXT, postmile REAL, volume_30s INTEGER, speed_kmh REAL, ts INTEGER,
  PRIMARY KEY (cctv_id, sampled_at, station_id));
"""


@dataclass(frozen=True)
class DetectorReading:
    station_id: int
    match: str
    route: str
    direction: str
    postmile: float | None
    volume_30s: int | None
    speed_kmh: float | None
    ts: int | None


def nearby_detector_readings(detectors_geojson: Path, cam: Camera, max_pm: float = 1.0) -> list[DetectorReading]:
    """Read the poller's live/detectors.geojson and pick the stations that speak for this camera:
    'segment' when on the camera's segment, else 'route_pm' when on the same route within max_pm miles."""
    if not detectors_geojson.exists():
        return []
    try:
        feats = json.loads(detectors_geojson.read_text())["features"]
    except (ValueError, KeyError, OSError):
        return []
    out: list[DetectorReading] = []
    for f in feats:
        p = f["properties"]
        if not p.get("volume_30s") or p.get("ts") is None:
            continue
        match = None
        if cam.segment_id is not None and p.get("segment_id") == cam.segment_id:
            match = "segment"
        elif cam.postmile is not None and str(p.get("route")) == cam.route_number and p.get("postmile") is not None \
                and abs(float(p["postmile"]) - cam.postmile) <= max_pm:
            match = "route_pm"
        if match:
            out.append(DetectorReading(int(p["station_id"]), match, str(p.get("route")), str(p.get("direction")),
                                       p.get("postmile"), p.get("volume_30s"), p.get("speed_kmh"), p.get("ts")))
    return out


def _direction_props(s: Sample, key: str) -> dict[str, Any] | None:
    if s.result is None:
        return None
    d = s.result.a if key == "a" else s.result.b
    return {"label": d.label, "count": d.count, "vehicles_per_min": d.vehicles_per_min, "flow_px_s": d.flow_px_s, "density": d.density}


def feature(cam: Camera, s: Sample | None, now: int) -> dict[str, Any]:
    props: dict[str, Any] = {"cctv_id": cam.cctv_id, "name": cam.name, "route": cam.route, "direction": cam.direction,
                             "postmile": cam.postmile, "segment_id": cam.segment_id, "image_url": cam.image_url,
                             "stream_url": cam.stream_url}
    if s is None:
        props.update({"status": "pending", "sampled_at": None, "age_s": None})
    else:
        props.update({
            "status": s.status, "sampled_at": s.sampled_at_iso, "age_s": now - s.sampled_at, "duration_s": s.duration_s,
            "resolution": s.resolution, "fps": s.fps, "frames": s.frames, "open_s": s.open_s, "decode_s": s.decode_s, "infer_s": s.infer_s,
            "a": _direction_props(s, "a"), "b": _direction_props(s, "b"),
            "vehicles_per_min": round(s.result.a.vehicles_per_min + s.result.b.vehicles_per_min, 1) if s.result else None,
            "density_all": s.result.density_all if s.result else None, "tracks": s.result.tracks if s.result else None,
            "speed_kmh": s.speed_kmh, "speed_status": s.speed_status,
            "model": s.model, "model_version": s.model_version, "device": s.device, "error": s.error})
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(cam.lon, 6), round(cam.lat, 6)]}, "properties": props}


def write_cameras(path: Path, cams: list[Camera], samples: dict[int, Sample], now: int, generated_note: str = "") -> int:
    feats = [feature(c, samples.get(c.cctv_id), now) for c in cams]
    payload = {"type": "FeatureCollection", "crs_note": "EPSG:4326", "generated_ts": now, "note": generated_note, "features": feats}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)
    return len(feats)


class CameraStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def write(self, s: Sample, readings: list[DetectorReading]) -> None:
        r = s.result
        self.conn.execute(
            "INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.cctv_id, s.sampled_at, s.status, s.resolution, s.fps, s.frames, s.duration_s, s.open_s, s.decode_s, s.infer_s,
             r.a.label if r else None, r.a.count if r else None, r.a.vehicles_per_min if r else None, r.a.flow_px_s if r else None, r.a.density if r else None,
             r.b.label if r else None, r.b.count if r else None, r.b.vehicles_per_min if r else None, r.b.flow_px_s if r else None, r.b.density if r else None,
             r.density_all if r else None, r.tracks if r else None, s.speed_kmh["a"], s.speed_kmh["b"], s.speed_status,
             s.model, s.model_version, s.device, s.error))
        self.conn.executemany(
            "INSERT OR IGNORE INTO detector_readings VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(s.cctv_id, s.sampled_at, d.station_id, d.match, d.route, d.direction, d.postmile, d.volume_30s, d.speed_kmh, d.ts) for d in readings])
        self.conn.commit()

    def latest(self) -> dict[int, tuple[int, str]]:
        """cctv_id -> (sampled_at, status) of the newest row, so a restart knows what it already has."""
        rows = self.conn.execute("SELECT cctv_id, MAX(sampled_at), status FROM samples GROUP BY cctv_id").fetchall()
        return {int(c): (int(t), str(st)) for c, t, st in rows}

    def close(self) -> None:
        self.conn.close()
