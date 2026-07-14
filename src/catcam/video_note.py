"""Telegram "video note" conversion.

Per the official Telegram Bot API docs (`sendVideoNote`, verified live
2026-07-14): "Telegram clients support rounded square MPEG4 videos of up to
1 minute long." There is no `sendVideoNote`-specific file-size limit stated;
the general multipart/form-data upload cap for non-photo files is 50 MB —
`VideoNoteConfig.max_size_mb` (default 8, from task 1) is already well under
that ceiling, chosen for reliable delivery over slow/mobile connections
rather than because Telegram requires it.

`convert_to_video_note()` center-crops the shorter dimension, scales to a
square `size_px x size_px`, truncates to `max_duration_seconds`, and strips
audio (fixed — a cat-monitoring clip has no useful audio track, and this
keeps the single-FFmpeg-invocation filtergraph simpler; not config-exposed,
same "fixed constant" precedent as motion.py's noise floor). If the first
encode exceeds `max_size_mb`, one retry is attempted at a reduced resolution
and a higher (more compressed) CRF; an FFmpeg `-fs` hard cap backstops both
attempts so the output can never silently exceed the configured size, same
pattern as `recorder.py`.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List

from .config import VideoNoteConfig

logger = logging.getLogger("catcam.video_note")

_MIN_RETRY_SIZE_PX = 128
_RETRY_CRF_INCREASE = 10
_MAX_CRF = 51


class VideoNoteConversionError(Exception):
    """Raised when FFmpeg is unavailable or the conversion subprocess fails."""


def _build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    size_px: int,
    duration_seconds: float,
    codec: str,
    crf: int,
    max_size_bytes: int,
) -> List[str]:
    crop_scale = f"crop=min(iw\\,ih):min(iw\\,ih),scale={size_px}:{size_px}"
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        crop_scale,
        "-t",
        str(duration_seconds),
        "-an",
        "-c:v",
        codec,
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-fs",
        str(max_size_bytes),
        str(output_path),
    ]


def _run_ffmpeg(command: List[str]) -> None:
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        stderr_tail = result.stderr.decode(errors="replace")[-2000:] if result.stderr else ""
        raise VideoNoteConversionError(
            f"ffmpeg exited with status {result.returncode} converting to "
            f"video note: {stderr_tail}"
        )


def convert_to_video_note(
    input_path: Path, output_path: Path, config: VideoNoteConfig
) -> Path:
    """Convert `input_path` into a square, Telegram-compatible video note.

    Raises `VideoNoteConversionError` if `ffmpeg` is missing or the
    conversion subprocess fails. Never silently exceeds
    `config.max_size_mb` (enforced via FFmpeg's `-fs`), but may return a
    clip still over that cap after retry if the source can't be compressed
    enough within one retry — logged loudly in that case rather than raised,
    since a slightly-oversized clip is still more useful to the caller than
    no clip at all (the caller/task 9 decides whether to send it anyway).
    """
    if shutil.which("ffmpeg") is None:
        raise VideoNoteConversionError(
            "ffmpeg executable not found on PATH - install ffmpeg "
            "(e.g. 'sudo apt install ffmpeg' on Raspberry Pi OS)."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    max_size_bytes = config.max_size_mb * 1024 * 1024

    command = _build_ffmpeg_command(
        input_path,
        output_path,
        config.size_px,
        config.max_duration_seconds,
        config.codec,
        config.crf,
        max_size_bytes,
    )
    logger.info("Converting '%s' to video note '%s'", input_path, output_path)
    _run_ffmpeg(command)

    size_bytes = output_path.stat().st_size
    if size_bytes <= max_size_bytes:
        return output_path

    retry_size_px = max(_MIN_RETRY_SIZE_PX, config.size_px // 2)
    retry_crf = min(_MAX_CRF, config.crf + _RETRY_CRF_INCREASE)
    logger.warning(
        "Video note '%s' (%.1f MB) exceeded the %d MB cap; retrying at "
        "%dx%d, crf=%d",
        output_path,
        size_bytes / (1024 * 1024),
        config.max_size_mb,
        retry_size_px,
        retry_size_px,
        retry_crf,
    )
    retry_command = _build_ffmpeg_command(
        input_path,
        output_path,
        retry_size_px,
        config.max_duration_seconds,
        config.codec,
        retry_crf,
        max_size_bytes,
    )
    _run_ffmpeg(retry_command)

    size_bytes = output_path.stat().st_size
    if size_bytes > max_size_bytes:
        logger.error(
            "Video note '%s' still %.1f MB after retry, over the %d MB cap",
            output_path,
            size_bytes / (1024 * 1024),
            config.max_size_mb,
        )

    return output_path
