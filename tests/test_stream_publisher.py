from unittest.mock import MagicMock, patch

from catcam.camera import CameraBusyError
from catcam.config import (
    AppConfig,
    CameraConfig,
    CooldownConfig,
    LoggingConfig,
    MotionConfig,
    RecordingConfig,
    StreamingConfig,
    TelegramConfig,
    VideoNoteConfig,
)
from catcam.stream_publisher import build_ffmpeg_command, main


def _app_config(camera_type: str) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(type=camera_type, device="/dev/video0", resolution=[640, 480], framerate=15),
        motion=MotionConfig(),
        recording=RecordingConfig(),
        video_note=VideoNoteConfig(),
        cooldown=CooldownConfig(),
        streaming=StreamingConfig(),
        logging=LoggingConfig(),
        telegram=TelegramConfig(bot_token="x", user_id=1, chat_id=1),
    )


def test_build_ffmpeg_command_shape():
    camera = CameraConfig(type="usb", device="/dev/video0", resolution=[640, 480], framerate=15)
    streaming = StreamingConfig(rtsp_port=8554, path="cam")
    cmd = build_ffmpeg_command(camera, streaming, "rtsp://127.0.0.1:8554/cam")

    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "v4l2"
    assert "/dev/video0" in cmd
    assert "640x480" in cmd
    assert cmd[-1] == "rtsp://127.0.0.1:8554/cam"
    assert cmd[-3:-1] == ["-rtsp_transport", "tcp"]


@patch("catcam.stream_publisher.load_config")
def test_main_rejects_csi_camera(mock_load_config):
    mock_load_config.return_value = _app_config("csi")

    with patch("catcam.stream_publisher.subprocess.Popen") as mock_popen:
        result = main()

    assert result == 1
    mock_popen.assert_not_called()


@patch("catcam.stream_publisher.shutil.which", return_value=None)
@patch("catcam.stream_publisher.load_config")
def test_main_fails_when_ffmpeg_missing(mock_load_config, mock_which):
    mock_load_config.return_value = _app_config("usb")

    with patch("catcam.stream_publisher.subprocess.Popen") as mock_popen:
        result = main()

    assert result == 1
    mock_popen.assert_not_called()


@patch("catcam.stream_publisher.shutil.which", return_value="/usr/bin/ffmpeg")
@patch("catcam.stream_publisher.load_config")
def test_main_fails_when_camera_lock_busy(mock_load_config, mock_which):
    mock_load_config.return_value = _app_config("usb")

    with patch("catcam.stream_publisher.CameraLock") as mock_lock_cls:
        mock_lock = MagicMock()
        mock_lock.acquire.side_effect = CameraBusyError("busy")
        mock_lock_cls.return_value = mock_lock

        with patch("catcam.stream_publisher.subprocess.Popen") as mock_popen:
            result = main()

    assert result == 1
    mock_popen.assert_not_called()
    mock_lock.release.assert_not_called()
