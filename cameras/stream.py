"""Decode a short clip from a Caltrans HLS stream with PyAV (bundled FFmpeg)."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import av
from av.error import FFmpegError
import numpy as np

from poller.feeds import USER_AGENT


class StreamError(Exception):
    """The playlist could not be opened (no stream)."""


class DecodeError(Exception):
    """The stream opened but no usable frames came out."""


@dataclass
class Clip:
    width: int
    height: int
    fps_nominal: float | None
    open_s: float
    decode_s: float
    times: list[float] = field(default_factory=list)
    frames: list[np.ndarray] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.times[-1] - self.times[0] if len(self.times) > 1 else 0.0

    @property
    def fps_measured(self) -> float | None:
        if len(self.times) < 2:
            return None
        return round(float(np.median(np.diff(np.array(self.times))) ** -1), 2)


def read_clip(url: str, seconds: float, stop: Callable[[], bool] = lambda: False, timeout_s: float = 10.0) -> Clip:
    t0 = time.time()
    try:
        container = av.open(url, options={"user_agent": USER_AGENT}, timeout=(timeout_s, timeout_s))
    except (FFmpegError, OSError, ValueError) as e:
        raise StreamError(f"{type(e).__name__}: {e}") from e
    open_s = time.time() - t0
    try:
        if not container.streams.video:
            raise DecodeError("no video stream in the playlist")
        vs = container.streams.video[0]
        vs.thread_type = "AUTO"
        rate = vs.base_rate or vs.average_rate
        clip = Clip(width=vs.width, height=vs.height, fps_nominal=float(rate) if rate else None, open_s=round(open_s, 2), decode_s=0.0)
        first_pts: int | None = None
        t1 = time.time()
        try:
            for frame in container.decode(vs):
                if stop():
                    break
                if frame.pts is None:
                    continue
                if first_pts is None:
                    first_pts = frame.pts
                t = float((frame.pts - first_pts) * vs.time_base)
                clip.times.append(t)
                clip.frames.append(frame.to_ndarray(format="bgr24"))
                if t >= seconds:
                    break
        except FFmpegError as e:
            if not clip.frames:
                raise DecodeError(f"{type(e).__name__}: {e}") from e
        clip.decode_s = round(time.time() - t1, 2)
        if len(clip.frames) < 2:
            raise DecodeError("fewer than two frames decoded")
        return clip
    finally:
        container.close()
