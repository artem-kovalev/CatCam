"""Camera abstraction layer.

Provides a single, safe interface over both CSI (libcamera/rpicam-apps) and
USB/UVC (V4L2) cameras so that streaming, motion detection, and snapshot code
all go through the same backend and never open the physical device twice in
an incompatible way. A file lock guards every `open()` regardless of backend.

Raspberry Pi OS Bookworm renamed the `libcamera-*` CLI tools to `rpicam-*`
(the old names remain as symlinks but are considered legacy); this module
targets the current `rpicam-*` names. `vcgencmd get_camera` is legacy-stack
only and unreliable on Bookworm, so CSI detection uses
`rpicam-hello --list-cameras` instead.
"""

import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import CameraConfig

try:
    import fcntl
except ImportError:  # pragma: no cover - Raspberry Pi OS / macOS both have fcntl
    fcntl = None


class CameraError(Exception):
    """Base class for camera-related errors."""


class CameraNotFoundError(CameraError):
    """Raised when the configured camera is missing, disconnected, or its
    driver tooling isn't installed."""


class CameraBusyError(CameraError):
    """Raised when another CatCam component already holds the camera lock."""


def _default_lock_path() -> Path:
    runtime_dir = os.environ.get("CATCAM_RUNTIME_DIR", "/run/catcam")
    candidate = Path(runtime_dir) / "camera.lock"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        # /run is not writable (e.g. local development off-device); fall back
        # to a per-user temp directory rather than failing to construct a path.
        fallback = Path(tempfile.gettempdir()) / "catcam" / "camera.lock"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


class CameraLock:
    """Exclusive, non-blocking file lock guarding physical camera access.

    Uses `fcntl.flock`, which locks per open-file-description rather than per
    process, so two `CameraLock` instances on the same path — even within the
    same process — correctly contend with each other.
    """

    def __init__(self, lock_path: Optional[str] = None):
        # Resolution of the *default* path (which has a real filesystem side
        # effect - creating /run/catcam or its fallback, see
        # _default_lock_path()) is deferred to acquire(), not done here.
        # Constructing a CameraBackend (and therefore a CameraLock) happens
        # on every `is_available()`-only availability check too (health.py's
        # `/status` and `scripts/diagnose.sh`) - if that eagerly created
        # /run/catcam, it would be owned by whichever unprivileged user ran
        # the check, not necessarily the `catcam` service user, permanently
        # locking the real service out of writing its own lock file there.
        self._lock_path: Optional[Path] = Path(lock_path) if lock_path else None
        self._fh = None

    def acquire(self) -> None:
        if self._fh is not None:
            return
        if self._lock_path is None:
            self._lock_path = _default_lock_path()
        else:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._lock_path, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise CameraBusyError(
                f"Camera lock '{self._lock_path}' is already held by another "
                "CatCam component (streaming, motion detection, or manual "
                "snapshot/record)."
            ) from exc
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()
        self._fh = None

    def __enter__(self) -> "CameraLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


class CameraBackend(ABC):
    """Common interface implemented by every camera backend."""

    @abstractmethod
    def open(self) -> None:
        """Acquire the camera lock and start capturing frames."""

    @abstractmethod
    def read_frame(self) -> np.ndarray:
        """Return the next frame as a BGR `np.ndarray`."""

    @abstractmethod
    def close(self) -> None:
        """Stop capturing and release the camera lock."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether this backend's camera is currently detected."""

    def __enter__(self) -> "CameraBackend":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


def _split_mjpeg_frames(buffer: bytes) -> Tuple[List[bytes], bytes]:
    """Split a raw MJPEG byte stream into complete JPEG frames.

    Returns `(frames, leftover)` where `leftover` is any trailing partial
    frame (or pre-SOI garbage) to prepend to the next chunk read.
    """
    frames: List[bytes] = []
    search_start = 0
    while True:
        start = buffer.find(_JPEG_SOI, search_start)
        if start == -1:
            return frames, b""
        end = buffer.find(_JPEG_EOI, start + len(_JPEG_SOI))
        if end == -1:
            return frames, buffer[start:]
        end += len(_JPEG_EOI)
        frames.append(buffer[start:end])
        search_start = 0
        buffer = buffer[end:]


