from unittest.mock import MagicMock, patch

from catcam.config import (
    AppConfig,
    CameraConfig,
    ConfigError,
    CooldownConfig,
    LoggingConfig,
    MotionConfig,
    RecordingConfig,
    StreamingConfig,
    TelegramConfig,
    VideoNoteConfig,
)
from catcam.cooldown import CooldownManager
from catcam.health import format_status, get_status, main as health_main
from catcam.storage import StorageManager


def _app_config(tmp_path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(type="csi", resolution=[320, 240]),
        motion=MotionConfig(),
        recording=RecordingConfig(
            storage_dir=str(tmp_path / "recordings"),
            pending_dir=str(tmp_path / "pending"),
        ),
        video_note=VideoNoteConfig(),
        cooldown=CooldownConfig(state_file=str(tmp_path / "cooldown.json")),
        streaming=StreamingConfig(),
        logging=LoggingConfig(),
        telegram=TelegramConfig(bot_token="x", user_id=1, chat_id=1),
    )


def test_get_status_reports_camera_stream_cooldown_and_storage(tmp_path):
    config = _app_config(tmp_path)
    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)

    with patch("catcam.health.create_camera") as mock_create_camera:
        mock_create_camera.return_value.is_available.return_value = True
        with patch("catcam.health.check_mediamtx_path") as mock_health:
            mock_health.return_value.path_ready = True
            mock_health.return_value.error = None
            status = get_status(config, cooldown_manager, storage_manager, last_motion_at=123.0)

    assert status.camera_available is True
    assert status.stream_ready is True
    assert status.cooldown_minutes == 60
    assert status.in_cooldown is False
    assert status.notifications_enabled is True
    assert status.last_motion_at == 123.0
    assert status.disk_quota_mb == config.recording.disk_quota_mb
    assert status.storage_used_mb == 0.0


def test_get_status_reflects_cooldown_and_disabled_notifications(tmp_path):
    config = _app_config(tmp_path)
    cooldown_manager = CooldownManager(config.cooldown)
    cooldown_manager.record_delivery_success()
    cooldown_manager.disable_notifications()
    storage_manager = StorageManager(config.recording)

    with patch("catcam.health.create_camera") as mock_create_camera:
        mock_create_camera.return_value.is_available.return_value = False
        with patch("catcam.health.check_mediamtx_path") as mock_health:
            mock_health.return_value.path_ready = False
            mock_health.return_value.error = "connection refused"
            status = get_status(config, cooldown_manager, storage_manager)

    assert status.camera_available is False
    assert status.stream_ready is False
    assert status.stream_error == "connection refused"
    assert status.in_cooldown is True
    assert status.notifications_enabled is False
    assert status.last_motion_at is None


def test_format_status_reports_no_motion_yet(tmp_path):
    config = _app_config(tmp_path)
    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)

    with patch("catcam.health.create_camera") as mock_create_camera:
        mock_create_camera.return_value.is_available.return_value = True
        with patch("catcam.health.check_mediamtx_path") as mock_health:
            mock_health.return_value.path_ready = True
            mock_health.return_value.error = None
            status = get_status(config, cooldown_manager, storage_manager)

    text = format_status(status)
    assert "no motion detected yet this run" in text
    assert "Camera (csi): available" in text
    assert "Notifications: enabled" in text


def test_format_status_reports_elapsed_motion_time(tmp_path):
    config = _app_config(tmp_path)
    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)

    with patch("catcam.health.create_camera") as mock_create_camera:
        mock_create_camera.return_value.is_available.return_value = True
        with patch("catcam.health.check_mediamtx_path") as mock_health:
            mock_health.return_value.path_ready = True
            mock_health.return_value.error = None
            import time

            status = get_status(config, cooldown_manager, storage_manager, last_motion_at=time.time())

    text = format_status(status)
    assert "last motion" in text
    assert "ago" in text


def test_main_returns_error_on_bad_config():
    with patch("catcam.health.load_config", side_effect=ConfigError("missing TELEGRAM_BOT_TOKEN")):
        assert health_main() == 1


def test_main_passes_when_disk_healthy(tmp_path, capsys):
    config = _app_config(tmp_path)
    with patch("catcam.health.load_config", return_value=config):
        with patch("catcam.health.create_camera") as mock_create_camera:
            mock_create_camera.return_value.is_available.return_value = True
            with patch("catcam.health.check_mediamtx_path") as mock_health:
                mock_health.return_value.path_ready = True
                mock_health.return_value.error = None
                with patch("catcam.health.shutil.disk_usage") as mock_disk_usage:
                    mock_disk_usage.return_value = MagicMock(free=10 * 1024 * 1024 * 1024)
                    exit_code = health_main()

    assert exit_code == 0
    assert "Storage used" in capsys.readouterr().out


def test_main_fails_when_storage_exceeds_quota(tmp_path):
    config = _app_config(tmp_path)
    config.recording.disk_quota_mb = 1
    (tmp_path / "recordings").mkdir()
    (tmp_path / "recordings" / "clip.mp4").write_bytes(b"0" * 2 * 1024 * 1024)

    with patch("catcam.health.load_config", return_value=config):
        with patch("catcam.health.create_camera") as mock_create_camera:
            mock_create_camera.return_value.is_available.return_value = True
            with patch("catcam.health.check_mediamtx_path") as mock_health:
                mock_health.return_value.path_ready = True
                mock_health.return_value.error = None
                with patch("catcam.health.shutil.disk_usage") as mock_disk_usage:
                    mock_disk_usage.return_value = MagicMock(free=10 * 1024 * 1024 * 1024)
                    exit_code = health_main()

    assert exit_code == 1


def test_main_fails_when_disk_free_below_threshold(tmp_path):
    config = _app_config(tmp_path)
    with patch("catcam.health.load_config", return_value=config):
        with patch("catcam.health.create_camera") as mock_create_camera:
            mock_create_camera.return_value.is_available.return_value = True
            with patch("catcam.health.check_mediamtx_path") as mock_health:
                mock_health.return_value.path_ready = True
                mock_health.return_value.error = None
                with patch("catcam.health.shutil.disk_usage") as mock_disk_usage:
                    mock_disk_usage.return_value = MagicMock(free=1 * 1024 * 1024)
                    exit_code = health_main()

    assert exit_code == 1
