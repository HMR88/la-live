"""Summarise a poller run: shapes mapped, vehicles per agency, pairs kept and
dropped by reason, and the speed distribution on Wilshire against the corridor.

Usage: .venv/bin/python prep/step3_report.py [--history history/observations.sqlite]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from pathlib import Path

from poller.base import load_base
from poller.shape_index import ShapeIndex


def quantiles(values: list[float]) -> str:
    if len(values) < 2:
        return f"n={len(values)}"
    q = statistics.quantiles(values, n=10)
    return f"n={len(values)} p10={q[0]:.1f} p50={q[4]:.1f} p90={q[8]:.1f} mean={statistics.mean(values):.1f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path, default=Path("history/observations.sqlite"))
    ap.add_argument("--base-dir", type=Path, default=Path("data/base"))
    ap.add_argument("--live-dir", type=Path, default=Path("live"))
    args = ap.parse_args()

    idx = ShapeIndex.load(args.base_dir / "shape_index.json")
    print("shape index:", idx.summary())
    base = load_base(args.base_dir)
    conn = sqlite3.connect(args.history)

    lo, hi = conn.execute("SELECT MIN(ts), MAX(ts) FROM positions").fetchone()
    print(f"positions from {lo} to {hi} ({(hi - lo) / 60:.0f} min)")
    print("vehicles per agency:", dict(conn.execute("SELECT agency, COUNT(DISTINCT vehicle_id) FROM positions GROUP BY agency")))
    print("positions per agency:", dict(conn.execute("SELECT agency, COUNT(*) FROM positions GROUP BY agency")))
    print("pairs by reason:", dict(conn.execute("SELECT reason, COUNT(*) FROM pairs GROUP BY reason ORDER BY COUNT(*) DESC")))
    for agency, in conn.execute("SELECT DISTINCT agency FROM pairs"):
        print(f"  {agency}:", dict(conn.execute("SELECT reason, COUNT(*) FROM pairs WHERE agency = ? GROUP BY reason", (agency,))))
    print("observation rows:", conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
          "distinct segments:", conn.execute("SELECT COUNT(DISTINCT segment_id) FROM observations").fetchone()[0])

    rows = conn.execute("SELECT segment_id, speed_kmh, weight FROM observations").fetchall()
    wilshire = {sid for sid, s in base.segments.items() if s.name and "wilshire" in s.name.lower()}
    corridor = [float(v) for _, v, _ in rows]
    on_wilshire = [float(v) for sid, v, _ in rows if sid in wilshire]
    print("speed kmh, all corridor observations:", quantiles(corridor))
    print("speed kmh, Wilshire Boulevard segments:", quantiles(on_wilshire))
    kept = [float(v) for v, in conn.execute("SELECT speed_kmh FROM pairs WHERE reason = 'kept'")]
    print("speed kmh per kept pair:", quantiles(kept))
    by_hw: dict[str, list[float]] = {}
    for sid, v, _ in rows:
        by_hw.setdefault(base.segments[sid].highway, []).append(float(v))
    for hw, vals in sorted(by_hw.items(), key=lambda kv: -len(kv[1]))[:6]:
        print(f"  {hw}: {quantiles(vals)}")

    status = json.loads((args.live_dir / "status.json").read_text())
    seg = json.loads((args.live_dir / "segments.geojson").read_text())
    veh = json.loads((args.live_dir / "vehicles.geojson").read_text())
    print("status.json feeds:", {k: {kk: vv for kk, vv in v.items() if kk in ("vehicles", "in_corridor", "errors", "reconnects")} for k, v in status["feeds"].items()})
    print(f"segments.geojson: {len(seg['features'])} features, generated {seg['generated']}; vehicles.geojson: {len(veh['features'])} features")
    wil = [f["properties"]["speed_kmh"] for f in seg["features"] if f["properties"]["name"] and "wilshire" in f["properties"]["name"].lower()]
    print("snapshot Wilshire segments:", quantiles([float(v) for v in wil]))


if __name__ == "__main__":
    main()
