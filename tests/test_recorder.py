from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from catcam.config import RecordingConfig
from catcam.recorder import (
    FfmpegNotFoundError,
    Recorder,
    RecordingFailedError,
    _build_ffmpeg_command,
)


def _config(tmp_path: Path) -> RecordingConfig:
    return RecordingConfig(
        clip_duration_seconds=2,
        pre_roll_seconds=1,
        max_clip_size_mb=10,
        storage_dir=str(tmp_path / "recordings"),
        pending_dir=str(tmp_path / "pending"),
        disk_quota_mb=2048,
    )


def test_build_ffmpeg_command_contains_expected_flags(tmp_path):
    output_path = tmp_path / "clip.mp4"
    command = _build_ffmpeg_command((320, 240), 10.0, 5_000_000, output_path)

    assert command[0] == "ffmpeg"
    assert "-s" in command and command[command.index("-s") + 1] == "320x240"
    assert "-r" in command and command[command.index("-r") + 1] == "10.0"
    assert "-fs" in command and command[command.index("-fs") + 1] == "5000000"
    assert command[-1] == str(output_path)


@patch("catcam.recorder.shutil.which", return_value=None)
def test_recorder_raises_when_ffmpeg_missing(mock_which, tmp_path):
    with pytest.raises(FfmpegNotFoundError):
        Recorder(_config(tmp_path))


@patch("catcam.recorder.shutil.which", return_value="/usr/bin/ffmpeg")
def test_recorder_constructs_when_ffmpeg_present(mock_which, tmp_path):
    Recorder(_config(tmp_path))  # must not raise


@patch("catcam.recorder.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.recorder.subprocess.Popen")
def test_record_event_raises_on_nonzero_exit(mock_popen, mock_which, tmp_path):
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.communicate.return_value = (b"", b"ffmpeg: fatal error")
    proc.returncode = 1
    mock_popen.return_value = proc

    recorder = Recorder(_config(tmp_path))
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    with pytest.raises(RecordingFailedError):
        recorder.record_event(
            pre_roll_frames=[frame],
            frame_source=lambda: frame,
            fps=10.0,
            resolution=(320, 240),
        )


@patch("catcam.recorder.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.recorder.subprocess.Popen")
def test_record_event_writes_pre_roll_then_live_frames(mock_popen, mock_which, tmp_path):
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.communicate.return_value = (b"", b"")
    proc.returncode = 0
    mock_popen.return_value = proc

    recorder = Recorder(_config(tmp_path))
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    live_calls = {"count": 0}

    def frame_source():
        live_calls["count"] += 1
        return frame

    # clip_duration_seconds=2, fps=10 -> 20 total frames; 5 pre-roll supplied.
    result = recorder.record_event(
        pre_roll_frames=[frame] * 5,
        frame_source=frame_source,
        fps=10.0,
        resolution=(320, 240),
    )

    assert live_calls["count"] == 15
    assert proc.stdin.write.call_count == 20
    assert result.parent == tmp_path / "tmp"
    assert result.suffix == ".mp4"


@patch("catcam.recorder.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.recorder.subprocess.Popen")
def test_record_event_truncates_pre_roll_longer_than_duration(mock_popen, mock_which, tmp_path):
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.communicate.return_value = (b"", b"")
    proc.returncode = 0
    mock_popen.return_value = proc

    recorder = Recorder(_config(tmp_path))
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    # clip_duration_seconds=2, fps=10 -> 20 total frames; 30 pre-roll frames supplied.
    recorder.record_event(
        pre_roll_frames=[frame] * 30,
        frame_source=lambda: frame,
        fps=10.0,
        resolution=(320, 240),
    )

    assert proc.stdin.write.call_count == 20


@patch("catcam.recorder.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.recorder.subprocess.Popen")
def test_record_event_handles_broken_pipe_as_size_cap_truncation(mock_popen, mock_which, tmp_path):
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write.side_effect = [None, None, BrokenPipeError()]
    proc.communicate.return_value = (b"", b"")
    proc.returncode = 0
    mock_popen.return_value = proc

    recorder = Recorder(_config(tmp_path))
    frame = np.zeros((240, 320, 3), dtype=np.uint8)

    result = recorder.record_event(
        pre_roll_frames=[frame] * 5,
        frame_source=lambda: frame,
        fps=10.0,
        resolution=(320, 240),
    )

    assert result.suffix == ".mp4"  # returns normally, doesn't raise
