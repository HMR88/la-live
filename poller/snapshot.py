"""Write the live snapshots the viewer reads: vehicles, segments, status."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString

from poller.base import BaseData
from poller.model import TO_WGS, Observation
from poller.speed import VehicleState


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    tmp.replace(path)


def _to_wgs(line: LineString) -> list[list[float]]:
    out = shapely.transform(line, lambda a: np.column_stack(TO_WGS.transform(a[:, 0], a[:, 1])))
    return [[round(x, 6), round(y, 6)] for x, y in out.coords]


def write_vehicles(path: Path, states: dict[tuple[str, str], VehicleState], now: int, route_label: dict[tuple[str, str], str]) -> int:
    feats: list[dict[str, Any]] = []
    for (agency, vid), st in states.items():
        o: Observation = st.last
        if not o.in_corridor() or now - o.ts > 300:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(o.lon, 6), round(o.lat, 6)]},
            "properties": {"agency": agency, "vehicle_id": vid, "route": route_label.get((agency, o.route_id), o.route_id),
                           "trip_id": o.trip_id, "bearing": o.bearing,
                           "speed_kmh": None if st.speed_kmh is None else round(st.speed_kmh, 1),
                           "ts": o.ts, "age_s": now - o.ts},
        })
    _write_atomic(path, {"type": "FeatureCollection", "generated": _iso(now), "features": feats})
    return len(feats)


def write_segments(path: Path, base: BaseData, speeds: list[tuple[int, float, int, int]], now: int) -> int:
    feats: list[dict[str, Any]] = []
    for sid, speed, n, latest in speeds:
        seg = base.segments.get(sid)
        if seg is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": _to_wgs(seg.geom)},
            "properties": {"segment_id": sid, "osm_id": seg.osm_id, "name": seg.name, "highway": seg.highway,
                           "speed_kmh": round(speed, 1), "n": n, "source": "bus", "age_s": now - latest},
        })
    _write_atomic(path, {"type": "FeatureCollection", "generated": _iso(now), "window_s": 600, "features": feats})
    return len(feats)


def write_status(path: Path, payload: dict[str, Any]) -> None:
    _write_atomic(path, payload)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
