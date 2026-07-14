"""Event clip recording.

`Recorder.record_event()` is frame-source-agnostic (same design precedent as
`motion.py`): it accepts a caller-supplied pre-roll buffer plus a callable
that yields further live frames, and encodes them all to an MP4 file via an
FFmpeg subprocess fed over stdin. It does *not* call `create_camera()`
itself — per task 3's camera-ownership contract, once streaming is active
the physical camera is owned by MediaMTX, so the normal frame source is the
same RTSP feed task 4's `MotionDetector` reads from, not a second direct
camera open. `create_camera_frame_source()` is provided separately for the
local-dev / streaming-disabled fallback path, where a direct camera open is
valid and its errors are meaningful.
"""

import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Iterator, List, Tuple

import numpy as np

from .camera import CameraBusyError, CameraNotFoundError, create_camera
from .config import CameraConfig, RecordingConfig

logger = logging.getLogger("catcam.recorder")

__all__ = [
    "RecordingError",
    "FfmpegNotFoundError",
    "RecordingFailedError",
    "CameraNotFoundError",
    "CameraBusyError",
    "Recorder",
    "create_camera_frame_source",
]


class RecordingError(Exception):
    """Base class for recorder-specific errors."""


class FfmpegNotFoundError(RecordingError):
    """Raised when the `ffmpeg` executable is not on PATH."""


class RecordingFailedError(RecordingError):
    """Raised when the FFmpeg subprocess exits with a non-zero status."""


def create_camera_frame_source(config: CameraConfig) -> Iterator[np.ndarray]:
    """Yield frames directly from `create_camera()`.

    Only valid when streaming is disabled (local dev/debugging) — per task
    3's camera-ownership contract, `create_camera()` will raise
    `CameraBusyError` (USB, publisher holds the lock) or race the driver
    (CSI, MediaMTX's native source bypasses the lock) whenever
    `catcam-stream.service` is active. Propagates `CameraNotFoundError` /
    `CameraBusyError` to the caller unchanged.
    """
    with create_camera(config) as backend:
        while True:
            yield backend.read_frame()


def _build_ffmpeg_command(
    resolution: Tuple[int, int], fps: float, max_size_bytes: int, output_path: Path
) -> List[str]:
    width, height = resolution
    return [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-fs",
        str(max_size_bytes),
        str(output_path),
    ]


class Recorder:
    """Encodes motion-event clips to disk via FFmpeg."""

    def __init__(self, config: RecordingConfig):
        if shutil.which("ffmpeg") is None:
            raise FfmpegNotFoundError(
                "ffmpeg executable not found on PATH - install ffmpeg "
                "(e.g. 'sudo apt install ffmpeg' on Raspberry Pi OS)."
            )
        self._config = config

    def record_event(
        self,
        pre_roll_frames: List[np.ndarray],
        frame_source: Callable[[], np.ndarray],
        fps: float,
        resolution: Tuple[int, int],
    ) -> Path:
        """Encode a clip of `config.clip_duration_seconds` and return its path.

        `pre_roll_frames` is written first (oldest first), then additional
        frames are pulled from `frame_source()` until the configured
        duration is reached. If `pre_roll_frames` alone already covers more
        than the configured duration, it is truncated to the most recent
        frames that fit.
        """
        tmp_dir = Path(self._config.storage_dir).parent / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        output_path = tmp_dir / f"{uuid.uuid4().hex}.mp4"

        total_frames = max(1, round(self._config.clip_duration_seconds * fps))
        max_size_bytes = self._config.max_clip_size_mb * 1024 * 1024

        if len(pre_roll_frames) > total_frames:
            logger.info(
                "Pre-roll buffer (%d frames) exceeds clip duration (%d frames); "
                "using the most recent %d pre-roll frames",
                len(pre_roll_frames),
                total_frames,
                total_frames,
            )
            pre_roll_frames = pre_roll_frames[-total_frames:]

        command = _build_ffmpeg_command(resolution, fps, max_size_bytes, output_path)
        logger.info("Starting recording: %s (target %d frames)", output_path, total_frames)

        proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        truncated = False
        try:
            assert proc.stdin is not None
            for frame in pre_roll_frames:
                proc.stdin.write(frame.tobytes())
            remaining = total_frames - len(pre_roll_frames)
            for _ in range(max(0, remaining)):
                frame = frame_source()
                proc.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            # ffmpeg's -fs cap was hit and it stopped reading/exited on its
            # own — this is the expected way the size cap gets enforced, not
            # a failure. Fall through to the exit-code check below.
            truncated = True
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        _, stderr = proc.communicate()
        if proc.returncode != 0:
            stderr_tail = stderr.decode(errors="replace")[-2000:] if stderr else ""
            raise RecordingFailedError(
                f"ffmpeg exited with status {proc.returncode} while recording "
                f"'{output_path}': {stderr_tail}"
            )

        if truncated:
            logger.warning(
                "Recording '%s' was truncated at the configured size cap "
                "(%d MB) before reaching the full requested duration",
                output_path,
                self._config.max_clip_size_mb,
            )
        logger.info("Finished recording: %s", output_path)
        return output_path
