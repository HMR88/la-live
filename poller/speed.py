"""Turn consecutive positions of one vehicle into segment speeds.

Rules (docs/plan.md 3.2 and the step 3 brief): both positions are projected
onto the vehicle's shape; the pair is dropped when the time gap exceeds
MAX_GAP_S, either point sits within STOP_RADIUS_M (along the shape) of a stop,
the along-shape distance is under MIN_ALONG_M, the vehicle moved backwards, a
point is further than MAX_OFF_SHAPE_M from the shape, or the implied speed
exceeds MAX_SPEED_KMH. Otherwise the speed is charged to every segment the
pair covered, weighted by the metres covered on that segment.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Point

from poller.base import BaseData
from poller.model import TO_UTM, Observation, PairResult, SegmentSpeed
from poller.shape_index import ShapeIndex

MAX_GAP_S = 120
STOP_RADIUS_M = 30.0
MIN_ALONG_M = 20.0
MAX_SPEED_KMH = 110.0
MAX_OFF_SHAPE_M = 50.0


@dataclass
class VehicleState:
    last: Observation
    shape_key: str | None  # chosen shape, kept while trip_id is unchanged
    speed_kmh: float | None  # last kept speed, for the vehicles snapshot


class SpeedEngine:
    def __init__(self, base: BaseData, index: ShapeIndex) -> None:
        self.base = base
        self.index = index
        self.state: dict[tuple[str, str], VehicleState] = {}

    def seed(self, observations: list[Observation]) -> None:
        """Restore the last known position per vehicle (after a restart)."""
        for o in observations:
            key = (o.agency, o.vehicle_id)
            cur = self.state.get(key)
            if cur is None or o.ts > cur.last.ts:
                self.state[key] = VehicleState(o, None, None)

    def ingest(self, obs: Observation) -> tuple[PairResult | None, list[SegmentSpeed]]:
        """Feed one observation. Returns the pair outcome (None if not a new position)."""
        key = (obs.agency, obs.vehicle_id)
        prev = self.state.get(key)
        if prev is None:
            self.state[key] = VehicleState(obs, None, None)
            return None, []
        if obs.ts <= prev.last.ts:
            return None, []
        shape_key = prev.shape_key if prev.last.trip_id == obs.trip_id and prev.shape_key else None
        result, rows, shape_key = self._pair(prev.last, obs, shape_key)
        kept = result.reason == "kept"
        self.state[key] = VehicleState(obs, shape_key if kept else prev.shape_key, result.speed_kmh if kept else prev.speed_kmh)
        return result, rows

    def _pair(self, a: Observation, b: Observation, shape_key: str | None
              ) -> tuple[PairResult, list[SegmentSpeed], str | None]:
        gap = b.ts - a.ts

        def drop(reason: str, speed: float | None = None, dist: float | None = None) -> tuple[PairResult, list[SegmentSpeed], str | None]:
            return PairResult(b.agency, b.vehicle_id, a.ts, b.ts, reason, shape_key, speed, dist), [], shape_key

        if gap > MAX_GAP_S:
            return drop("gap")
        pa = Point(TO_UTM.transform(a.lon, a.lat))
        pb = Point(TO_UTM.transform(b.lon, b.lat))
        if shape_key is None:
            shape_key = self._choose_shape(b, pa, pb)
            if shape_key is None:
                return drop("no_shape")
        shape = self.base.shapes[shape_key]
        mapping = self.index.mappings.get(shape_key)
        if mapping is None or not mapping.segments:
            return drop("unmapped_shape")
        if max(shape.geom.distance(pa), shape.geom.distance(pb)) > MAX_OFF_SHAPE_M:
            return drop("off_shape")
        d0, d1 = shape.geom.project(pa), shape.geom.project(pb)
        along = d1 - d0
        if along < 0 and along < -MIN_ALONG_M:
            return drop("backwards", dist=along)
        if along < MIN_ALONG_M:
            return drop("short", dist=along)
        if any(abs(s - d0) < STOP_RADIUS_M or abs(s - d1) < STOP_RADIUS_M for s in mapping.stops_m):
            return drop("dwell", dist=along)
        speed = along / gap * 3.6
        if speed > MAX_SPEED_KMH:
            return drop("too_fast", speed=speed, dist=along)
        rows = [
            SegmentSpeed(b.ts, sid, "bus", speed, min(to_m, d1) - max(from_m, d0), b.vehicle_id, b.agency)
            for sid, from_m, to_m in mapping.segments
            if min(to_m, d1) - max(from_m, d0) > 0
        ]
        if not rows:
            return drop("no_segment", speed=speed, dist=along)
        return PairResult(b.agency, b.vehicle_id, a.ts, b.ts, "kept", shape_key, speed, along), rows, shape_key

    def _choose_shape(self, obs: Observation, pa: Point, pb: Point) -> str | None:
        """The candidate shape both points lie closest to, preferring one the
        vehicle travels forward along (both directions of a route are candidates
        when the trip is unknown, as for Big Blue Bus)."""
        best: tuple[int, float, str] | None = None
        for key in self.base.candidate_shapes(obs.agency, obs.trip_id, obs.route_id):
            geom = self.base.shapes[key].geom
            d = max(geom.distance(pa), geom.distance(pb))
            if d > MAX_OFF_SHAPE_M:
                continue
            backwards = 1 if geom.project(pb) < geom.project(pa) else 0
            if best is None or (backwards, d) < best[:2]:
                best = (backwards, d, key)
        return best[2] if best is not None else None
