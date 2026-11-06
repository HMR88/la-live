"""Compare bus-derived speeds with Caltrans detector speeds.

Strict: freeway segments that carry both a detector reading (source vds) and
bus rows in the same 10-minute window; paired difference bus minus detector.
Relaxed: bus rows on motorway segments within RADIUS_M of a reporting
station, paired with that station's readings in the same window.

Usage: .venv/bin/python prep/step4_compare.py [--since EPOCH]
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Point
from shapely.strtree import STRtree

from poller.base import load_base
from poller.model import TO_UTM

WINDOW_S = 600
RADIUS_M = 300.0


def describe(diffs: list[float]) -> str:
    if not diffs:
        return "n=0"
    if len(diffs) < 3:
        return f"n={len(diffs)} values={[round(d, 1) for d in diffs]}"
    q = statistics.quantiles(diffs, n=10)
    return f"n={len(diffs)} median={statistics.median(diffs):.1f} p10={q[0]:.1f} p90={q[8]:.1f} mean={statistics.mean(diffs):.1f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path, default=Path("history/observations.sqlite"))
    ap.add_argument("--since", type=int, default=0)
    args = ap.parse_args()
    base = load_base()
    conn = sqlite3.connect(args.history)
    rows = conn.execute("SELECT ts, segment_id, source, speed_kmh, weight FROM observations WHERE ts >= ?", (args.since,)).fetchall()
    vds = [(int(t), int(s), float(v)) for t, s, src, v, w in rows if src == "vds"]
    bus = [(int(t), int(s), float(v), float(w)) for t, s, src, v, w in rows if src == "bus"]
    print(f"rows since {args.since}: vds {len(vds)} on {len({s for _, s, _ in vds})} segments; bus {len(bus)}")

    # strict: same segment, same window
    vds_win: dict[tuple[int, int], list[float]] = defaultdict(list)
    for t, s, v in vds:
        vds_win[(s, t // WINDOW_S)].append(v)
    bus_win: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for t, s, v, w in bus:
        bus_win[(s, t // WINDOW_S)].append((v, w))
    strict: list[float] = []
    for key, bs in bus_win.items():
        if key in vds_win:
            b = sum(v * w for v, w in bs) / sum(w for _, w in bs)
            strict.append(b - statistics.mean(vds_win[key]))
    print("strict (same segment, same 10-minute window), bus minus detector km/h:", describe(strict))

    # relaxed: bus rows on motorway segments within RADIUS_M of a reporting station
    stations = {sid for sid, d in base.detectors.items() if d.segment_id is not None}
    reporting = sorted({int(v) for v, in conn.execute("SELECT DISTINCT vehicle_id FROM observations WHERE source='vds' AND ts >= ?", (args.since,))})
    pts = [Point(TO_UTM.transform(base.detectors[s].lon, base.detectors[s].lat)) for s in reporting if s in stations]
    ids = [s for s in reporting if s in stations]
    tree = STRtree(pts) if pts else None
    seg_station: dict[int, int] = {}
    for sid, seg in base.segments.items():
        if tree is None or not seg.highway.startswith("motorway"):
            continue
        hits = tree.query(seg.geom.buffer(RADIUS_M), predicate="intersects")
        if len(hits):
            seg_station[sid] = ids[int(hits[0])]
    station_win: dict[tuple[int, int], list[float]] = defaultdict(list)
    for t, s, v in vds:
        station = next((st for st in ids if base.detectors[st].segment_id == s), None)
        if station is not None:
            station_win[(station, t // WINDOW_S)].append(v)
    relaxed: list[float] = []
    relaxed_pairs: list[tuple[int, float, float]] = []
    for (s, win), bs in bus_win.items():
        st = seg_station.get(s)
        if st is None or (st, win) not in station_win:
            continue
        b = sum(v * w for v, w in bs) / sum(w for _, w in bs)
        d = statistics.mean(station_win[(st, win)])
        relaxed.append(b - d)
        relaxed_pairs.append((st, b, d))
    print(f"relaxed (bus on motorway segments within {RADIUS_M:.0f} m of a reporting station, same window):", describe(relaxed))
    by_station: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for st, b, d in relaxed_pairs:
        by_station[st].append((b, d))
    for st, pairs in sorted(by_station.items()):
        det = base.detectors[st]
        print(f"  station {st} {det.route} {det.direction} {det.name}: n={len(pairs)} bus mean {statistics.mean(b for b, _ in pairs):.1f} detector mean {statistics.mean(d for _, d in pairs):.1f}")
    if len(relaxed) >= 3:
        bs = [b for _, b, _ in relaxed_pairs]
        ds = [d for _, _, d in relaxed_pairs]
        if statistics.pstdev(bs) > 0 and statistics.pstdev(ds) > 0:
            print(f"  correlation bus vs detector across pairs: {statistics.correlation(bs, ds):.2f}")


if __name__ == "__main__":
    main()
