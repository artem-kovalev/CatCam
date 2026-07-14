import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from catcam.config import VideoNoteConfig
from catcam.video_note import (
    VideoNoteConversionError,
    _build_ffmpeg_command,
    convert_to_video_note,
)


def _config(**overrides) -> VideoNoteConfig:
    defaults = dict(size_px=384, max_duration_seconds=60, max_size_mb=8, codec="libx264", crf=28)
    defaults.update(overrides)
    return VideoNoteConfig(**defaults)


def test_build_ffmpeg_command_contains_crop_scale_and_limits(tmp_path):
    input_path = tmp_path / "in.mp4"
    output_path = tmp_path / "out.mp4"
    command = _build_ffmpeg_command(
        input_path, output_path, 384, 60, "libx264", 28, 8 * 1024 * 1024
    )

    assert command[0] == "ffmpeg"
    vf_value = command[command.index("-vf") + 1]
    assert "crop=min(iw\\,ih):min(iw\\,ih)" in vf_value
    assert "scale=384:384" in vf_value
    assert "-an" in command  # audio stripped, fixed
    assert command[command.index("-t") + 1] == "60"
    assert command[command.index("-crf") + 1] == "28"
    assert command[command.index("-fs") + 1] == str(8 * 1024 * 1024)
    assert command[-1] == str(output_path)


@patch("catcam.video_note.shutil.which", return_value=None)
def test_convert_raises_when_ffmpeg_missing(mock_which, tmp_path):
    with pytest.raises(VideoNoteConversionError):
        convert_to_video_note(tmp_path / "in.mp4", tmp_path / "out.mp4", _config())


@patch("catcam.video_note.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.video_note.subprocess.run")
def test_convert_raises_on_ffmpeg_failure(mock_run, mock_which, tmp_path):
    mock_run.return_value = MagicMock(returncode=1, stderr=b"ffmpeg: fatal error")

    with pytest.raises(VideoNoteConversionError):
        convert_to_video_note(tmp_path / "in.mp4", tmp_path / "out.mp4", _config())


@patch("catcam.video_note.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.video_note.subprocess.run")
def test_convert_returns_path_when_within_size_cap(mock_run, mock_which, tmp_path):
    output_path = tmp_path / "out.mp4"

    def fake_run(command, capture_output=True):
        output_path.write_bytes(b"\0" * 1024)  # 1 KB, well under any cap
        return MagicMock(returncode=0, stderr=b"")

    mock_run.side_effect = fake_run

    result = convert_to_video_note(tmp_path / "in.mp4", output_path, _config())

    assert result == output_path
    assert mock_run.call_count == 1  # no retry needed


@patch("catcam.video_note.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.video_note.subprocess.run")
def test_convert_retries_once_when_oversized(mock_run, mock_which, tmp_path):
    output_path = tmp_path / "out.mp4"
    one_mb = 1024 * 1024
    calls = {"count": 0}

    def fake_run(command, capture_output=True):
        calls["count"] += 1
        if calls["count"] == 1:
            output_path.write_bytes(b"\0" * (10 * one_mb))  # over the 8 MB cap
        else:
            output_path.write_bytes(b"\0" * one_mb)  # retry succeeds
        return MagicMock(returncode=0, stderr=b"")

    mock_run.side_effect = fake_run

    result = convert_to_video_note(tmp_path / "in.mp4", output_path, _config(max_size_mb=8))

    assert result == output_path
    assert calls["count"] == 2
    # second command used a smaller resolution and a higher (worse) crf
    second_command = mock_run.call_args_list[1].args[0]
    assert second_command[second_command.index("-crf") + 1] == "38"
    assert "scale=192:192" in second_command[second_command.index("-vf") + 1]


@patch("catcam.video_note.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.video_note.subprocess.run")
def test_convert_logs_and_returns_when_still_oversized_after_retry(mock_run, mock_which, tmp_path):
    output_path = tmp_path / "out.mp4"
    one_mb = 1024 * 1024

    def fake_run(command, capture_output=True):
        output_path.write_bytes(b"\0" * (10 * one_mb))  # always over cap
        return MagicMock(returncode=0, stderr=b"")

    mock_run.side_effect = fake_run

    result = convert_to_video_note(tmp_path / "in.mp4", output_path, _config(max_size_mb=8))

    assert result == output_path  # returns anyway rather than raising
    assert mock_run.call_count == 2  # retried exactly once, no further loop


# --- Real-FFmpeg integration tests: skipped cleanly if ffmpeg isn't installed ---

pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed in this test environment"
)


@pytestmark_ffmpeg
def test_real_conversion_produces_square_mp4(tmp_path):
    import cv2

    source = tmp_path / "source.mp4"
    # 16:9 synthetic test source, 3 seconds, via ffmpeg's lavfi testsrc.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=10",
            "-t",
            "3",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    output = tmp_path / "note.mp4"
    result = convert_to_video_note(source, output, _config(size_px=128))

    assert result == output
    assert output.exists()

    cap = cv2.VideoCapture(str(output))
    assert cap.isOpened()
    ok, frame = cap.read()
    assert ok
    assert frame.shape[0] == frame.shape[1] == 128  # square
    cap.release()
