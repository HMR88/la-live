"""Speed from tracks through a four-point ground calibration. Shipped, tested, used by no camera yet."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import cv2
import numpy as np
from pydantic import BaseModel, Field

from cameras.counting import Track

MIN_SPEED_TRACK_S = 1.0


class CalibrationPoint(BaseModel):
    px: tuple[float, float]
    ground: tuple[float, float]


class CalibrationFile(BaseModel):
    crs: str = Field(pattern=r"^EPSG:26911$")
    points: list[CalibrationPoint] = Field(min_length=4, max_length=4)


@dataclass
class Calibration:
    crs: str
    homography: np.ndarray

    @staticmethod
    def from_points(points: list[CalibrationPoint], crs: str = "EPSG:26911") -> "Calibration":
        src = np.array([p.px for p in points], dtype=np.float32)
        dst = np.array([p.ground for p in points], dtype=np.float32)
        return Calibration(crs=crs, homography=cv2.getPerspectiveTransform(src, dst))

    def to_ground(self, xy: np.ndarray) -> np.ndarray:
        pts = np.asarray(xy, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.homography).reshape(-1, 2)


def load_calibration(path: Path) -> Calibration:
    data = CalibrationFile.model_validate(json.loads(path.read_text()))
    return Calibration.from_points(data.points, data.crs)


def track_speed_kmh(cal: Calibration, track: Track) -> float | None:
    if track.duration_s < MIN_SPEED_TRACK_S:
        return None
    ground = cal.to_ground(np.array([(x, y) for _, x, y in track.points]))
    metres = float(np.sum(np.linalg.norm(np.diff(ground, axis=0), axis=1)))
    return metres / track.duration_s * 3.6


def direction_speed_kmh(cal: Calibration, tracks: list[Track], direction_by_track: dict[int, int], direction: int) -> float | None:
    speeds = [s for t in tracks if direction_by_track.get(t.track_id) == direction
              for s in [track_speed_kmh(cal, t)] if s is not None]
    return round(median(speeds), 1) if speeds else None
