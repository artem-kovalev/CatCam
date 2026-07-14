import asyncio
import subprocess
from unittest.mock import patch

import pytest

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
from catcam.storage import StorageManager
from catcam.telegram_bot import (
    _DENIAL_MESSAGE,
    _build_restart_command,
    cmd_cooldown,
    cmd_notifications_off,
    cmd_restart_service,
    on_button,
)

_OWNER_USER_ID = 111
_OWNER_CHAT_ID = 222
_INTRUDER_USER_ID = 999


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


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.answered = False

    async def answer(self):
        self.answered = True


class FakeUpdate:
    def __init__(self, user_id, chat_id, args=None, callback_data=None):
        self.effective_user = FakeUser(user_id) if user_id is not None else None
        self.effective_chat = FakeChat(chat_id) if chat_id is not None else None
        self.effective_message = FakeMessage()
        self.callback_query = FakeCallbackQuery(callback_data) if callback_data else None


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


def test_authorized_user_reaches_handler(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_OWNER_USER_ID, _OWNER_CHAT_ID)
    context = FakeContext(bot_data)

    asyncio.run(cmd_notifications_off(update, context))

    assert bot_data["cooldown_manager"].notifications_enabled() is False
    assert update.effective_message.replies == ["Notifications disabled."]


def test_unauthorized_user_rejected_before_handler_runs(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_INTRUDER_USER_ID, _INTRUDER_USER_ID)
    context = FakeContext(bot_data)

    asyncio.run(cmd_notifications_off(update, context))

    # State-changing logic must never have run.
    assert bot_data["cooldown_manager"].notifications_enabled() is True
    assert update.effective_message.replies == [_DENIAL_MESSAGE]


def test_mismatched_chat_id_rejected(tmp_path):
    bot_data = _bot_data(tmp_path)
    # Correct user id, but a chat id that doesn't match config.telegram.chat_id.
    update = FakeUpdate(_OWNER_USER_ID, _INTRUDER_USER_ID)
    context = FakeContext(bot_data)

    asyncio.run(cmd_notifications_off(update, context))

    assert bot_data["cooldown_manager"].notifications_enabled() is True
    assert update.effective_message.replies == [_DENIAL_MESSAGE]


def test_unauthorized_cooldown_change_rejected(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_INTRUDER_USER_ID, _INTRUDER_USER_ID)
    context = FakeContext(bot_data, args=["120"])

    asyncio.run(cmd_cooldown(update, context))

    assert bot_data["cooldown_manager"].get_interval_minutes() == 60
    assert update.effective_message.replies == [_DENIAL_MESSAGE]


def test_authorized_cooldown_change_accepted(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_OWNER_USER_ID, _OWNER_CHAT_ID)
    context = FakeContext(bot_data, args=["120"])

    asyncio.run(cmd_cooldown(update, context))

    assert bot_data["cooldown_manager"].get_interval_minutes() == 120


def test_unauthorized_button_press_rejected(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_INTRUDER_USER_ID, _INTRUDER_USER_ID, callback_data="notifications_off")
    context = FakeContext(bot_data)

    asyncio.run(on_button(update, context))

    assert bot_data["cooldown_manager"].notifications_enabled() is True
    assert update.callback_query.answered is False


def test_authorized_button_press_dispatches_to_handler(tmp_path):
    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_OWNER_USER_ID, _OWNER_CHAT_ID, callback_data="notifications_off")
    context = FakeContext(bot_data)

    asyncio.run(on_button(update, context))

    assert bot_data["cooldown_manager"].notifications_enabled() is False
    assert update.callback_query.answered is True
    assert update.effective_message.replies == ["Notifications disabled."]


def test_bot_token_never_appears_in_logs(tmp_path, caplog):
    bot_data = _bot_data(tmp_path)
    token = bot_data["config"].telegram.bot_token

    with caplog.at_level("DEBUG"):
        # Rejected attempt (logs a warning).
        asyncio.run(
            cmd_notifications_off(
                FakeUpdate(_INTRUDER_USER_ID, _INTRUDER_USER_ID), FakeContext(bot_data)
            )
        )
        # A failing restart_service (logs an error via the redaction helper).
        with patch(
            "catcam.telegram_bot.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["x"]),
        ):
            asyncio.run(
                cmd_restart_service(
                    FakeUpdate(_OWNER_USER_ID, _OWNER_CHAT_ID), FakeContext(bot_data)
                )
            )

    assert token not in caplog.text


def test_restart_service_command_is_fixed_allowlisted_and_shell_false(tmp_path):
    command = _build_restart_command()
    assert command == ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "catcam.service"]

    bot_data = _bot_data(tmp_path)
    update = FakeUpdate(_OWNER_USER_ID, _OWNER_CHAT_ID)
    context = FakeContext(bot_data)

    with patch("catcam.telegram_bot.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        asyncio.run(cmd_restart_service(update, context))

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == ["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "catcam.service"]
    assert kwargs["shell"] is False
    assert kwargs["check"] is True
