"""One sample of one camera: decode, track, count, draw."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from cameras.config import Camera, CameraEntry
from cameras.counting import A, B, CountResult, Line, Track, count_tracks
from cameras.speed import Calibration, direction_speed_kmh
from cameras.stream import Clip, DecodeError, StreamError, read_clip

VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO car, motorcycle, bus, truck
TRACKER = Path(__file__).with_name("bytetrack_cctv.yaml")
MIN_CONF = 0.05


@dataclass(frozen=True)
class Box:
    track_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float


@dataclass
class Sample:
    cctv_id: int
    sampled_at: int
    status: str
    seconds_requested: float
    model: str
    model_version: str
    device: str
    resolution: str | None = None
    fps: float | None = None
    frames: int = 0
    duration_s: float = 0.0
    open_s: float | None = None
    decode_s: float | None = None
    infer_s: float | None = None
    result: CountResult | None = None
    speed_kmh: dict[str, float | None] = field(default_factory=lambda: {"a": None, "b": None})
    speed_status: str = "uncalibrated"
    error: str | None = None

    @property
    def sampled_at_iso(self) -> str:
        return datetime.fromtimestamp(self.sampled_at, timezone.utc).isoformat().replace("+00:00", "Z")


class Detector:
    def __init__(self, weights: Path, device: str = "mps", imgsz: int = 960) -> None:
        import ultralytics
        from ultralytics import YOLO, settings
        settings.update({"sync": False})
        self.model = YOLO(str(weights))
        self.name = weights.stem
        self.version = ultralytics.__version__
        self.device = device
        self.imgsz = imgsz

    def warm_up(self) -> float:
        t0 = time.time()
        self.model.predict(np.zeros((360, 640, 3), dtype=np.uint8), device=self.device, imgsz=self.imgsz, verbose=False)
        return time.time() - t0

    def track_clip(self, clip: Clip) -> tuple[list[Track], list[list[Box]]]:
        tracks: dict[int, Track] = {}
        per_frame: list[list[Box]] = []
        for i, (t, img) in enumerate(zip(clip.times, clip.frames)):
            r = next(iter(self.model.track(img, persist=i > 0, device=self.device, imgsz=self.imgsz, classes=VEHICLE_CLASSES,
                                           conf=MIN_CONF, tracker=str(TRACKER), verbose=False)))
            boxes: list[Box] = []
            if r.boxes is not None and r.boxes.id is not None:
                bx = r.boxes.cpu()
                xyxy = np.asarray(bx.xyxy, dtype=float)
                ids = np.asarray(bx.id, dtype=int)
                confs = np.asarray(bx.conf, dtype=float)
                for (x1, y1, x2, y2), tid, conf in zip(xyxy, ids, confs):
                    tid = int(tid)
                    tracks.setdefault(tid, Track(tid, [])).points.append((t, float((x1 + x2) / 2), float(y2)))
                    boxes.append(Box(tid, float(x1), float(y1), float(x2), float(y2), float(conf)))
            per_frame.append(boxes)
        return list(tracks.values()), per_frame


def sample_camera(cam: Camera, entry: CameraEntry, default_line: list[tuple[float, float]], det: Detector,
                  seconds: float, calibration: Calibration | None = None, sheet_path: Path | None = None,
                  stop=lambda: False) -> Sample:
    s = Sample(cctv_id=cam.cctv_id, sampled_at=int(time.time()), status="ok", seconds_requested=seconds,
               model=det.name, model_version=det.version, device=det.device)
    if not cam.stream_url:
        s.status, s.error = "no_stream", "no stream URL in the Caltrans record"
        return s
    try:
        clip = read_clip(cam.stream_url, seconds, stop=stop)
    except StreamError as e:
        s.status, s.error = "no_stream", str(e)
        return s
    except DecodeError as e:
        s.status, s.error = "decode_error", str(e)
        return s
    s.resolution, s.fps, s.frames = f"{clip.width}x{clip.height}", clip.fps_measured, len(clip.frames)
    s.duration_s, s.open_s, s.decode_s = round(clip.duration_s, 2), clip.open_s, clip.decode_s
    line = Line.from_fractions([list(p) for p in (entry.line or default_line)], clip.width, clip.height)
    t0 = time.time()
    tracks, per_frame = det.track_clip(clip)
    s.infer_s = round(time.time() - t0, 2)
    s.result = count_tracks(line, tracks, clip.duration_s, len(clip.frames), entry.a_label or "A", entry.b_label or "B")
    if calibration is not None:
        s.speed_kmh = {"a": direction_speed_kmh(calibration, tracks, s.result.direction_by_track, A),
                       "b": direction_speed_kmh(calibration, tracks, s.result.direction_by_track, B)}
        s.speed_status = "calibrated"
    if sheet_path is not None:
        contact_sheet(cam, clip, per_frame, line, s, sheet_path)
    return s


COLOUR = {A: (80, 200, 80), B: (230, 140, 40), 0: (160, 160, 160)}  # BGR


def contact_sheet(cam: Camera, clip: Clip, per_frame: list[list[Box]], line: Line, s: Sample, path: Path,
                  cols: int = 4, every_s: float = 1.0, tile_w: int = 640) -> None:
    assert s.result is not None
    scale = tile_w / clip.width
    tile_h = int(round(clip.height * scale))
    picks: list[int] = []
    next_t = 0.0
    for i, t in enumerate(clip.times):
        if t >= next_t:
            picks.append(i)
            next_t += every_s
    rows = (len(picks) + cols - 1) // cols
    header = 70
    sheet = np.full((header + rows * tile_h, cols * tile_w, 3), 255, dtype=np.uint8)
    r = s.result
    title = (f"CCTV {cam.cctv_id} {cam.name}  sampled {s.sampled_at_iso}  {s.duration_s} s, {s.frames} frames, {s.resolution}  "
             f"model {s.model} on {s.device}")
    counts = (f"A ({r.a.label}, neg->pos, green): {r.a.count} crossings = {r.a.vehicles_per_min}/min   "
              f"B ({r.b.label}, pos->neg, orange): {r.b.count} = {r.b.vehicles_per_min}/min   tracks {r.tracks}, density {r.density_all}/frame")
    cv2.putText(sheet, title, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(sheet, counts, (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
    for k, i in enumerate(picks):
        tile = cv2.resize(clip.frames[i], (tile_w, tile_h))
        p0 = (int(line.p0[0] * scale), int(line.p0[1] * scale))
        p1 = (int(line.p1[0] * scale), int(line.p1[1] * scale))
        cv2.line(tile, p0, p1, (0, 220, 255), 2)
        for b in per_frame[i]:
            colour = COLOUR[r.direction_by_track.get(b.track_id, 0)]
            crossed = r.crossing_by_track.get(b.track_id, 0)
            x1, y1, x2, y2 = (int(v * scale) for v in (b.x1, b.y1, b.x2, b.y2))
            cv2.rectangle(tile, (x1, y1), (x2, y2), colour, 2 if crossed else 1)
            cv2.putText(tile, f"{b.track_id}{'*' if crossed else ''}", (x1, max(12, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
        cv2.putText(tile, f"t={clip.times[i]:.1f}s", (8, tile_h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
        y, x = header + (k // cols) * tile_h, (k % cols) * tile_w
        sheet[y:y + tile_h, x:x + tile_w] = tile
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
