"""Private Telegram bot: owner-only commands for status, snapshots, manual
recording, cooldown/notification control, stream info, and service restart.

Every command handler is wrapped by `_authorized_only`, which checks
`update.effective_user.id` against `config.telegram.user_id` (and
`update.effective_chat.id` against `config.telegram.chat_id`) *before* any
handler body runs, replying with a generic "Not authorized." on mismatch and
logging the rejected id (never a token) for audit. Inline buttons dispatch
through the exact same handler functions as their typed-command counterparts
(see `_BUTTON_HANDLERS`), so there is no separate "button-only" code path to
drift out of sync.

Frame acquisition for `/snapshot` and `/record` deliberately does not call
`create_camera()` directly: per task 3's camera-ownership contract
(`docs/streaming.md`), once `catcam-stream.service` is active the physical
camera is owned by MediaMTX, so a second direct open would either raise
`CameraBusyError` (USB) or race the driver (CSI). `_live_frame_source()`
instead reads from the same MediaMTX RTSP feed, falling back to
`create_camera_frame_source()` (recorder.py's local-dev/no-streaming path)
only when the RTSP feed isn't reachable.
"""

import dataclasses
import logging
import subprocess
import sys
from functools import wraps
from io import BytesIO
from typing import Callable, Dict, List, Optional

import cv2
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from . import health
from .config import AppConfig, ConfigError, load_config
from .cooldown import CooldownConfigError, CooldownManager
from .frame_source import FrameSourceError as SnapshotError
from .frame_source import live_frame_source as _live_frame_source
from .logging_config import redact, setup_logging
from .recorder import FfmpegNotFoundError, Recorder, RecordingFailedError
from .storage import StorageManager
from .video_note import VideoNoteConversionError, convert_to_video_note

logger = logging.getLogger("catcam.telegram_bot")

# Manual /record is clamped to this many seconds regardless of the request,
# to bound storage/abuse exposure even though only the owner can call it.
# Fixed, not config-exposed - same "small fixed constant" precedent as
# motion.py's noise floor and video_note.py's audio stripping.
_MAX_MANUAL_RECORD_SECONDS = 30

# Fixed, allowlisted restart invocation - no shell, no user-supplied string
# ever reaches it. The matching sudoers rule is documented in task 10.
_SUDO = "/usr/bin/sudo"
_SYSTEMCTL = "/usr/bin/systemctl"
_SERVICE_NAME = "catcam.service"

_DENIAL_MESSAGE = "Not authorized."


def _build_restart_command() -> List[str]:
    return [_SUDO, _SYSTEMCTL, "restart", _SERVICE_NAME]


def _parse_and_clamp_seconds(args: List[str]) -> Optional[int]:
    if len(args) != 1:
        return None
    try:
        value = int(args[0])
    except ValueError:
        return None
    if value <= 0:
        return None
    return min(value, _MAX_MANUAL_RECORD_SECONDS)


def _authorized_only(handler):
    """Reject any update not from `config.telegram.user_id`/`chat_id` first."""

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        config: AppConfig = context.bot_data["config"]
        user = update.effective_user
        chat = update.effective_chat
        authorized = (
            user is not None
            and user.id == config.telegram.user_id
            and (chat is None or chat.id == config.telegram.chat_id)
        )
        if not authorized:
            logger.warning(
                "Rejected unauthorized access: user_id=%s chat_id=%s",
                user.id if user is not None else None,
                chat.id if chat is not None else None,
            )
            if update.effective_message is not None:
                await update.effective_message.reply_text(_DENIAL_MESSAGE)
            return
        await handler(update, context)

    return wrapper


# --- Command implementations (plain, undecorated) --------------------------
# Each is registered for its typed command via `_authorized_only` below, and
# some are also reused directly by `on_button` for inline-keyboard dispatch -
# `on_button` itself is authorized, so no double-wrapping is needed there.

async def _start_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "CatCam bot ready. Send /help to see available commands.",
        reply_markup=_main_menu_keyboard(),
    )


