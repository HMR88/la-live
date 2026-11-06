"""Run the poller.

  python -m poller build-index          map every shape with Valhalla, write data/base/shape_index.json
  python -m poller run --minutes 20     poll feeds every 30 s, write live/ and history/
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from poller.base import BaseData, load_base
from poller.feeds import Feed, MetroWebSocketFeed, default_feeds
from poller.model import Observation, PairResult, SegmentSpeed
from poller.shape_index import ShapeIndex, build_index
from poller.snapshot import write_segments, write_status, write_vehicles
from poller.speed import SpeedEngine
from poller.store import Store

WINDOW_S = 600


def route_labels(base: BaseData) -> dict[tuple[str, str], str]:
    """(agency, feed route id) -> short name for the vehicles snapshot."""
    out: dict[tuple[str, str], str] = {}
    for sh in base.shapes.values():
        out[(sh.agency, sh.route_id)] = sh.route_short_name or sh.route_id
        out[(sh.agency, sh.route_short_name)] = sh.route_short_name
    return out


def run(minutes: float, interval: float, base_dir: Path, live_dir: Path, history: Path, feeds: list[Feed]) -> dict[str, Any]:
    base = load_base(base_dir)
    index = ShapeIndex.load(base_dir / "shape_index.json")
    engine = SpeedEngine(base, index)
    store = Store(history)
    engine.seed(store.latest_positions())
    labels = route_labels(base)
    print(f"base: {len(base.segments)} segments, {len(base.shapes)} shapes; index {index.summary()}; "
          f"seeded {len(engine.state)} vehicles from history", flush=True)
    feed_status: dict[str, dict[str, Any]] = {f.name: {"errors": 0, "last_error": None} for f in feeds}
    reasons: Counter[str] = Counter()
    per_agency_vehicles: dict[str, set[str]] = {}
    cycles = 0
    deadline = time.monotonic() + minutes * 60
    while True:
        t0 = time.monotonic()
        now = int(time.time())
        positions: list[Observation] = []
        pairs: list[PairResult] = []
        rows: list[SegmentSpeed] = []
        for feed in feeds:
            st = feed_status[feed.name]
            try:
                obs = feed.fetch()
            except Exception as exc:  # a dead feed must not stop the others
                st["errors"] += 1
                st["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
                continue
            inside = [o for o in obs if o.in_corridor()]
            st.update({"last_fetch_utc": _iso(now), "vehicles": len(obs), "in_corridor": len(inside)})
            if isinstance(feed, MetroWebSocketFeed):
                st.update({"messages": feed.messages, "reconnects": feed.reconnects, "ws_error": feed.last_error})
            for o in inside:
                per_agency_vehicles.setdefault(o.agency, set()).add(o.vehicle_id)
                result, seg_rows = engine.ingest(o)
                if result is None:
                    continue
                positions.append(o)
                pairs.append(result)
                reasons[result.reason] += 1
                rows.extend(seg_rows)
        store.write(positions, pairs, rows)
        cycles += 1
        n_veh = write_vehicles(live_dir / "vehicles.geojson", engine.state, now, labels)
        n_seg = write_segments(live_dir / "segments.geojson", base, store.segment_speeds(now - WINDOW_S), now)
        status: dict[str, Any] = {
            "generated": _iso(now), "cycle": cycles, "interval_s": interval,
            "feeds": feed_status, "vehicles_in_snapshot": n_veh, "segments_in_snapshot": n_seg,
            "pairs_by_reason": dict(reasons),
            "vehicles_seen": {a: len(v) for a, v in per_agency_vehicles.items()},
        }
        write_status(live_dir / "status.json", status)
        print(f"cycle {cycles} {_iso(now)}: new positions {len(positions)}, kept {reasons['kept']}, "
              f"segments {n_seg}, vehicles {n_veh}, reasons {dict(reasons)}", flush=True)
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))
    for feed in feeds:
        feed.close()
    store.close()
    return status


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-dir", type=Path, default=Path("data/base"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-index")
    b.add_argument("--valhalla", default="http://localhost:8002")
    r = sub.add_parser("run")
    r.add_argument("--minutes", type=float, default=20)
    r.add_argument("--interval", type=float, default=30)
    r.add_argument("--live-dir", type=Path, default=Path("live"))
    r.add_argument("--history", type=Path, default=Path("history/observations.sqlite"))
    args = ap.parse_args()
    if args.cmd == "build-index":
        base = load_base(args.base_dir)
        idx = build_index(base, valhalla=args.valhalla)
        idx.save(args.base_dir / "shape_index.json")
        print(idx.summary())
    else:
        run(args.minutes, args.interval, args.base_dir, args.live_dir, args.history, default_feeds())


if __name__ == "__main__":
    main()
