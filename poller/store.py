"""SQLite history. Every table has a natural primary key, so re-inserting the
same observation after a restart is a no-op (INSERT OR IGNORE)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from poller.model import Observation, PairResult, SegmentSpeed

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  agency TEXT NOT NULL, vehicle_id TEXT NOT NULL, ts INTEGER NOT NULL,
  lon REAL NOT NULL, lat REAL NOT NULL, trip_id TEXT NOT NULL, route_id TEXT NOT NULL, bearing REAL,
  PRIMARY KEY (agency, vehicle_id, ts));
CREATE TABLE IF NOT EXISTS pairs (
  agency TEXT NOT NULL, vehicle_id TEXT NOT NULL, ts0 INTEGER NOT NULL, ts1 INTEGER NOT NULL,
  reason TEXT NOT NULL, shape_key TEXT, speed_kmh REAL, dist_m REAL,
  PRIMARY KEY (agency, vehicle_id, ts1));
CREATE TABLE IF NOT EXISTS observations (
  ts INTEGER NOT NULL, segment_id INTEGER NOT NULL, source TEXT NOT NULL, speed_kmh REAL NOT NULL,
  weight REAL NOT NULL, vehicle_id TEXT NOT NULL, agency TEXT NOT NULL,
  PRIMARY KEY (agency, vehicle_id, ts, segment_id));
CREATE INDEX IF NOT EXISTS observations_ts ON observations (ts);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def write(self, positions: list[Observation], pairs: list[PairResult], rows: list[SegmentSpeed]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO positions VALUES (?,?,?,?,?,?,?,?)",
                [(p.agency, p.vehicle_id, p.ts, p.lon, p.lat, p.trip_id, p.route_id, p.bearing) for p in positions])
            self.conn.executemany(
                "INSERT OR IGNORE INTO pairs VALUES (?,?,?,?,?,?,?,?)",
                [(p.agency, p.vehicle_id, p.ts0, p.ts1, p.reason, p.shape_id, p.speed_kmh, p.dist_m) for p in pairs])
            self.conn.executemany(
                "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?)",
                [(r.ts, r.segment_id, r.source, r.speed_kmh, r.weight, r.vehicle_id, r.agency) for r in rows])

    def latest_positions(self) -> list[Observation]:
        cur = self.conn.execute(
            "SELECT agency, vehicle_id, ts, lon, lat, trip_id, route_id, bearing FROM positions p "
            "WHERE ts = (SELECT MAX(ts) FROM positions q WHERE q.agency = p.agency AND q.vehicle_id = p.vehicle_id)")
        return [Observation(a, v, trip, route, ts, lon, lat, bearing) for a, v, ts, lon, lat, trip, route, bearing in cur]

    def segment_speeds(self, since_ts: int) -> list[tuple[int, str, float, int, int]]:
        """(segment_id, source, weighted mean speed, n observations, latest ts) since since_ts."""
        cur = self.conn.execute(
            "SELECT segment_id, source, SUM(speed_kmh * weight) / SUM(weight), COUNT(*), MAX(ts) "
            "FROM observations WHERE ts >= ? AND weight > 0 GROUP BY segment_id, source", (since_ts,))
        return [(int(s), str(src), float(v), int(n), int(t)) for s, src, v, n, t in cur]

    def prune(self, now: int, keep_positions_s: int, keep_pairs_s: int, keep_observations_s: int) -> dict[str, int]:
        """Delete rows older than the windows and compact the file. Returns rows deleted per table.
        The unattended job hands the file to the next run through a cache, so it keeps only the seed
        (positions, pairs) and a window of observations; the local mode never calls this."""
        deleted: dict[str, int] = {}
        with self.conn:
            for table, col, keep in (("positions", "ts", keep_positions_s), ("pairs", "ts1", keep_pairs_s), ("observations", "ts", keep_observations_s)):
                deleted[table] = self.conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (now - keep,)).rowcount
        self.conn.execute("VACUUM")
        return deleted

    def close(self) -> None:
        self.conn.close()