async def _help_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = [
        "/status - camera/stream/cooldown/disk status",
        "/cooldown [minutes] - show or set the delivery cooldown",
        "/notifications_on - resume automatic delivery",
        "/notifications_off - pause automatic delivery",
        "/snapshot - capture a single still frame",
        f"/record <seconds> - record a clip (clamped to {_MAX_MANUAL_RECORD_SECONDS}s max)",
        "/stream - how to view the live stream",
        "/restart_service - restart the catcam service",
        "/help - this message",
    ]
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_main_menu_keyboard())


async def _status_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.bot_data["config"]
    cooldown_manager: CooldownManager = context.bot_data["cooldown_manager"]
    storage_manager: StorageManager = context.bot_data["storage_manager"]
    last_motion_at_provider: Optional[Callable[[], Optional[float]]] = context.bot_data.get(
        "last_motion_at_provider"
    )

    last_motion_at = last_motion_at_provider() if last_motion_at_provider is not None else None
    status = health.get_status(
        config, cooldown_manager, storage_manager, last_motion_at=last_motion_at
    )
    await update.effective_message.reply_text(health.format_status(status))


async def _cooldown_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cooldown_manager: CooldownManager = context.bot_data["cooldown_manager"]
    args = context.args or []

    if not args:
        status = "in cooldown" if cooldown_manager.is_in_cooldown() else "ready"
        await update.effective_message.reply_text(
            f"Cooldown interval: {cooldown_manager.get_interval_minutes()} minutes ({status})."
        )
        return

    try:
        value = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("Usage: /cooldown <minutes> (integer)")
        return

    try:
        cooldown_manager.set_interval_minutes(value)
    except CooldownConfigError as exc:
        await update.effective_message.reply_text(f"Invalid cooldown value: {exc}")
        return

    await update.effective_message.reply_text(f"Cooldown interval set to {value} minutes.")


async def _notifications_on_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["cooldown_manager"].enable_notifications()
    await update.effective_message.reply_text("Notifications enabled.")


async def _notifications_off_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["cooldown_manager"].disable_notifications()
    await update.effective_message.reply_text("Notifications disabled.")


async def _snapshot_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.bot_data["config"]
    try:
        with _live_frame_source(config) as read_frame:
            frame = read_frame()
    except SnapshotError as exc:
        await update.effective_message.reply_text(f"Could not capture a snapshot: {exc}")
        return

    ok, jpeg = cv2.imencode(".jpg", frame)
    if not ok:
        await update.effective_message.reply_text("Captured a frame but failed to encode it.")
        return
    await update.effective_message.reply_photo(photo=BytesIO(jpeg.tobytes()))


async def _record_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: AppConfig = context.bot_data["config"]
    args = context.args or []
    seconds = _parse_and_clamp_seconds(args)
    if seconds is None:
        await update.effective_message.reply_text(
            f"Usage: /record <seconds> (1-{_MAX_MANUAL_RECORD_SECONDS})"
        )
        return

    clip_config = dataclasses.replace(config.recording, clip_duration_seconds=seconds)
    try:
        recorder = Recorder(clip_config)
    except FfmpegNotFoundError as exc:
        await update.effective_message.reply_text(f"Cannot record: {exc}")
        return

    await update.effective_message.reply_text(f"Recording a {seconds}s clip...")
    try:
        with _live_frame_source(config) as read_frame:
            clip_path = recorder.record_event(
                pre_roll_frames=[],
                frame_source=read_frame,
                fps=config.camera.framerate,
                resolution=tuple(config.camera.resolution),
            )
    except (SnapshotError, RecordingFailedError) as exc:
        await update.effective_message.reply_text(f"Recording failed: {exc}")
        return

    note_path = clip_path.with_name(clip_path.stem + "_note.mp4")
    try:
        convert_to_video_note(clip_path, note_path, config.video_note)
        with note_path.open("rb") as fh:
            await update.effective_message.reply_video_note(video_note=fh)
    except VideoNoteConversionError as exc:
        logger.warning(
            "Video note conversion failed (%s); sending the raw clip instead", exc
        )
        with clip_path.open("rb") as fh:
            await update.effective_message.reply_video(video=fh)
    finally:
        clip_path.unlink(missing_ok=True)
        note_path.unlink(missing_ok=True)


