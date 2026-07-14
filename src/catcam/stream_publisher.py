"""USB-camera publisher: feeds a V4L2 device into MediaMTX as an RTSP source.

CSI cameras don't need this — MediaMTX's native `rpiCamera` source opens the
CSI sensor directly (see `deploy/mediamtx/mediamtx.yml`). USB/V4L2 cameras
have no equivalent native MediaMTX source, so this module runs `ffmpeg` as a
long-lived RTSP publisher, holding `catcam.camera.CameraLock` for as long as
it runs so `create_camera()` correctly raises `CameraBusyError` if anything
else tries to open the same device while streaming is active.

Entry point: `python -m catcam.stream_publisher` (wired to
`deploy/systemd/catcam-publisher.service`, USB deployments only).
"""

import logging
import shutil
import signal
import subprocess
import sys
from typing import List

from .camera import CameraBusyError, CameraLock
from .config import AppConfig, CameraConfig, ConfigError, StreamingConfig, load_config

logger = logging.getLogger("catcam.stream_publisher")


def build_ffmpeg_command(
    camera: CameraConfig, streaming: StreamingConfig, rtsp_url: str
) -> List[str]:
    """Build the ffmpeg command that publishes `camera.device` into MediaMTX.

    Pure function (no I/O) so the exact CLI flags are unit-testable without
    ffmpeg, a camera, or a network connection.
    """
    width, height = camera.resolution
    return [
        "ffmpeg",
        "-f",
        "v4l2",
        "-framerate",
        str(camera.framerate),
        "-video_size",
        f"{width}x{height}",
        "-i",
        camera.device,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ]


def _build_rtsp_url(streaming: StreamingConfig) -> str:
    # The publisher always runs on the same host as MediaMTX, authenticating
    # as the loopback-only `catcam-publisher` user configured in
    # deploy/mediamtx/mediamtx.yml. The password is supplied to *this*
    # process (and to MediaMTX) via the shared /etc/catcam/mediamtx.env file,
    # never hardcoded here.
    import os

    password = os.environ.get("CATCAM_STREAM_PUBLISH_PASSWORD", "")
    return f"rtsp://catcam-publisher:{password}@127.0.0.1:{streaming.rtsp_port}/{streaming.path}"


def _run_publisher(config: AppConfig) -> int:
    if not shutil.which("ffmpeg"):
        logger.error(
            "ffmpeg not found - install it (e.g. 'sudo apt install -y ffmpeg') "
            "before enabling catcam-publisher.service."
        )
        return 1

    lock = CameraLock()
    try:
        lock.acquire()
    except CameraBusyError as exc:
        logger.error("Cannot start USB camera publisher: %s", exc)
        return 1

    try:
        rtsp_url = _build_rtsp_url(config.streaming)
        cmd = build_ffmpeg_command(config.camera, config.streaming, rtsp_url)
        logger.info(
            "Publishing USB camera '%s' to MediaMTX path '%s'",
            config.camera.device,
            config.streaming.path,
        )
        proc = subprocess.Popen(cmd)

        def _forward_signal(signum, _frame):
            proc.terminate()

        signal.signal(signal.SIGTERM, _forward_signal)
        signal.signal(signal.SIGINT, _forward_signal)

        return proc.wait()
    finally:
        lock.release()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Invalid configuration: %s", exc)
        return 1

    if config.camera.type != "usb":
        logger.error(
            "catcam-publisher.service is USB-only (camera.type=%r). CSI cameras "
            "are fed to MediaMTX natively via its rpiCamera source - do not "
            "enable this unit for CSI deployments. See docs/streaming.md.",
            config.camera.type,
        )
        return 1

    return _run_publisher(config)


if __name__ == "__main__":
    sys.exit(main())
