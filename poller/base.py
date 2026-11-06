"""Load the corridor base layers (data/base, from qgis-mcp step 2).

All geometries are kept in EPSG:32611 so distances are metres.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import shapely
from pyogrio.raw import read
from shapely.geometry import LineString, MultiLineString, Point
from shapely.strtree import STRtree

from poller.model import AGENCY_BY_NAME

GTFS_FILES = {
    "metro": "gtfs_metro_bus.gpkg",
    "bbb": "gtfs_bigbluebus.gpkg",
    "ccb": "gtfs_culvercity.gpkg",
    "ladot": "gtfs_ladot.gpkg",
}


@dataclass(frozen=True)
class Segment:
    segment_id: int
    osm_id: int
    highway: str
    name: str | None
    length_m: float
    geom: LineString  # EPSG:32611


@dataclass(frozen=True)
class Shape:
    agency: str
    shape_id: str
    route_id: str
    route_short_name: str
    geom: LineString  # EPSG:32611

    @property
    def key(self) -> str:
        return f"{self.agency}|{self.shape_id}"

    @property
    def length_m(self) -> float:
        return float(self.geom.length)


@dataclass(frozen=True)
class Stop:
    agency: str
    stop_id: str
    geom: Point  # EPSG:32611


@dataclass
class BaseData:
    segments: dict[int, Segment]
    shapes: dict[str, Shape]  # by Shape.key
    stops: list[Stop]
    trips: dict[tuple[str, str], tuple[str, str]]  # (agency, trip_id) -> (shape_id, route_id)
    segments_by_osm: dict[int, list[int]] = field(default_factory=dict)
    shapes_by_route: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (agency, route_id) -> keys
    shapes_by_short_name: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        by_osm: dict[int, list[int]] = defaultdict(list)
        for s in self.segments.values():
            by_osm[s.osm_id].append(s.segment_id)
        self.segments_by_osm = dict(by_osm)
        by_route: dict[tuple[str, str], list[str]] = defaultdict(list)
        by_short: dict[tuple[str, str], list[str]] = defaultdict(list)
        for sh in self.shapes.values():
            by_route[(sh.agency, sh.route_id)].append(sh.key)
            by_short[(sh.agency, sh.route_short_name)].append(sh.key)
        self.shapes_by_route = dict(by_route)
        self.shapes_by_short_name = dict(by_short)

    def stop_tree(self, agency: str) -> tuple[STRtree, list[Stop]]:
        stops = [s for s in self.stops if s.agency == agency]
        return STRtree([s.geom for s in stops]), stops

    def candidate_shapes(self, agency: str, trip_id: str, route_id: str) -> list[str]:
        """Shape keys a vehicle may be on: its trip's shape, else its route's shapes."""
        hit = self.trips.get((agency, trip_id))
        if hit is not None:
            key = f"{agency}|{hit[0]}"
            if key in self.shapes:
                return [key]
            route_id = route_id or hit[1]
        if not route_id:
            return []
        return self.shapes_by_route.get((agency, route_id)) or self.shapes_by_short_name.get((agency, route_id)) or []


def _as_linestring(wkb: bytes) -> LineString | None:
    g = shapely.from_wkb(wkb)
    if isinstance(g, MultiLineString):
        merged = shapely.line_merge(g)
        if isinstance(merged, LineString):
            return merged
        parts = list(merged.geoms) if isinstance(merged, MultiLineString) else []
        return max(parts, key=lambda p: p.length) if parts else None
    return g if isinstance(g, LineString) else None


def _columns(path: Path, layer: str | None, cols: list[str], geometry: bool) -> tuple[dict[str, list[object]], list[bytes]]:
    meta, _idx, geoms, fields = read(path, layer=layer, columns=cols, read_geometry=geometry)
    table = {name: list(fields[i]) for i, name in enumerate(meta["fields"])}
    return table, list(geoms) if geoms is not None else []


def load_base(base_dir: Path = Path("data/base")) -> BaseData:
    seg_tab, seg_geoms = _columns(base_dir / "segments.gpkg", None,
                                  ["segment_id", "osm_id", "highway", "name", "length_m"], True)
    segments: dict[int, Segment] = {}
    for i, wkb in enumerate(seg_geoms):
        line = _as_linestring(wkb)
        if line is None:
            continue
        sid = int(str(seg_tab["segment_id"][i]))
        segments[sid] = Segment(sid, int(str(seg_tab["osm_id"][i])), str(seg_tab["highway"][i]),
                                None if seg_tab["name"][i] is None else str(seg_tab["name"][i]),
                                float(str(seg_tab["length_m"][i])), line)

    sh_tab, sh_geoms = _columns(base_dir / "shapes.gpkg", None,
                                ["shape_id", "route_id", "route_short_name", "agency_name"], True)
    shapes: dict[str, Shape] = {}
    for i, wkb in enumerate(sh_geoms):
        agency = AGENCY_BY_NAME.get(str(sh_tab["agency_name"][i]))
        line = _as_linestring(wkb)
        if agency is None or line is None:
            continue
        sh = Shape(agency, str(sh_tab["shape_id"][i]), str(sh_tab["route_id"][i]),
                   str(sh_tab["route_short_name"][i] or ""), line)
        shapes[sh.key] = sh

    st_tab, st_geoms = _columns(base_dir / "stops.gpkg", None, ["stop_id", "agency_name"], True)
    stops: list[Stop] = []
    for i, wkb in enumerate(st_geoms):
        agency = AGENCY_BY_NAME.get(str(st_tab["agency_name"][i]))
        g = shapely.from_wkb(wkb)
        if agency is None or not isinstance(g, Point):
            continue
        stops.append(Stop(agency, str(st_tab["stop_id"][i]), g))

    trips: dict[tuple[str, str], tuple[str, str]] = {}
    for agency, fname in GTFS_FILES.items():
        tab, _ = _columns(base_dir / fname, "trips", ["trip_id", "shape_id", "route_id"], False)
        for i in range(len(tab["trip_id"])):
            trips[(agency, str(tab["trip_id"][i]))] = (str(tab["shape_id"][i]), str(tab["route_id"][i]))

    return BaseData(segments=segments, shapes=shapes, stops=stops, trips=trips)
