"""Camera counts against ATMS detector volumes, from history/cameras.sqlite.

The sampler stores, next to each camera sample, the ATMS stations that speak for the camera (same segment, or same
route within one postmile) with their volume_30s at sample time. A station's volume_30s times 2 is vehicles per
minute for that station's direction; the camera gives vehicles per minute per side of its line (A, B). Both are
printed as they are, with the mismatch. Nothing here decides which is right.

Run: python prep/step7_compare.py [--history history/cameras.sqlite]
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path, default=ROOT / "history" / "cameras.sqlite")
    args = ap.parse_args()
    con = sqlite3.connect(args.history)
    samples = con.execute("SELECT cctv_id, sampled_at, status, a_count, a_vpm, b_count, b_vpm, duration_s FROM samples ORDER BY sampled_at").fetchall()
    readings = con.execute("SELECT cctv_id, sampled_at, station_id, match, direction, postmile, volume_30s, speed_kmh, ts FROM detector_readings ORDER BY sampled_at").fetchall()
    by_sample: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for r in readings:
        by_sample[(r[0], r[1])].append(r)
    print(f"{len(samples)} camera samples, {len(readings)} detector readings alongside, {sum(1 for s in samples if by_sample.get((s[0], s[1])))} samples with a station")
    print()
    print("| camera | sampled | status | camera A /min | camera B /min | camera both /min | station | match | dir | pm | volume_30s | station /min | station speed km/h | reading age s |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    pairs = 0
    for cid, ts, status, ac, avpm, bc, bvpm, dur in samples:
        rows = by_sample.get((cid, ts), [])
        if not rows:
            continue
        both = round((avpm or 0) + (bvpm or 0), 1) if status == "ok" else None
        for _, _, sid, match, d, pm, vol, spd, rts in rows:
            pairs += 1
            print(f"| {cid} | {iso(ts)} | {status} | {avpm} | {bvpm} | {both} | {sid} | {match} | {d} | {pm} | {vol} | {vol * 2 if vol is not None else None} | {spd} | {ts - rts if rts else None} |")
    print()
    print(f"{pairs} camera-sample by station pairs. Camera directions A and B are sides of the counting line, not compass directions;")
    print("the station direction column is the ATMS one. Compare a camera side with the station of the carriageway it sees, by eye from the contact sheet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