async def _stream_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    streaming = context.bot_data["config"].streaming
    text = (
        "Live stream - reachable over Tailscale or the plain LAN only, "
        "never on the public internet:\n"
        f"WebRTC: http://<pi-tailscale-or-lan-ip>:{streaming.webrtc_port}/{streaming.path} "
        "(user catcam-viewer)\n"
        f"HLS fallback: http://<pi-tailscale-or-lan-ip>:{streaming.hls_port}/{streaming.path}/\n"
        f"RTSP (VLC): rtsp://catcam-viewer:<password>@<pi-tailscale-or-lan-ip>:"
        f"{streaming.rtsp_port}/{streaming.path}\n"
        "See docs/streaming.md for full setup and troubleshooting."
    )
    await update.effective_message.reply_text(text)


async def _restart_service_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(f"Restarting {_SERVICE_NAME}...")
    command = _build_restart_command()
    try:
        subprocess.run(command, shell=False, check=True, timeout=15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("restart_service failed: %s", redact(str(exc)))
        try:
            await update.effective_message.reply_text(f"Restart failed: {exc}")
        except Exception:  # noqa: BLE001 - best-effort, the process may be dying
            pass


# --- Registration ------------------------------------------------------------

cmd_start = _authorized_only(_start_impl)
cmd_help = _authorized_only(_help_impl)
cmd_status = _authorized_only(_status_impl)
cmd_cooldown = _authorized_only(_cooldown_impl)
cmd_notifications_on = _authorized_only(_notifications_on_impl)
cmd_notifications_off = _authorized_only(_notifications_off_impl)
cmd_snapshot = _authorized_only(_snapshot_impl)
cmd_record = _authorized_only(_record_impl)
cmd_stream = _authorized_only(_stream_impl)
cmd_restart_service = _authorized_only(_restart_service_impl)

# Buttons only mirror the simple, argument-less, non-destructive commands -
# /record, /cooldown <minutes>, and /restart_service are deliberately
# type-only to avoid an accidental tap triggering a recording or a restart.
_BUTTON_HANDLERS: Dict[str, Callable] = {
    "status": _status_impl,
    "snapshot": _snapshot_impl,
    "notifications_on": _notifications_on_impl,
    "notifications_off": _notifications_off_impl,
    "stream": _stream_impl,
}


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Status", callback_data="status"),
                InlineKeyboardButton("Snapshot", callback_data="snapshot"),
            ],
            [
                InlineKeyboardButton("Notifications on", callback_data="notifications_on"),
                InlineKeyboardButton("Notifications off", callback_data="notifications_off"),
            ],
            [InlineKeyboardButton("Stream info", callback_data="stream")],
        ]
    )


async def _on_button_impl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    handler = _BUTTON_HANDLERS.get(query.data)
    if handler is not None:
        await handler(update, context)


on_button = _authorized_only(_on_button_impl)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error while processing update: %s", redact(str(context.error)))


def build_application(
    config: AppConfig,
    cooldown_manager: CooldownManager,
    storage_manager: StorageManager,
    last_motion_at_provider: Optional[Callable[[], Optional[float]]] = None,
) -> Application:
    """Construct the configured `Application`.

    `last_motion_at_provider`, when supplied (by `main.py`'s orchestrator,
    task 9), lets `/status` report real motion-detector activity via
    `health.py` instead of the "not tracked here" placeholder.
    """
    application = Application.builder().token(config.telegram.bot_token).build()
    application.bot_data["config"] = config
    application.bot_data["cooldown_manager"] = cooldown_manager
    application.bot_data["storage_manager"] = storage_manager
    application.bot_data["last_motion_at_provider"] = last_motion_at_provider

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("cooldown", cmd_cooldown))
    application.add_handler(CommandHandler("notifications_on", cmd_notifications_on))
    application.add_handler(CommandHandler("notifications_off", cmd_notifications_off))
    application.add_handler(CommandHandler("snapshot", cmd_snapshot))
    application.add_handler(CommandHandler("record", cmd_record))
    application.add_handler(CommandHandler("stream", cmd_stream))
    application.add_handler(CommandHandler("restart_service", cmd_restart_service))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_error_handler(_on_error)

    return application


def main() -> int:
    """Standalone entry point: bot commands only, no motion-detection
    pipeline - see `main.py` (task 9) for the fully orchestrated service.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO)
        logger.error("Invalid configuration: %s", exc)
        return 1

    setup_logging(config.logging)
    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)
    application = build_application(config, cooldown_manager, storage_manager)
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
