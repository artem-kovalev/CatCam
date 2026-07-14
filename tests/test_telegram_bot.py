import asyncio
from contextlib import contextmanager
from unittest.mock import patch

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
from catcam.cooldown import CooldownManager
from catcam.recorder import FfmpegNotFoundError
from catcam.storage import StorageManager
from catcam.telegram_bot import (
    SnapshotError,
    _parse_and_clamp_seconds,
    build_application,
    cmd_cooldown,
    cmd_help,
    cmd_record,
    cmd_snapshot,
    cmd_status,
    cmd_stream,
)

_OWNER_USER_ID = 111
_OWNER_CHAT_ID = 222


class FakeUser:
    def __init__(self, id):
        self.id = id


class FakeChat:
    def __init__(self, id):
        self.id = id


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id, chat_id):
        self.effective_user = FakeUser(user_id) if user_id is not None else None
        self.effective_chat = FakeChat(chat_id) if chat_id is not None else None
        self.effective_message = FakeMessage()
        self.callback_query = None


class FakeContext:
    def __init__(self, bot_data, args=None):
        self.bot_data = bot_data
        self.args = args or []


def _app_config(tmp_path) -> AppConfig:
    return AppConfig(
        camera=CameraConfig(resolution=[320, 240]),
        motion=MotionConfig(),
        recording=RecordingConfig(
            storage_dir=str(tmp_path / "recordings"),
            pending_dir=str(tmp_path / "pending"),
        ),
        video_note=VideoNoteConfig(),
        cooldown=CooldownConfig(state_file=str(tmp_path / "cooldown.json")),
        streaming=StreamingConfig(),
        logging=LoggingConfig(),
        telegram=TelegramConfig(
            bot_token="123456789:AAtestFAKEtokenNOTrealNOTrealNOTreal12",
            user_id=_OWNER_USER_ID,
            chat_id=_OWNER_CHAT_ID,
        ),
    )


def _bot_data(tmp_path):
    config = _app_config(tmp_path)
    return {
        "config": config,
        "cooldown_manager": CooldownManager(config.cooldown),
        "storage_manager": StorageManager(config.recording),
        "detector_status_provider": None,
    }


def _owner_update():
    return FakeUpdate(_OWNER_USER_ID, _OWNER_CHAT_ID)


def test_parse_and_clamp_seconds_clamps_to_max():
    assert _parse_and_clamp_seconds(["9999"]) == 30


def test_parse_and_clamp_seconds_rejects_non_positive_and_non_integer():
    assert _parse_and_clamp_seconds(["0"]) is None
    assert _parse_and_clamp_seconds(["-5"]) is None
    assert _parse_and_clamp_seconds(["abc"]) is None
    assert _parse_and_clamp_seconds([]) is None
    assert _parse_and_clamp_seconds(["10", "20"]) is None


def test_cooldown_out_of_range_rejected_with_clear_message_and_state_unchanged(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data, args=["9999"])

    asyncio.run(cmd_cooldown(update, context))

    assert bot_data["cooldown_manager"].get_interval_minutes() == 60
    assert "Invalid cooldown value" in update.effective_message.replies[0]


def test_cooldown_non_integer_rejected_with_usage_message(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data, args=["thirty"])

    asyncio.run(cmd_cooldown(update, context))

    assert bot_data["cooldown_manager"].get_interval_minutes() == 60
    assert "Usage" in update.effective_message.replies[0]


def test_cooldown_no_args_reports_current_state(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data)

    asyncio.run(cmd_cooldown(update, context))

    assert "60 minutes" in update.effective_message.replies[0]


def test_help_lists_every_command(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data)

    asyncio.run(cmd_help(update, context))

    text = update.effective_message.replies[0]
    for command in (
        "/status",
        "/cooldown",
        "/notifications_on",
        "/notifications_off",
        "/snapshot",
        "/record",
        "/stream",
        "/restart_service",
        "/help",
    ):
        assert command in text


def test_stream_command_never_includes_a_public_url(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data)

    asyncio.run(cmd_stream(update, context))

    text = update.effective_message.replies[0]
    assert "http://0.0.0.0" not in text
    assert "tailscale" in text.lower() or "lan" in text.lower()


def test_snapshot_reports_error_when_no_frame_source_available(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data)

    @contextmanager
    def _broken_source(config):
        raise SnapshotError("no camera reachable")
        yield  # pragma: no cover - unreachable, satisfies generator shape

    with patch("catcam.telegram_bot._live_frame_source", _broken_source):
        asyncio.run(cmd_snapshot(update, context))

    assert "Could not capture a snapshot" in update.effective_message.replies[0]


def test_record_reports_error_when_ffmpeg_missing(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data, args=["5"])

    with patch(
        "catcam.telegram_bot.Recorder",
        side_effect=FfmpegNotFoundError("ffmpeg not found"),
    ):
        asyncio.run(cmd_record(update, context))

    assert "Cannot record" in update.effective_message.replies[-1]


def test_record_rejects_bad_usage_without_touching_recorder(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = _owner_update()
    context = FakeContext(bot_data, args=[])

    with patch("catcam.telegram_bot.Recorder") as mock_recorder:
        asyncio.run(cmd_record(update, context))

    mock_recorder.assert_not_called()
    assert "Usage" in update.effective_message.replies[0]


def test_status_reports_cooldown_and_notifications_state(tmp_path):
    bot_data = _bot_data(tmp_path)
    bot_data["cooldown_manager"].disable_notifications()
    update = _owner_update()
    context = FakeContext(bot_data)

    with patch("catcam.health.check_mediamtx_path") as mock_health:
        mock_health.return_value.path_ready = False
        mock_health.return_value.error = "connection refused"
        with patch("catcam.health.create_camera") as mock_camera:
            mock_camera.return_value.is_available.return_value = False
            asyncio.run(cmd_status(update, context))

    text = update.effective_message.replies[0]
    assert "Notifications: disabled" in text
    assert "NOT DETECTED" in text
    assert "not ready" in text


def test_build_application_registers_all_commands(tmp_path):
    config = _app_config(tmp_path)
    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)

    application = build_application(config, cooldown_manager, storage_manager)

    registered_commands = set()
    for handlers in application.handlers.values():
        for handler in handlers:
            commands = getattr(handler, "commands", None)
            if commands:
                registered_commands.update(commands)

    assert registered_commands == {
        "start",
        "help",
        "status",
        "cooldown",
        "notifications_on",
        "notifications_off",
        "snapshot",
        "record",
        "stream",
        "restart_service",
    }
