"""python -m cameras run | once. Serial sampling of the configured camera subset."""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from cameras.config import Camera, CameraEntry, load_cameras, load_subset
from cameras.sample import Detector, Sample, sample_camera
from cameras.snapshot import CameraStore, nearby_detector_readings, write_cameras
from cameras.speed import Calibration, load_calibration

ROOT = Path(__file__).resolve().parent.parent
_stop = False


def _on_sigint(signum: int, frame: object) -> None:
    global _stop
    if _stop:
        raise KeyboardInterrupt
    _stop = True
    print("Ctrl-C: finishing the open camera, then exiting (press again to abort)", file=sys.stderr, flush=True)


def stopping() -> bool:
    return _stop


def calibration_for(entry: CameraEntry) -> Calibration | None:
    if not entry.calibration:
        return None
    return load_calibration(ROOT / "cameras" / "calibration" / entry.calibration)


def describe(cam: Camera, s: Sample) -> str:
    if s.result is None:
        return f"  {cam.cctv_id:>5} {cam.route:<6} {s.status:<12} {s.error}"
    r = s.result
    return (f"  {cam.cctv_id:>5} {cam.route:<6} {s.status:<12} {s.resolution} {s.fps} fps {s.frames} f {s.duration_s} s "
            f"open {s.open_s} s decode {s.decode_s} s infer {s.infer_s} s | A {r.a.count} ({r.a.vehicles_per_min}/min) "
            f"B {r.b.count} ({r.b.vehicles_per_min}/min) tracks {r.tracks} density {r.density_all}")


def run(args: argparse.Namespace) -> int:
    subset = load_subset(args.config)
    entries = [e for e in subset.cameras if not args.cctv or e.cctv_id in args.cctv]
    if not entries:
        print("no cameras selected", file=sys.stderr)
        return 2
    cams_by_id = load_cameras(args.base_dir / "cameras.gpkg", [e.cctv_id for e in entries])
    cams = [cams_by_id[e.cctv_id] for e in entries]
    det = Detector(args.weights, device=args.device, imgsz=args.imgsz)
    print(f"warm-up {det.warm_up():.1f} s; model {det.name} ultralytics {det.version} on {det.device}, imgsz {det.imgsz}", flush=True)
    store = CameraStore(args.history)
    samples: dict[int, Sample] = {}
    sheets = set(args.sheets)
    signal.signal(signal.SIGINT, _on_sigint)
    rounds_done = 0
    try:
        while not _stop:
            t_round = time.time()
            print(f"round {rounds_done + 1} at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", flush=True)
            for cam, entry in zip(cams, entries):
                if _stop:
                    break
                sheet = (args.sheet_dir / f"cctv-{cam.cctv_id}-sheet.jpg") if cam.cctv_id in sheets else None
                s = sample_camera(cam, entry, subset.default_line, det, args.seconds,
                                  calibration=calibration_for(entry), sheet_path=sheet, stop=stopping)
                samples[cam.cctv_id] = s
                readings = nearby_detector_readings(args.detectors, cam)
                store.write(s, readings)
                write_cameras(args.live_dir / "cameras.geojson", cams, samples, int(time.time()),
                              f"{args.seconds} s samples, one camera at a time, a round every {args.every} s")
                print(describe(cam, s) + (f" | detectors {[(d.station_id, d.match, d.volume_30s) for d in readings]}" if readings else ""), flush=True)
            rounds_done += 1
            print(f"round {rounds_done} took {time.time() - t_round:.1f} s", flush=True)
            if args.rounds and rounds_done >= args.rounds:
                break
            while not _stop and time.time() < t_round + args.every:
                time.sleep(1)
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
    finally:
        write_cameras(args.live_dir / "cameras.geojson", cams, samples, int(time.time()), "sampler stopped")
        store.close()
    return 0


def preview(args: argparse.Namespace) -> int:
    """One frame per subset camera with its counting line drawn, to place lines by eye. One stream open per camera."""
    import cv2
    from cameras.counting import Line
    from cameras.stream import StreamError, DecodeError, read_clip
    subset = load_subset(args.config)
    cams = load_cameras(args.base_dir / "cameras.gpkg", [e.cctv_id for e in subset.cameras])
    args.out.mkdir(parents=True, exist_ok=True)
    for e in subset.cameras:
        cam = cams[e.cctv_id]
        if not cam.stream_url:
            print(f"{cam.cctv_id}: no stream URL")
            continue
        try:
            clip = read_clip(cam.stream_url, 0.5)
        except (StreamError, DecodeError) as err:
            print(f"{cam.cctv_id}: {err}")
            continue
        img = clip.frames[-1]
        line = Line.from_fractions([list(p) for p in (e.line or subset.default_line)], clip.width, clip.height)
        cv2.line(img, (int(line.p0[0]), int(line.p0[1])), (int(line.p1[0]), int(line.p1[1])), (0, 220, 255), 2)
        for frac in (0.3, 0.4, 0.5, 0.6, 0.7):
            y = int(frac * clip.height)
            cv2.line(img, (0, y), (40, y), (255, 255, 255), 1)
            cv2.putText(img, f"{frac:.1f}", (44, y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        out = args.out / f"cctv-{cam.cctv_id}.jpg"
        cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        print(f"{cam.cctv_id}: {clip.width}x{clip.height} -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m cameras")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("preview", help="save one frame per camera with the counting line drawn")
    v.add_argument("--config", type=Path, default=ROOT / "cameras" / "subset.json")
    v.add_argument("--base-dir", type=Path, default=ROOT / "data" / "base")
    v.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run", help="sample the subset in rounds")
    r.add_argument("--config", type=Path, default=ROOT / "cameras" / "subset.json")
    r.add_argument("--base-dir", type=Path, default=ROOT / "data" / "base")
    r.add_argument("--live-dir", type=Path, default=ROOT / "live")
    r.add_argument("--history", type=Path, default=ROOT / "history" / "cameras.sqlite")
    r.add_argument("--detectors", type=Path, default=ROOT / "live" / "detectors.geojson", help="the poller's detector snapshot, read only")
    r.add_argument("--weights", type=Path, default=ROOT / "data" / "models" / "yolo11s.pt")
    r.add_argument("--device", default="mps")
    r.add_argument("--imgsz", type=int, default=960)
    r.add_argument("--seconds", type=float, default=15.0)
    r.add_argument("--every", type=float, default=300.0, help="seconds between round starts")
    r.add_argument("--rounds", type=int, default=0, help="stop after this many rounds (0 = until Ctrl-C)")
    r.add_argument("--cctv", type=int, nargs="*", default=[], help="only these cameras from the config")
    r.add_argument("--sheets", type=int, nargs="*", default=[], help="write a contact sheet for these cameras each round")
    r.add_argument("--sheet-dir", type=Path, default=ROOT / "docs" / "img")
    args = p.parse_args(argv)
    return preview(args) if args.cmd == "preview" else run(args)


if __name__ == "__main__":
    sys.exit(main())
