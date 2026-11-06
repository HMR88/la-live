"""Counting-line logic on tracks. Pure, no OpenCV, no torch.

Image coordinates: x right, y down, pixels. A Line is directed p0 -> p1; the side of a point is the sign of
the 2D cross product (line vector x point vector), so on a horizontal left-to-right line the positive side is
the lower part of the frame. Direction A is a crossing from the negative to the positive side, B the reverse.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

Point = tuple[float, float]
TrackPoint = tuple[float, float, float]  # t seconds, x px, y px

A = 1
B = -1
MIN_DIRECTION_PX = 10.0


@dataclass(frozen=True)
class Line:
    p0: Point
    p1: Point

    @staticmethod
    def from_fractions(frac: list[list[float]], width: int, height: int) -> "Line":
        (x0, y0), (x1, y1) = frac
        return Line((x0 * width, y0 * height), (x1 * width, y1 * height))

    def side(self, q: Point) -> float:
        dx, dy = self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]
        return dx * (q[1] - self.p0[1]) - dy * (q[0] - self.p0[0])

    def normal_offset(self, q: Point) -> float:
        """Signed distance of q from the line in pixels, positive on the positive side."""
        dx, dy = self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]
        length = (dx * dx + dy * dy) ** 0.5
        return self.side(q) / length if length else 0.0

    def crossing(self, a: Point, b: Point) -> int:
        """A, B or 0 for the step a -> b. Only a step that meets the segment p0-p1 itself counts."""
        sa, sb = self.side(a), self.side(b)
        if sa == 0 or sb == 0 or (sa > 0) == (sb > 0):
            return 0
        dx, dy = self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]
        ex, ey = b[0] - a[0], b[1] - a[1]
        denom = dx * ey - dy * ex
        if denom == 0:
            return 0
        u = ((a[0] - self.p0[0]) * ey - (a[1] - self.p0[1]) * ex) / denom
        if u < 0 or u > 1:
            return 0
        return A if sa < 0 else B


@dataclass
class Track:
    track_id: int
    points: list[TrackPoint]

    @property
    def duration_s(self) -> float:
        return self.points[-1][0] - self.points[0][0] if len(self.points) > 1 else 0.0

    @property
    def length_px(self) -> float:
        return sum(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                   for (_, x0, y0), (_, x1, y1) in zip(self.points, self.points[1:]))


def first_crossing(line: Line, track: Track) -> int:
    """A, B or 0. One vehicle counts once: later crossings (jitter back and forth) are ignored.
    A point exactly on the line keeps the last side and never counts on its own."""
    last: Point | None = None
    for _, x, y in track.points:
        q = (x, y)
        if line.side(q) == 0:
            continue
        if last is not None:
            c = line.crossing(last, q)
            if c:
                return c
        last = q
    return 0


def track_direction(line: Line, track: Track, min_px: float = MIN_DIRECTION_PX) -> int:
    """A, B or 0 from the displacement along the line normal, for tracks that never reach the line."""
    _, x0, y0 = track.points[0]
    _, x1, y1 = track.points[-1]
    d = line.normal_offset((x1, y1)) - line.normal_offset((x0, y0))
    if abs(d) < min_px:
        return 0
    return A if d > 0 else B


@dataclass
class DirectionStats:
    label: str
    count: int
    vehicles_per_min: float
    flow_px_s: float | None
    density: float


@dataclass
class CountResult:
    a: DirectionStats
    b: DirectionStats
    density_all: float
    tracks: int
    duration_s: float
    frames: int
    crossing_by_track: dict[int, int]
    direction_by_track: dict[int, int]


def count_tracks(line: Line, tracks: list[Track], duration_s: float, frames: int,
                 a_label: str = "A", b_label: str = "B") -> CountResult:
    crossings = {t.track_id: first_crossing(line, t) for t in tracks}
    directions: dict[int, int] = {}
    for t in tracks:
        directions[t.track_id] = crossings[t.track_id] or track_direction(line, t)
    minutes = duration_s / 60 if duration_s > 0 else None

    def stats(d: int, label: str) -> DirectionStats:
        counted = [t for t in tracks if crossings[t.track_id] == d]
        flows = [t.length_px / t.duration_s for t in counted if t.duration_s > 0]
        boxes = sum(len(t.points) for t in tracks if directions[t.track_id] == d)
        return DirectionStats(label=label, count=len(counted),
                              vehicles_per_min=round(len(counted) / minutes, 1) if minutes else 0.0,
                              flow_px_s=round(mean(flows), 1) if flows else None,
                              density=round(boxes / frames, 2) if frames else 0.0)

    total_boxes = sum(len(t.points) for t in tracks)
    return CountResult(a=stats(A, a_label), b=stats(B, b_label),
                       density_all=round(total_boxes / frames, 2) if frames else 0.0,
                       tracks=len(tracks), duration_s=duration_s, frames=frames,
                       crossing_by_track=crossings, direction_by_track=directions)
