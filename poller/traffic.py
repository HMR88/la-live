"""Caltrans District 7 and CHP sources: detector speeds (ATMS), incidents
(CHP), lane closures (LCS) and message signs (CMS).

Each reader keeps its last good result. A fetch that fails or parses badly
counts an error and leaves the last good result in place. Coordinates are
WGS84 (EPSG:4326). Times are epoch seconds; the feeds print Pacific local
time, which is converted with the America/Los_Angeles zone.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, TypeVar
from zoneinfo import ZoneInfo

import httpx

from poller.model import CORRIDOR

PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "la-live/0.1 (+https://github.com/HMR88/la-live)"
MPH_TO_KMH = 1.609344

# Colour codes seen in webupdate.txt on 2026-11-06 (speed ranges in mph):
# 0 no data (speed 0), 1 = 2 to 17, 9 = 21 to 34, 3 = 37 to 50, 2 = 50 to 70.
# A handful of rows carry colour 0 with a speed above 70; they are kept as
# readings because the speed and good-lane fields are filled. Unverified
# against any Caltrans document.
COLOUR_LABEL = {0: "no data", 1: "very slow", 9: "slow", 3: "moderate", 2: "free flow"}


def in_bbox(lon: float, lat: float) -> bool:
    lon_min, lat_min, lon_max, lat_max = CORRIDOR
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


@dataclass(frozen=True)
class VdsReading:
    station_id: int
    colour: int
    speed_mph: int
    volume_30s: int
    good_lanes_pct: int

    @property
    def valid(self) -> bool:
        return self.speed_mph > 0 and self.good_lanes_pct > 0

    @property
    def speed_kmh(self) -> float:
        return self.speed_mph * MPH_TO_KMH


@dataclass(frozen=True)
class AtmsSnapshot:
    created_ts: int
    readings: dict[int, VdsReading]


@dataclass(frozen=True)
class Incident:
    log_id: str
    ts: int
    log_type: str
    location: str
    location_desc: str
    area: str
    lon: float
    lat: float
    details: tuple[str, ...]


@dataclass(frozen=True)
class Closure:
    closure_id: str
    start_ts: int
    end_ts: int | None  # None when indefinite
    route: str
    direction: str
    facility: str
    type_of_closure: str
    type_of_work: str
    lanes_closed: str
    begin_name: str
    end_name: str
    lon0: float
    lat0: float
    lon1: float
    lat1: float
    in_effect: bool  # Caltrans code 10-97 reported
    ended: bool  # Caltrans code 10-98 reported

    def is_current(self, now: int) -> bool:
        return self.start_ts <= now and not self.ended and (self.end_ts is None or self.end_ts > now)


@dataclass(frozen=True)
class Sign:
    sign_id: str
    lon: float
    lat: float
    route: str
    direction: str
    location: str
    in_service: bool
    display: str
    text: str
    record_ts: int


_CREATED = re.compile(r"^### Created:\s*(.+?)\s*$", re.M)


def _pacific_ts(text: str, fmt: str) -> int:
    return int(datetime.strptime(" ".join(text.split()), fmt).replace(tzinfo=PACIFIC).timestamp())


def parse_webupdate(text: str) -> AtmsSnapshot:
    m = _CREATED.search(text)
    if m is None:
        raise ValueError("webupdate.txt without a '### Created:' line (truncated?)")
    created = _pacific_ts(m.group(1), "%a %b %d %H:%M:%S %Y")
    readings: dict[int, VdsReading] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 6 and parts[0] == "VDS":
            r = VdsReading(int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]))
            readings[r.station_id] = r
    if not readings:
        raise ValueError("webupdate.txt without VDS rows")
    return AtmsSnapshot(created, readings)


def _unquote(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip().strip('"').strip()


def parse_latlon(value: str) -> tuple[float, float]:
    """'33949745:118170477' -> (lon, lat); degrees times 1e6, west longitude sign dropped."""
    lat_s, lon_s = value.split(":")
    return -abs(int(lon_s)) / 1e6, int(lat_s) / 1e6


def parse_chp(text: str, dispatch: str = "LACC") -> list[Incident]:
    """All logs of one dispatch centre. Raises ValueError on a truncated file."""
    if "</State>" not in text[-200:]:
        raise ValueError("sa.xml without closing </State> (truncated)")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"sa.xml does not parse: {exc}") from exc
    out: list[Incident] = []
    for disp in root.iter("Dispatch"):
        if disp.get("ID", "").strip() != dispatch:
            continue
        for log in disp.iter("Log"):
            latlon = _unquote(log.find("LATLON"))
            if not latlon or ":" not in latlon:
                continue
            lon, lat = parse_latlon(latlon)
            try:
                ts = _pacific_ts(_unquote(log.find("LogTime")), "%b %d %Y %I:%M%p")
            except ValueError:
                continue
            details = tuple(_unquote(d.find("IncidentDetail")) for d in log.iter("details") if d.find("IncidentDetail") is not None)
            out.append(Incident(log.get("ID", "").strip(), ts, _unquote(log.find("LogType")), _unquote(log.find("Location")),
                                _unquote(log.find("LocationDesc")), _unquote(log.find("Area")), lon, lat, details))
    return out


def _epoch(s: object) -> int | None:
    try:
        return int(str(s))
    except (TypeError, ValueError):
        return None


def parse_lcs(payload: dict[str, Any]) -> list[Closure]:
    out: list[Closure] = []
    for item in payload.get("data", []):
        lcs = item.get("lcs", {})
        loc, clo = lcs.get("location", {}), lcs.get("closure", {})
        b, e, ts = loc.get("begin", {}), loc.get("end", {}), clo.get("closureTimestamp", {})
        start = _epoch(ts.get("closureStartEpoch"))
        try:
            lon0, lat0 = float(b["beginLongitude"]), float(b["beginLatitude"])
            lon1, lat1 = float(e["endLongitude"]), float(e["endLatitude"])
        except (KeyError, ValueError):
            continue
        if start is None:
            continue
        indefinite = str(ts.get("isClosureEndIndefinite", "false")).lower() == "true"
        out.append(Closure(
            closure_id=str(clo.get("closureID", "")), start_ts=start,
            end_ts=None if indefinite else _epoch(ts.get("closureEndEpoch")),
            route=str(b.get("beginRoute", "")), direction=str(loc.get("travelFlowDirection", "")),
            facility=str(clo.get("facility", "")), type_of_closure=str(clo.get("typeOfClosure", "")),
            type_of_work=str(clo.get("typeOfWork", "")), lanes_closed=str(clo.get("lanesClosed", "")),
            begin_name=str(b.get("beginLocationName", "")), end_name=str(e.get("endLocationName", "")),
            lon0=lon0, lat0=lat0, lon1=lon1, lat1=lat1,
            in_effect=str(clo.get("code1097", {}).get("isCode1097", "false")).lower() == "true",
            ended=str(clo.get("code1098", {}).get("isCode1098", "false")).lower() == "true",
        ))
    return out


def parse_cms(payload: dict[str, Any]) -> list[Sign]:
    out: list[Sign] = []
    for item in payload.get("data", []):
        cms = item.get("cms", {})
        loc, msg, rec = cms.get("location", {}), cms.get("message", {}), cms.get("recordTimestamp", {})
        try:
            lon, lat = float(loc["longitude"]), float(loc["latitude"])
        except (KeyError, ValueError):
            continue
        lines = [str(msg.get(f"phase{p}", {}).get(f"phase{p}Line{n}", "")).strip() for p in (1, 2) for n in (1, 2, 3)]
        text = " / ".join(l for l in lines if l)
        try:
            record_ts = _pacific_ts(f"{rec.get('recordDate', '')} {rec.get('recordTime', '')}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            record_ts = 0
        out.append(Sign(str(cms.get("index", "")), lon, lat, str(loc.get("route", "")), str(loc.get("direction", "")),
                        str(loc.get("locationName", "")), str(cms.get("inService", "")).lower() == "true",
                        str(msg.get("display", "")), text, record_ts))
    return out


T = TypeVar("T")


@dataclass
class CachedSource(Generic[T]):
    """Fetch a URL at most every interval_s seconds; keep the last good parse."""

    name: str
    url: str
    interval_s: int
    parse: Callable[[httpx.Response], T]
    client: httpx.Client = field(default_factory=lambda: httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60))
    last_good: T | None = None
    last_fetch_ts: int = 0
    last_good_ts: int = 0
    fetches: int = 0
    errors: int = 0
    last_error: str | None = None

    def poll(self, now: int | None = None) -> T | None:
        now = int(time.time()) if now is None else now
        if now - self.last_fetch_ts < self.interval_s:
            return self.last_good
        self.last_fetch_ts = now
        self.fetches += 1
        try:
            r = self.client.get(self.url)
            r.raise_for_status()
            self.last_good = self.parse(r)
            self.last_good_ts = now
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
        return self.last_good

    def status(self, now: int) -> dict[str, Any]:
        return {"url": self.url, "interval_s": self.interval_s, "fetches": self.fetches, "errors": self.errors,
                "last_error": self.last_error, "age_s": (now - self.last_good_ts) if self.last_good_ts else None}


def default_sources() -> tuple[CachedSource[AtmsSnapshot], CachedSource[list[Incident]], CachedSource[list[Closure]], CachedSource[list[Sign]]]:
    return (
        CachedSource("atms", "https://cwwp2.dot.ca.gov/data/d7/atms/webupdate.txt", 60, lambda r: parse_webupdate(r.text)),
        CachedSource("chp", "https://media.chp.ca.gov/sa_xml/sa.xml", 60, lambda r: parse_chp(r.text)),
        CachedSource("lcs", "https://cwwp2.dot.ca.gov/data/d7/lcs/lcsStatusD07.json", 300, lambda r: parse_lcs(r.json())),
        CachedSource("cms", "https://cwwp2.dot.ca.gov/data/d7/cms/cmsStatusD07.json", 60, lambda r: parse_cms(r.json())),
    )
