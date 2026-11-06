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
from poller.traffic import COLOUR_LABEL, AtmsSnapshot, Closure, Incident, Sign
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


def write_segments(path: Path, base: BaseData, speeds: list[tuple[int, str, float, int, int]], now: int) -> int:
    """One feature per segment with a speed in the window. speed_kmh is the
    detector speed where one exists (cars), else the bus speed; both are kept
    side by side as speed_bus_kmh and speed_vds_kmh."""
    per_seg: dict[int, dict[str, tuple[float, int, int]]] = {}
    for sid, source, speed, n, latest in speeds:
        per_seg.setdefault(sid, {})[source] = (speed, n, latest)
    feats: list[dict[str, Any]] = []
    for sid, by_source in per_seg.items():
        seg = base.segments.get(sid)
        if seg is None:
            continue
        bus, vds = by_source.get("bus"), by_source.get("vds")
        shown = vds or bus
        if shown is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": _to_wgs(seg.geom)},
            "properties": {
                "segment_id": sid, "osm_id": seg.osm_id, "name": seg.name, "highway": seg.highway,
                "speed_kmh": round(shown[0], 1), "n": shown[1], "age_s": now - shown[2],
                "source": "both" if bus and vds else ("vds" if vds else "bus"),
                "speed_bus_kmh": None if bus is None else round(bus[0], 1), "n_bus": 0 if bus is None else bus[1],
                "speed_vds_kmh": None if vds is None else round(vds[0], 1), "n_vds": 0 if vds is None else vds[1],
            },
        })
    _write_atomic(path, {"type": "FeatureCollection", "generated": _iso(now), "window_s": 600, "features": feats})
    return len(feats)


def write_events(path: Path, incidents: list[Incident], closures: list[Closure], signs: list[Sign], now: int) -> dict[str, int]:
    feats: list[dict[str, Any]] = []
    for i in incidents:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [i.lon, i.lat]},
                      "properties": {"type": "incident", "source": "chp", "id": i.log_id, "ts": i.ts, "time": _iso(i.ts),
                                     "age_s": now - i.ts, "text": f"{i.log_type}: {i.location}", "area": i.area,
                                     "details": list(i.details)}})
    for c in closures:
        same = abs(c.lon0 - c.lon1) < 1e-6 and abs(c.lat0 - c.lat1) < 1e-6
        geom: dict[str, Any] = ({"type": "Point", "coordinates": [c.lon0, c.lat0]} if same
                                else {"type": "LineString", "coordinates": [[c.lon0, c.lat0], [c.lon1, c.lat1]]})
        feats.append({"type": "Feature", "geometry": geom,
                      "properties": {"type": "closure", "source": "caltrans_lcs", "id": c.closure_id, "ts": c.start_ts,
                                     "time": _iso(c.start_ts), "end": None if c.end_ts is None else _iso(c.end_ts),
                                     "text": f"{c.route} {c.direction} {c.type_of_closure} closure, lanes {c.lanes_closed}: "
                                             f"{c.begin_name} to {c.end_name} ({c.type_of_work})",
                                     "facility": c.facility, "in_effect": c.in_effect}})
    for sg in signs:
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [sg.lon, sg.lat]},
                      "properties": {"type": "sign", "source": "caltrans_cms", "id": sg.sign_id, "ts": sg.record_ts,
                                     "time": _iso(sg.record_ts) if sg.record_ts else None, "route": sg.route,
                                     "direction": sg.direction, "location": sg.location, "display": sg.display, "text": sg.text}})
    _write_atomic(path, {"type": "FeatureCollection", "generated": _iso(now), "features": feats})
    return {"incidents": len(incidents), "closures": len(closures), "signs": len(signs)}


def write_detectors(path: Path, base: BaseData, atms: AtmsSnapshot | None, now: int) -> int:
    feats: list[dict[str, Any]] = []
    for sid, d in base.detectors.items():
        r = atms.readings.get(sid) if atms else None
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(d.lon, 6), round(d.lat, 6)]},
                      "properties": {"station_id": sid, "name": d.name, "route": d.route, "direction": d.direction,
                                     "postmile": d.postmile, "segment_id": d.segment_id,
                                     "speed_kmh": round(r.speed_kmh, 1) if r and r.valid else None,
                                     "speed_mph": r.speed_mph if r and r.valid else None,
                                     "volume_30s": r.volume_30s if r else None, "colour": r.colour if r else None,
                                     "colour_label": COLOUR_LABEL.get(r.colour, "unknown") if r else None,
                                     "good_lanes_pct": r.good_lanes_pct if r else None,
                                     "ts": atms.created_ts if atms else None, "age_s": (now - atms.created_ts) if atms else None}})
    _write_atomic(path, {"type": "FeatureCollection", "generated": _iso(now),
                         "note": "Caltrans District 7 ATMS feed, deprecated by Caltrans but live; speeds are detector speeds in km/h",
                         "features": feats})
    return len(feats)


def write_status(path: Path, payload: dict[str, Any]) -> None:
    _write_atomic(path, payload)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
