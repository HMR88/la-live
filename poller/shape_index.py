"""Map every transit shape onto road segments with Valhalla, once.

For each shape the index holds the ordered segments it runs along as
(segment_id, from_m, to_m) in metres along the shape, and the along-shape
distance of every stop of the same agency within STOP_RADIUS_M of the line.
Cached as JSON in data/base/shape_index.json.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import LineString, MultiLineString, Point, box

from poller.base import BaseData, Shape
from poller.model import CORRIDOR, TO_UTM, TO_WGS

STOP_RADIUS_M = 30.0
SEGMENT_MATCH_M = 20.0
MIN_PIECE_M = 100.0


def corridor_polygon(margin_m: float = 1500.0):
    """The corridor box (plus the tile margin) in EPSG:32611."""
    lon_min, lat_min, lon_max, lat_max = CORRIDOR
    x0, y0 = TO_UTM.transform(lon_min, lat_min)
    x1, y1 = TO_UTM.transform(lon_max, lat_max)
    return box(x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m)


def corridor_pieces(shape: Shape) -> list[LineString]:
    """The parts of a shape inside the corridor, each long enough to trace."""
    inter = shape.geom.intersection(corridor_polygon())
    parts = list(inter.geoms) if isinstance(inter, MultiLineString) else [inter]
    return [p for p in parts if isinstance(p, LineString) and p.length >= MIN_PIECE_M and len(p.coords) >= 2]


@dataclass
class ShapeMapping:
    length_m: float
    coverage: float  # mapped metres over the metres of the shape inside the corridor
    unmatched_points: int
    segments: list[tuple[int, float, float]]  # (segment_id, from_m, to_m), ordered along the shape
    stops_m: list[float]
    error: str | None = None

    @property
    def end_to_end(self) -> bool:
        return self.error is None and self.unmatched_points == 0 and self.coverage >= 0.95


@dataclass
class ShapeIndex:
    built_utc: str
    mappings: dict[str, ShapeMapping] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        payload = {
            "built_utc": self.built_utc,
            "shapes": {k: {"length_m": m.length_m, "coverage": m.coverage, "unmatched_points": m.unmatched_points,
                           "segments": m.segments, "stops_m": m.stops_m, "error": m.error}
                       for k, m in self.mappings.items()},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "ShapeIndex":
        d = json.loads(path.read_text())
        idx = cls(built_utc=d["built_utc"])
        for k, m in d["shapes"].items():
            idx.mappings[k] = ShapeMapping(m["length_m"], m["coverage"], m["unmatched_points"],
                                           [(int(s[0]), float(s[1]), float(s[2])) for s in m["segments"]],
                                           [float(x) for x in m["stops_m"]], m.get("error"))
        return idx

    def summary(self) -> dict[str, int]:
        ms = list(self.mappings.values())
        return {
            "shapes": len(ms),
            "end_to_end": sum(1 for m in ms if m.end_to_end),
            "partial": sum(1 for m in ms if not m.end_to_end and m.error is None and m.segments),
            "no_segments": sum(1 for m in ms if m.error is None and not m.segments),
            "errors": sum(1 for m in ms if m.error is not None),
        }


class _Edge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    way_id: int | None = None
    length: float | None = None
    begin_shape_index: int | None = None
    end_shape_index: int | None = None


class _Matched(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str


class _TraceAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")
    edges: list[_Edge] = Field(default_factory=list)
    matched_points: list[_Matched] = Field(default_factory=list)
    shape: str = ""


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Valhalla encoded polyline, precision 1e-6, to (lon, lat)."""
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


