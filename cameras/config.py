"""Subset config and camera records: the boundary where files are validated."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from pyogrio.raw import read

Fraction = tuple[float, float]


class CameraEntry(BaseModel):
    cctv_id: int
    note: str = ""
    line: list[Fraction] | None = Field(default=None, min_length=2, max_length=2)
    a_label: str = ""
    b_label: str = ""
    calibration: str | None = None


class SubsetFile(BaseModel):
    comment: str = ""
    default_line: list[Fraction] = Field(min_length=2, max_length=2)
    cameras: list[CameraEntry] = Field(min_length=1)


def load_subset(path: Path) -> SubsetFile:
    return SubsetFile.model_validate(json.loads(path.read_text()))


@dataclass(frozen=True)
class Camera:
    cctv_id: int
    name: str
    route: str
    direction: str
    postmile: float | None
    stream_url: str | None
    image_url: str | None
    image_minutes: int | None
    segment_id: int | None
    lon: float
    lat: float

    @property
    def route_number(self) -> str:
        """'I-10' -> '10', matching the ATMS route field."""
        return self.route.split("-", 1)[-1]


def _none_if_nan(v: object) -> float | None:
    if v is None:
        return None
    f = float(v)  # type: ignore[arg-type]
    return None if math.isnan(f) else f


def load_cameras(gpkg: Path, ids: list[int]) -> dict[int, Camera]:
    meta, _, _, fields = read(gpkg)
    names = list(meta["fields"])
    cols = {n: fields[i] for i, n in enumerate(names)}
    out: dict[int, Camera] = {}
    for i in range(len(cols["cctv_id"])):
        cid = int(cols["cctv_id"][i])
        if cid not in ids:
            continue
        seg = _none_if_nan(cols["seg_segment_id"][i])
        out[cid] = Camera(
            cctv_id=cid, name=str(cols["name"][i]), route=str(cols["route"][i]), direction=str(cols["direction"][i]),
            postmile=_none_if_nan(cols["postmile"][i]),
            stream_url=cols["stream_url"][i] or None, image_url=cols["image_url"][i] or None,
            image_minutes=int(cols["image_minutes"][i]) if cols["image_minutes"][i] is not None else None,
            segment_id=int(seg) if seg is not None else None,
            lon=float(cols["lon"][i]), lat=float(cols["lat"][i]))
    missing = [c for c in ids if c not in out]
    if missing:
        raise ValueError(f"cameras not in {gpkg}: {missing}")
    return out