class CsiCameraBackend(CameraBackend):
    """CSI camera via the `rpicam-apps` CLI (Raspberry Pi OS Bookworm+)."""

    def __init__(self, config: CameraConfig, lock_path: Optional[str] = None):
        self._config = config
        self._lock = CameraLock(lock_path)
        self._proc: Optional[subprocess.Popen] = None
        self._buffer = b""
        self._frame_queue: List[bytes] = []

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["rpicam-hello", "--list-cameras"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        return "no cameras available" not in result.stdout.lower()

    def open(self) -> None:
        self._lock.acquire()
        width, height = self._config.resolution
        try:
            self._proc = subprocess.Popen(
                [
                    "rpicam-vid",
                    "-t",
                    "0",
                    "--codec",
                    "mjpeg",
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--framerate",
                    str(self._config.framerate),
                    "-o",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            self._lock.release()
            raise CameraNotFoundError(
                "rpicam-vid not found - install rpicam-apps (Raspberry Pi OS "
                "Bookworm's CSI camera tooling)."
            ) from exc

        if self._proc.poll() is not None:
            self._lock.release()
            raise CameraNotFoundError(
                "rpicam-vid exited immediately - no CSI camera detected "
                "(check the ribbon cable and run scripts/check_camera.sh)."
            )

    def read_frame(self) -> np.ndarray:
        if self._proc is None or self._proc.stdout is None:
            raise CameraError("read_frame() called before open()")

        while not self._frame_queue:
            chunk = self._proc.stdout.read(65536)
            if not chunk:
                raise CameraNotFoundError(
                    "Camera process ended unexpectedly (rpicam-vid exited); "
                    "the CSI camera may have been disconnected."
                )
            self._buffer += chunk
            frames, self._buffer = _split_mjpeg_frames(self._buffer)
            self._frame_queue.extend(frames)

        jpeg_bytes = self._frame_queue.pop(0)
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise CameraError("Failed to decode a JPEG frame from the camera stream")
        return frame

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._buffer = b""
        self._frame_queue = []
        self._lock.release()


class UsbCameraBackend(CameraBackend):
    """USB/UVC camera via V4L2, accessed through OpenCV's `VideoCapture`."""

    def __init__(self, config: CameraConfig, lock_path: Optional[str] = None):
        self._config = config
        self._lock = CameraLock(lock_path)
        self._cap: Optional[cv2.VideoCapture] = None

    def is_available(self) -> bool:
        return os.path.exists(self._config.device)

    def open(self) -> None:
        self._lock.acquire()
        cap = cv2.VideoCapture(self._config.device)
        if not cap.isOpened():
            cap.release()
            self._lock.release()
            raise CameraNotFoundError(
                f"Could not open USB camera at '{self._config.device}' - check "
                "it is connected and run scripts/check_camera.sh."
            )
        width, height = self._config.resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, self._config.framerate)
        self._cap = cap

    def read_frame(self) -> np.ndarray:
        if self._cap is None:
            raise CameraError("read_frame() called before open()")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise CameraNotFoundError(
                f"Lost connection to USB camera at '{self._config.device}'."
            )
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._lock.release()


def create_camera(config: CameraConfig, lock_path: Optional[str] = None) -> CameraBackend:
    """Select and construct the camera backend for `config.type`."""
    if config.type == "csi":
        return CsiCameraBackend(config, lock_path=lock_path)
    if config.type == "usb":
        return UsbCameraBackend(config, lock_path=lock_path)
    raise CameraError(f"Unknown camera type: {config.type!r}")
