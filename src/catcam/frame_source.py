"""Live frame acquisition shared by the Telegram bot's manual commands
(task 8, `/snapshot` and `/record`) and the continuous motion-detection
pipeline (task 9, `main.py`).

Prefers MediaMTX's RTSP feed (the normal always-on state per task 3's camera
ownership contract - see `docs/streaming.md`); falls back to a direct camera
open via `recorder.create_camera_frame_source()` only when that feed isn't
reachable (streaming stopped / local dev).
"""

import logging
import os
from contextlib import contextmanager
from typing import Callable, Iterator

import cv2
import numpy as np

from .camera import CameraBusyError, CameraNotFoundError
from .config import AppConfig
from .recorder import create_camera_frame_source

logger = logging.getLogger("catcam.frame_source")


class FrameSourceError(Exception):
    """Raised when no live frame could be captured from either source."""


@contextmanager
def live_frame_source(config: AppConfig) -> Iterator[Callable[[], np.ndarray]]:
    """Yield a zero-arg callable returning the next live frame."""
    password = os.environ.get("CATCAM_STREAM_VIEW_PASSWORD", "")
    rtsp_url = (
        f"rtsp://catcam-viewer:{password}@127.0.0.1:"
        f"{config.streaming.rtsp_port}/{config.streaming.path}"
    )
    cap = cv2.VideoCapture(rtsp_url)
    if cap.isOpened():
        def _read_rtsp() -> np.ndarray:
            ok, frame = cap.read()
            if not ok or frame is None:
                raise FrameSourceError("Lost connection to the MediaMTX RTSP feed mid-capture")
            return frame

        try:
            yield _read_rtsp
        finally:
            cap.release()
        return

    cap.release()
    logger.warning(
        "MediaMTX RTSP feed at '%s' unreachable; falling back to a direct "
        "camera open (only valid while streaming is stopped)",
        rtsp_url,
    )
    frame_gen = create_camera_frame_source(config.camera)

    def _read_direct() -> np.ndarray:
        try:
            return next(frame_gen)
        except (CameraNotFoundError, CameraBusyError) as exc:
            raise FrameSourceError(str(exc)) from exc

    try:
        yield _read_direct
    finally:
        frame_gen.close()
