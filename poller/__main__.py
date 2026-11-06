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
from poller.snapshot import write_detectors, write_events, write_segments, write_status, write_vehicles
from poller.speed import SpeedEngine
from poller.store import Store
from poller.traffic import AtmsSnapshot, default_sources, in_bbox

WINDOW_S = 600


def detector_rows(base: BaseData, atms: AtmsSnapshot) -> list[SegmentSpeed]:
    """Valid detector readings on stations that have a segment, one row each.
    ts is the file's Created time, so an unchanged file adds nothing."""
    out: list[SegmentSpeed] = []
    for sid, r in atms.readings.items():
        d = base.detectors.get(sid)
        if d is None or d.segment_id is None or not r.valid:
            continue
        out.append(SegmentSpeed(atms.created_ts, d.segment_id, "vds", r.speed_kmh, 1.0, str(sid), "caltrans"))
    return out


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
    atms_src, chp_src, lcs_src, cms_src = default_sources()
    traffic_counts: dict[str, Any] = {}
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
        atms = atms_src.poll(now)
        if atms is not None:
            vds_rows = detector_rows(base, atms)
            rows.extend(vds_rows)
            traffic_counts["atms"] = {"created_ts": atms.created_ts, "age_s": now - atms.created_ts, "stations_in_feed": len(atms.readings),
                                      "valid_readings": sum(1 for r in atms.readings.values() if r.valid),
                                      "corridor_stations_with_speed": sum(1 for sid, r in atms.readings.items() if r.valid and sid in base.detectors),
                                      "rows_to_segments": len(vds_rows), "note": "deprecated feed, still live"}
        incidents = [i for i in (chp_src.poll(now) or []) if in_bbox(i.lon, i.lat)]
        closures = [c for c in (lcs_src.poll(now) or []) if c.is_current(now) and in_bbox(c.lon0, c.lat0) and in_bbox(c.lon1, c.lat1)]
        signs = [sg for sg in (cms_src.poll(now) or []) if sg.in_service and in_bbox(sg.lon, sg.lat)]
        store.write(positions, pairs, rows)
        cycles += 1
        n_veh = write_vehicles(live_dir / "vehicles.geojson", engine.state, now, labels)
        traffic_counts["events"] = write_events(live_dir / "events.geojson", incidents, closures, signs, now)
        traffic_counts["detectors"] = write_detectors(live_dir / "detectors.geojson", base, atms, now)
        n_seg = write_segments(live_dir / "segments.geojson", base, store.segment_speeds(now - WINDOW_S), now)
        status: dict[str, Any] = {
            "generated": _iso(now), "cycle": cycles, "interval_s": interval,
            "feeds": feed_status, "vehicles_in_snapshot": n_veh, "segments_in_snapshot": n_seg,
            "traffic_sources": {src.name: src.status(now) for src in (atms_src, chp_src, lcs_src, cms_src)},
            "traffic": traffic_counts,
            "pairs_by_reason": dict(reasons),
            "vehicles_seen": {a: len(v) for a, v in per_agency_vehicles.items()},
        }
        write_status(live_dir / "status.json", status)
        print(f"cycle {cycles} {_iso(now)}: new positions {len(positions)}, kept {reasons['kept']}, "
              f"segments {n_seg}, vehicles {n_veh}, vds rows {len([r for r in rows if r.source == 'vds'])}, "
              f"events {traffic_counts.get('events')}, reasons {dict(reasons)}", flush=True)
        if time.monotonic() >= deadline:
            break
        time.sleep(max(0.0, interval - (time.monotonic() - t0)))
    for feed in feeds:
        feed.close()
    for src in (atms_src, chp_src, lcs_src, cms_src):
        src.client.close()
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