def trace_shape(client: httpx.Client, valhalla: str, piece: LineString, max_points: int = 2000) -> _TraceAttributes:
    coords = list(piece.coords)
    step = max(1, len(coords) // max_points + (1 if len(coords) % max_points else 0))
    sampled = coords[::step]
    if sampled[-1] != coords[-1]:
        sampled.append(coords[-1])
    body: dict[str, Any] = {
        "shape": [{"lat": lat, "lon": lon} for lon, lat in (TO_WGS.transform(x, y) for x, y in sampled)],
        "costing": "bus",
        "shape_match": "map_snap",
        "trace_options": {"search_radius": 40, "gps_accuracy": 10},
        "filters": {"attributes": ["edge.way_id", "edge.length", "edge.begin_shape_index",
                                   "edge.end_shape_index", "matched.type", "shape"], "action": "include"},
    }
    r = client.post(f"{valhalla}/trace_attributes", json=body, timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"valhalla {r.status_code}: {r.text[:200]}")
    return _TraceAttributes.model_validate(r.json())


def map_shape(base: BaseData, shape: Shape, results: list[_TraceAttributes], inside_m: float) -> ShapeMapping:
    line = shape.geom
    segs: list[tuple[int, float, float]] = []
    unmatched = 0
    for res in results:
        path = [Point(TO_UTM.transform(lon, lat)) for lon, lat in decode_polyline6(res.shape)]
        unmatched += sum(1 for m in res.matched_points if m.type == "unmatched")
        for e in res.edges:
            if e.way_id is None or e.begin_shape_index is None or e.end_shape_index is None:
                continue
            pts = path[e.begin_shape_index: e.end_shape_index + 1]
            if len(pts) < 2:
                continue
            mid = LineString(pts).interpolate(0.5, normalized=True)
            best: tuple[float, int] | None = None
            for sid in base.segments_by_osm.get(e.way_id, []):
                d = base.segments[sid].geom.distance(mid)
                if d <= SEGMENT_MATCH_M and (best is None or d < best[0]):
                    best = (d, sid)
            if best is None:
                continue
            a, b = line.project(pts[0]), line.project(pts[-1])
            lo, hi = (a, b) if a <= b else (b, a)
            if segs and segs[-1][0] == best[1]:
                prev = segs[-1]
                segs[-1] = (prev[0], min(prev[1], lo), max(prev[2], hi))
            else:
                segs.append((best[1], lo, hi))
    segs.sort(key=lambda t: t[1])
    mapped_m = sum(hi - lo for _, lo, hi in segs)
    coverage = min(1.0, mapped_m / inside_m) if inside_m > 0 else 0.0
    return ShapeMapping(float(line.length), float(coverage), unmatched, segs, [])


def stops_along(base: BaseData, shape: Shape, tree_cache: dict[str, Any]) -> list[float]:
    if shape.agency not in tree_cache:
        tree_cache[shape.agency] = base.stop_tree(shape.agency)
    tree, stops = tree_cache[shape.agency]
    hits = tree.query(shape.geom.buffer(STOP_RADIUS_M), predicate="intersects")
    return sorted(float(shape.geom.project(stops[i].geom)) for i in hits)


def build_index(base: BaseData, valhalla: str = "http://localhost:8002", workers: int = 4,
                progress: bool = True) -> ShapeIndex:
    idx = ShapeIndex(built_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    tree_cache: dict[str, Any] = {}
    shapes = list(base.shapes.values())

    def work(shape: Shape) -> tuple[str, ShapeMapping]:
        pieces = corridor_pieces(shape)
        inside_m = sum(p.length for p in pieces)
        with httpx.Client() as client:
            try:
                results = [trace_shape(client, valhalla, p) for p in pieces]
                m = map_shape(base, shape, results, inside_m)
            except (RuntimeError, httpx.HTTPError, ValueError) as exc:
                m = ShapeMapping(shape.length_m, 0.0, 0, [], [], error=str(exc)[:200])
        m.stops_m = stops_along(base, shape, tree_cache)
        return shape.key, m

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (key, m) in enumerate(pool.map(work, shapes), 1):
            idx.mappings[key] = m
            if progress and n % 50 == 0:
                print(f"  {n}/{len(shapes)} shapes mapped", flush=True)
    return idx
