"""Collect a Big Blue Bus vehicle trace and map-match it with Valhalla.

Two commands:

  collect  poll the GTFS-Realtime vehicle feed, keep vehicles on the Wilshire
           routes, write one GeoJSON point collection under data/traces/.
  match    POST each vehicle's points to Valhalla trace_attributes and write
           the matched edges (names, OSM way ids) as JSON and GeoJSON.

Coordinates are WGS84 (EPSG:4326) throughout: the feed, Valhalla and GeoJSON
all use it. Distances reported by Valhalla are kilometres and metres as noted.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from google.transit import gtfs_realtime_pb2 as rt
from pydantic import BaseModel, ConfigDict, Field

VEHICLE_FEED = "https://cleverapi.bigbluebus.com/gtfsrt/vehicles"
STATIC_GTFS_DIR = Path("data/downloads/bbb_gtfs")
TRACE_DIR = Path("data/traces")
DEFAULT_ROUTES = ("2", "R10")
CORRIDOR = (-118.52, 33.98, -118.22, 34.09)  # lon_min, lat_min, lon_max, lat_max


@dataclass(frozen=True)
class Position:
    vehicle_id: str
    route: str
    trip_id: str
    ts: int
    lat: float
    lon: float


def wilshire_route_names(gtfs_dir: Path, wanted: tuple[str, ...]) -> dict[str, str]:
    """Map route_short_name to route_id for the routes we keep.

    The realtime feed fills trip.route_id with the short name, not the GTFS
    route_id, so the short name is the join key. Kept routes are the wanted
    short names plus any route whose long name mentions Wilshire.
    """
    out: dict[str, str] = {}
    with (gtfs_dir / "routes.txt").open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            short, long_ = row["route_short_name"], row["route_long_name"]
            if short in wanted or "wilshire" in long_.lower():
                out[short] = row["route_id"]
    return out


def fetch_positions(client: httpx.Client, routes: set[str]) -> tuple[int, list[Position]]:
    msg = rt.FeedMessage()
    msg.ParseFromString(client.get(VEHICLE_FEED, timeout=20).content)
    kept: list[Position] = []
    for ent in msg.entity:
        v = ent.vehicle
        if not v.HasField("position") or v.trip.route_id not in routes:
            continue
        kept.append(
            Position(
                vehicle_id=v.vehicle.id,
                route=v.trip.route_id,
                trip_id=v.trip.trip_id,
                ts=int(v.timestamp),
                lat=float(v.position.latitude),
                lon=float(v.position.longitude),
            )
        )
    return int(msg.header.timestamp), kept


def in_corridor(p: Position) -> bool:
    lon_min, lat_min, lon_max, lat_max = CORRIDOR
    return lon_min <= p.lon <= lon_max and lat_min <= p.lat <= lat_max


def collect(minutes: float, interval: float, routes: tuple[str, ...], out_dir: Path) -> Path:
    names = wilshire_route_names(STATIC_GTFS_DIR, routes)
    print(f"routes kept (short name -> route_id): {names}", flush=True)
    seen: dict[tuple[str, int], Position] = {}
    deadline = time.monotonic() + minutes * 60
    polls = 0
    with httpx.Client(headers={"User-Agent": "la-live/0.1 trace_match"}) as client:
        while True:
            t0 = time.monotonic()
            try:
                header_ts, positions = fetch_positions(client, set(names))
            except (httpx.HTTPError, ValueError) as exc:
                print(f"poll failed: {exc}", file=sys.stderr, flush=True)
                positions, header_ts = [], 0
            new = 0
            for p in positions:
                if in_corridor(p) and (p.vehicle_id, p.ts) not in seen:
                    seen[(p.vehicle_id, p.ts)] = p
                    new += 1
            polls += 1
            print(
                f"poll {polls} header_ts={header_ts} vehicles={len(positions)} "
                f"new_points={new} total={len(seen)}",
                flush=True,
            )
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, interval - (time.monotonic() - t0)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"bbb_wilshire_{stamp}.geojson"
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
            "properties": {
                "vehicle_id": p.vehicle_id,
                "route": p.route,
                "trip_id": p.trip_id,
                "ts": p.ts,
                "time_utc": datetime.fromtimestamp(p.ts, timezone.utc).isoformat(),
            },
        }
        for p in sorted(seen.values(), key=lambda q: (q.vehicle_id, q.ts))
    ]
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    per_vehicle = defaultdict(int)
    for p in seen.values():
        per_vehicle[p.vehicle_id] += 1
    print(f"wrote {out} with {len(features)} points from {len(per_vehicle)} vehicles")
    print("points per vehicle:", dict(sorted(per_vehicle.items())))
    return out


class MatchedPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lat: float
    lon: float
    type: Literal["matched", "interpolated", "unmatched"]
    edge_index: int | None = None
    distance_from_trace_point: float | None = None
    distance_along_edge: float | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    names: list[str] = Field(default_factory=list)
    way_id: int | None = None
    length: float | None = None
    speed: float | None = None
    road_class: str | None = None
    begin_shape_index: int | None = None
    end_shape_index: int | None = None


class TraceAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")
    edges: list[Edge] = Field(default_factory=list)
    matched_points: list[MatchedPoint] = Field(default_factory=list)
    shape: str = ""
    units: str = "kilometers"
    confidence_score: float | None = None


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Decode Valhalla's encoded polyline (precision 1e-6) to (lon, lat)."""
    coords: list[tuple[float, float]] = []
    lat = lon = 0
    i = 0
    while i < len(encoded):
        for which in ("lat", "lon"):
            shift = result = 0
            while True:
                b = ord(encoded[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == "lat":
                lat += delta
            else:
                lon += delta
        coords.append((lon / 1e6, lat / 1e6))
    return coords


def load_trace(path: Path) -> dict[str, list[Position]]:
    data = json.loads(path.read_text())
    by_vehicle: dict[str, list[Position]] = defaultdict(list)
    for f in data["features"]:
        pr = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        by_vehicle[pr["vehicle_id"]].append(
            Position(pr["vehicle_id"], pr["route"], pr["trip_id"], int(pr["ts"]), lat, lon)
        )
    for pts in by_vehicle.values():
        pts.sort(key=lambda p: p.ts)
    return dict(by_vehicle)


def match_vehicle(client: httpx.Client, base: str, pts: list[Position]) -> TraceAttributes:
    body: dict[str, Any] = {
        "shape": [{"lat": p.lat, "lon": p.lon, "time": p.ts} for p in pts],
        "costing": "auto",
        "shape_match": "walk_or_snap",
        "trace_options": {"search_radius": 50, "gps_accuracy": 10},
        "filters": {
            "attributes": [
                "edge.names", "edge.way_id", "edge.length", "edge.speed", "edge.road_class",
                "edge.begin_shape_index", "edge.end_shape_index",
                "matched.point", "matched.type", "matched.edge_index",
                "matched.distance_from_trace_point", "matched.distance_along_edge",
                "shape",
            ],
            "action": "include",
        },
    }
    r = client.post(f"{base}/trace_attributes", json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"valhalla {r.status_code}: {r.text[:500]}")
    return TraceAttributes.model_validate(r.json())


def match(trace: Path, base: str, min_points: int) -> None:
    by_vehicle = load_trace(trace)
    stem = trace.with_suffix("").name
    summary: dict[str, Any] = {"trace": str(trace), "valhalla": base, "vehicles": {}}
    edge_features: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for vid, pts in sorted(by_vehicle.items()):
            if len(pts) < min_points:
                print(f"vehicle {vid}: {len(pts)} points, skipped (< {min_points})")
                continue
            try:
                res = match_vehicle(client, base, pts)
            except RuntimeError as exc:
                print(f"vehicle {vid}: {exc}")
                summary["vehicles"][vid] = {"error": str(exc), "points": len(pts)}
                continue
            counts = {t: sum(1 for m in res.matched_points if m.type == t)
                      for t in ("matched", "interpolated", "unmatched")}
            shape = decode_polyline6(res.shape)
            total_km = sum(e.length or 0 for e in res.edges)
            wilshire_km = sum(e.length or 0 for e in res.edges
                              if any("wilshire" in n.lower() for n in e.names))
            names = sorted({n for e in res.edges for n in e.names})
            for e in res.edges:
                if e.begin_shape_index is None or e.end_shape_index is None:
                    continue
                seg = shape[e.begin_shape_index: e.end_shape_index + 1]
                if len(seg) < 2:
                    continue
                edge_features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": seg},
                    "properties": {
                        "vehicle_id": vid, "route": pts[0].route, "names": e.names,
                        "way_id": e.way_id, "length_km": e.length, "speed_kmh": e.speed,
                        "road_class": e.road_class,
                    },
                })
            summary["vehicles"][vid] = {
                "route": pts[0].route,
                "trip_id": pts[0].trip_id,
                "points": len(pts),
                "span_s": pts[-1].ts - pts[0].ts,
                "matched_points": counts,
                "confidence_score": res.confidence_score,
                "edges": len(res.edges),
                "edges_named_wilshire": sum(1 for e in res.edges if any("wilshire" in n.lower() for n in e.names)),
                "length_km": round(total_km, 3),
                "wilshire_length_km": round(wilshire_km, 3),
                "edge_names": names,
                "edge_list": [e.model_dump() for e in res.edges],
                "matched_point_list": [m.model_dump() for m in res.matched_points],
            }
            share = wilshire_km / total_km if total_km else 0.0
            print(
                f"vehicle {vid} route {pts[0].route}: {len(pts)} points over {pts[-1].ts - pts[0].ts}s; "
                f"matched {counts['matched']} interpolated {counts['interpolated']} unmatched {counts['unmatched']}; "
                f"{len(res.edges)} edges, {total_km:.2f} km, {share:.0%} on Wilshire; names: {names}"
            )
    out_json = trace.with_name(f"{stem}_matched.json")
    out_geojson = trace.with_name(f"{stem}_matched_edges.geojson")
    out_json.write_text(json.dumps(summary, indent=1))
    out_geojson.write_text(json.dumps({"type": "FeatureCollection", "features": edge_features}))
    print(f"wrote {out_json} and {out_geojson} ({len(edge_features)} edge features)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="poll the vehicle feed and write a GeoJSON trace")
    c.add_argument("--minutes", type=float, default=10)
    c.add_argument("--interval", type=float, default=20)
    c.add_argument("--routes", nargs="+", default=list(DEFAULT_ROUTES), help="route short names")
    c.add_argument("--out-dir", type=Path, default=TRACE_DIR)
    m = sub.add_parser("match", help="map-match a trace with Valhalla trace_attributes")
    m.add_argument("trace", type=Path)
    m.add_argument("--valhalla", default="http://localhost:8002")
    m.add_argument("--min-points", type=int, default=5)
    args = ap.parse_args()
    if args.cmd == "collect":
        collect(args.minutes, args.interval, tuple(args.routes), args.out_dir)
    else:
        match(args.trace, args.valhalla, args.min_points)


if __name__ == "__main__":
    main()
