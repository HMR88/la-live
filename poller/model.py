"""Data shapes shared by the poller modules.

Geometry convention: feed coordinates are WGS84 (EPSG:4326) lon/lat; every
distance or projection is done in EPSG:32611 (WGS84 / UTM zone 11N, metres).
"""

from __future__ import annotations

from dataclasses import dataclass

from pyproj import Transformer

AGENCY_BY_NAME = {
    "Metro - Los Angeles": "metro",
    "Big Blue Bus": "bbb",
    "Culver CityBus": "ccb",
    "LADOT": "ladot",
}

CORRIDOR = (-118.52, 33.98, -118.22, 34.09)  # lon_min, lat_min, lon_max, lat_max

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32611", always_xy=True)
TO_WGS = Transformer.from_crs("EPSG:32611", "EPSG:4326", always_xy=True)


@dataclass(frozen=True)
class Observation:
    """One vehicle position as the feed reported it."""

    agency: str
    vehicle_id: str
    trip_id: str
    route_id: str  # raw value from the feed; Big Blue Bus sends the short name here
    ts: int  # epoch seconds
    lon: float
    lat: float
    bearing: float | None = None

    def in_corridor(self, margin_deg: float = 0.02) -> bool:
        lon_min, lat_min, lon_max, lat_max = CORRIDOR
        return (lon_min - margin_deg <= self.lon <= lon_max + margin_deg
                and lat_min - margin_deg <= self.lat <= lat_max + margin_deg)


@dataclass(frozen=True)
class SegmentSpeed:
    """One speed observation charged to one segment."""

    ts: int
    segment_id: int
    source: str
    speed_kmh: float
    weight: float  # metres of the segment covered by the pair
    vehicle_id: str
    agency: str


@dataclass(frozen=True)
class PairResult:
    """Outcome of comparing two consecutive positions of one vehicle."""

    agency: str
    vehicle_id: str
    ts0: int
    ts1: int
    reason: str  # "kept" or the drop reason
    shape_id: str | None
    speed_kmh: float | None
    dist_m: float | None
