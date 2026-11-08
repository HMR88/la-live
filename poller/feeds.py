"""Feed readers. Each yields normalised Observation lists on fetch().

Protobuf feeds (Big Blue Bus, Culver CityBus, LADOT) are polled over HTTP.
LA Metro is a WebSocket that pushes every vehicle every 3 s as JSON; a
background thread keeps the latest message per vehicle and fetch() returns a
snapshot of that. The Swiftly protobuf feed for Metro would be another
GtfsRtFeed with an Authorization header, if a key ever exists.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Protocol

import time
from collections.abc import Callable

import httpx
import websockets
from google.transit import gtfs_realtime_pb2 as rt

from poller.model import Observation

USER_AGENT = "la-live/0.1 (+https://github.com/HMR88/la-live)"


class Feed(Protocol):
    name: str
    agency: str

    def fetch(self) -> list[Observation]: ...

    def close(self) -> None: ...


class GtfsRtFeed:
    """A GTFS-Realtime vehicle positions feed polled over HTTP."""

    def __init__(self, name: str, agency: str, url: str, client: httpx.Client | None = None,
                 backoff_s: tuple[float, ...] = (1.0, 3.0), sleep: Callable[[float], None] = time.sleep) -> None:
        self.name = name
        self.agency = agency
        self.url = url
        self._client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20)
        self._backoff = backoff_s
        self._sleep = sleep
        self.retries = 0  # attempts beyond the first, over the feed's life, reported in status.json

    def fetch(self) -> list[Observation]:
        """One cycle's fetch: up to len(backoff)+1 attempts, so a single timeout does not cost the cycle."""
        for attempt, wait in enumerate((*self._backoff, None)):
            try:
                r = self._client.get(self.url)
                r.raise_for_status()
                return parse_gtfs_rt(self.agency, r.content)
            except (httpx.HTTPError, OSError):
                if wait is None:
                    raise
                self.retries += 1
                self._sleep(wait)
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self._client.close()


def parse_gtfs_rt(agency: str, payload: bytes) -> list[Observation]:
    msg = rt.FeedMessage()
    msg.ParseFromString(payload)
    out: list[Observation] = []
    for ent in msg.entity:
        v = ent.vehicle
        if not v.HasField("position") or not v.vehicle.id:
            continue
        ts = int(v.timestamp) if v.timestamp else int(msg.header.timestamp)
        out.append(
            Observation(
                agency=agency,
                vehicle_id=v.vehicle.id,
                trip_id=v.trip.trip_id,
                route_id=v.trip.route_id,
                ts=ts,
                lon=float(v.position.longitude),
                lat=float(v.position.latitude),
                bearing=float(v.position.bearing) if v.position.HasField("bearing") else None,
            )
        )
    return out


def parse_metro_message(agency: str, raw: str) -> Observation | None:
    """Parse one Metro WebSocket message (GTFS-RT VehiclePosition as camelCase JSON)."""
    try:
        d: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        return None
    v = d.get("vehicle")
    if not isinstance(v, dict):
        return None
    pos = v.get("position")
    if not isinstance(pos, dict) or "latitude" not in pos or "longitude" not in pos:
        return None
    trip_raw = v.get("trip")
    trip: dict[str, Any] = trip_raw if isinstance(trip_raw, dict) else {}
    vid = str(d.get("id") or v.get("vehicle", {}).get("id") or "")
    if not vid:
        return None
    bearing = pos.get("bearing")
    return Observation(
        agency=agency,
        vehicle_id=vid,
        trip_id=str(trip.get("tripId", "")),
        route_id=str(trip.get("routeId", "")),
        ts=int(v.get("timestamp", 0)),
        lon=float(pos["longitude"]),
        lat=float(pos["latitude"]),
        bearing=float(bearing) if isinstance(bearing, (int, float)) else None,
    )


class MetroWebSocketFeed:
    """Keeps the latest position per vehicle from wss://api.metro.net."""

    def __init__(self, name: str = "metro_ws", agency: str = "metro",
                 url: str = "wss://api.metro.net/ws/LACMTA/vehicle_positions") -> None:
        self.name = name
        self.agency = agency
        self.url = url
        self._latest: dict[str, Observation] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.messages = 0
        self.reconnects = 0
        self.last_error: str | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, user_agent_header=USER_AGENT, open_timeout=20,
                                              ping_interval=20, max_queue=4096) as ws:
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        obs = parse_metro_message(self.agency, str(raw))
                        if obs is None:
                            continue
                        with self._lock:
                            self.messages += 1
                            prev = self._latest.get(obs.vehicle_id)
                            if prev is None or obs.ts >= prev.ts:
                                self._latest[obs.vehicle_id] = obs
            except Exception as exc:  # reconnect on any transport error
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnects += 1
                await asyncio.sleep(5)

    def fetch(self) -> list[Observation]:
        with self._lock:
            return list(self._latest.values())

    def close(self) -> None:
        self._stop.set()


def default_feeds() -> list[Feed]:
    return [
        GtfsRtFeed("bbb", "bbb", "https://cleverapi.bigbluebus.com/gtfsrt/vehicles"),
        GtfsRtFeed("ccb", "ccb", "https://nextccbus.org/gtfsrt/vehicles"),
        GtfsRtFeed("ladot", "ladot", "https://ladotbus.com/gtfs-rt/vehiclepositions"),
        MetroWebSocketFeed(),
    ]

