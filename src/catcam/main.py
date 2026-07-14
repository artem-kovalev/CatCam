"""Top-level orchestrator: wires camera -> motion detection -> recording ->
video-note conversion -> cooldown-gated Telegram delivery into a single
process, alongside the Telegram bot's own update polling and a background
retry-queue drain for previously-failed deliveries.

Architecture (see `docs/architecture.md` for the full diagram):

- The motion-detection/recording pipeline is inherently blocking (frame
  reads, OpenCV processing, and `Recorder`'s synchronous FFmpeg subprocess
  calls), so it runs in its own dedicated background thread rather than
  fighting the asyncio event loop `python-telegram-bot` needs for update
  polling. Telegram sends are async; the thread bridges into the running
  event loop via `asyncio.run_coroutine_threadsafe()`.
- The retry-queue drain is naturally async (just a periodic check + await),
  so it runs as a plain `asyncio.Task` alongside the bot's polling loop.
- `send_video_note_with_retry()` is the single delivery path both the live
  pipeline and the retry-queue drain call, so a failure always lands the
  clip in `storage/pending/` uniformly regardless of which path triggered it.

Entry point: `python -m catcam.main` (wired to `deploy/systemd/catcam.service`
in task 10).
"""

import asyncio
import logging
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, ContextManager, Deque, List, Optional, Tuple

import numpy as np
from telegram.ext import Application

from .config import AppConfig, ConfigError, load_config
from .cooldown import CooldownManager
from .frame_source import FrameSourceError, live_frame_source
from .logging_config import setup_logging
from .motion import MotionDetector, MotionEvent
from .recorder import FfmpegNotFoundError, Recorder, RecordingFailedError
from .storage import StorageManager
from .telegram_bot import build_application
from .video_note import VideoNoteConversionError, convert_to_video_note

logger = logging.getLogger("catcam.main")

_FRAME_SOURCE_RETRY_BACKOFF_SECONDS = 5.0
_PIPELINE_CRASH_BACKOFF_SECONDS = 5.0
_RETRY_QUEUE_INTERVAL_SECONDS = 60.0


class OrchestratorState:
    """Small mutable holder for state shared across threads/tasks.

    Only `last_motion_at` today - read by `/status` (task 8) via
    `health.py` to report live motion-detector activity.
    """

    def __init__(self) -> None:
        self.last_motion_at: Optional[float] = None


# --- Delivery --------------------------------------------------------------


async def send_video_note_with_retry(bot, chat_id: int, clip_path: Path, config: AppConfig) -> bool:
    """Convert `clip_path` to a video note (falling back to the raw clip if
    conversion fails) and attempt one Telegram send. Returns whether it was
    confirmed delivered - never raises, so callers can treat any failure
    (network, Telegram API error, conversion failure) uniformly.
    """
    note_path = clip_path.with_name(clip_path.stem + "_note.mp4")
    send_path = clip_path
    is_note = False
    try:
        convert_to_video_note(clip_path, note_path, config.video_note)
        send_path = note_path
        is_note = True
    except VideoNoteConversionError as exc:
        logger.warning(
            "Video note conversion failed for '%s' (%s); sending the raw clip instead",
            clip_path,
            exc,
        )

    try:
        with send_path.open("rb") as fh:
            if is_note:
                await bot.send_video_note(chat_id=chat_id, video_note=fh)
            else:
                await bot.send_video(chat_id=chat_id, video=fh)
        return True
    except Exception as exc:  # noqa: BLE001 - any failure here means "not delivered"
        logger.warning("Delivery to Telegram failed for '%s': %s", clip_path, exc)
        return False
    finally:
        if is_note:
            note_path.unlink(missing_ok=True)


async def drain_retry_queue_once(
    storage_manager: StorageManager,
    cooldown_manager: CooldownManager,
    deliver: Callable[[Path], Awaitable[bool]],
) -> None:
    """One pass over `storage_manager.list_pending()`.

    Does *not* re-check cooldown before retrying: the decision to send was
    already made when the clip was first recorded (it only ended up pending
    because delivery itself failed), and the cooldown timer only ever moves
    forward on confirmed success regardless of which path triggers it.
    """
    for clip_path in storage_manager.list_pending():
        delivered = await deliver(clip_path)
        if delivered:
            storage_manager.mark_delivered(clip_path)
            cooldown_manager.record_delivery_success()
        else:
            logger.info("Retry still pending for '%s'", clip_path)


async def _retry_queue_loop(
    config: AppConfig,
    cooldown_manager: CooldownManager,
    storage_manager: StorageManager,
    application: Application,
) -> None:
    async def deliver(clip_path: Path) -> bool:
        return await send_video_note_with_retry(
            application.bot, config.telegram.chat_id, clip_path, config
        )

    while True:
        try:
            await drain_retry_queue_once(storage_manager, cooldown_manager, deliver)
        except Exception:
            logger.exception("Retry-queue drain iteration failed; will retry next interval")
        await asyncio.sleep(_RETRY_QUEUE_INTERVAL_SECONDS)


# --- Motion pipeline (runs in a dedicated background thread) --------------


def handle_motion_event(
    *,
    in_cooldown: bool,
    recorder: Recorder,
    storage_manager: StorageManager,
    cooldown_manager: CooldownManager,
    deliver: Callable[[Path], bool],
    pre_roll_frames: List[np.ndarray],
    frame_source: Callable[[], np.ndarray],
    fps: float,
    resolution: Tuple[int, int],
) -> None:
    """Record one clip and, unless `in_cooldown`, attempt delivery.

    Pure(ish) core of the "what happens for one confirmed motion event"
    logic - no threading/asyncio here, so it's directly unit-testable via
    injected fakes for `recorder`/`deliver`.
    """
    try:
        clip_path = recorder.record_event(
            pre_roll_frames=pre_roll_frames,
            frame_source=frame_source,
            fps=fps,
            resolution=resolution,
        )
    except RecordingFailedError as exc:
        logger.error("Recording failed: %s", exc)
        return

    if in_cooldown:
        # Per spec: motion during cooldown "may be logged but must not be
        # sent". Discarding (rather than queueing to pending/) respects the
        # disk quota, since this clip was never going to be sent anyway.
        clip_path.unlink(missing_ok=True)
        logger.info("Discarded clip '%s' recorded during cooldown", clip_path)
        return

    saved_path = storage_manager.save_temp(clip_path)
    storage_manager.enforce_quota()

    if deliver(saved_path):
        storage_manager.mark_delivered(saved_path)
        cooldown_manager.record_delivery_success()
    else:
        storage_manager.mark_failed(saved_path)


def process_frame_for_motion(
    frame: np.ndarray,
    *,
    motion_detector: MotionDetector,
    pre_roll: Deque[np.ndarray],
    cooldown_manager: CooldownManager,
    storage_manager: StorageManager,
    recorder_factory: Callable[[], Recorder],
    deliver: Callable[[Path], bool],
    fps: float,
    resolution: Tuple[int, int],
    frame_source: Callable[[], np.ndarray],
    state: OrchestratorState,
) -> Optional[MotionEvent]:
    """Feed one frame through the detector and, on a confirmed event, decide
    whether to skip it entirely (notifications disabled), record-but-discard
    (cooldown), or record-and-deliver.
    """
    pre_roll.append(frame)
    event = motion_detector.process_frame(frame)
    if event is None:
        return None

    state.last_motion_at = event.timestamp

    if not cooldown_manager.notifications_enabled():
        logger.info(
            "Motion event at %.3f skipped entirely: notifications disabled", event.timestamp
        )
        return event

    in_cooldown = cooldown_manager.is_in_cooldown()
    if in_cooldown:
        logger.info(
            "Motion event at %.3f occurred during cooldown: recording but will not send",
            event.timestamp,
        )

    try:
        recorder = recorder_factory()
    except FfmpegNotFoundError as exc:
        logger.error("Cannot record motion event: %s", exc)
        return event

    motion_detector.is_recording_active = True
    try:
        handle_motion_event(
            in_cooldown=in_cooldown,
            recorder=recorder,
            storage_manager=storage_manager,
            cooldown_manager=cooldown_manager,
            deliver=deliver,
            pre_roll_frames=list(pre_roll),
            frame_source=frame_source,
            fps=fps,
            resolution=resolution,
        )
    finally:
        motion_detector.is_recording_active = False

    return event


def _drain_frames_until_stopped(
    frame_source_factory: Callable[[], ContextManager[Callable[[], np.ndarray]]],
    on_frame: Callable[[np.ndarray, Callable[[], np.ndarray]], None],
    stop_event: threading.Event,
    backoff_seconds: float = _FRAME_SOURCE_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Keep pulling frames until `stop_event` is set.

    A frame-source outage (camera disconnected, MediaMTX down) or any other
    unexpected error is logged and backed off from, never allowed to
    propagate and crash the whole process - this is what satisfies "a
    simulated camera disconnect or FFmpeg failure is logged and does not
    terminate the whole service" at the pipeline level.
    """
    while not stop_event.is_set():
        try:
            with frame_source_factory() as read_frame:
                while not stop_event.is_set():
                    frame = read_frame()
                    on_frame(frame, read_frame)
        except FrameSourceError as exc:
            logger.error(
                "Motion pipeline: frame source unavailable (%s); retrying in %.0fs",
                exc,
                backoff_seconds,
            )
            sleep(backoff_seconds)
        except Exception:
            logger.exception("Motion pipeline iteration crashed; restarting after backoff")
            sleep(backoff_seconds)


def _deliver_sync(
    application: Application, clip_path: Path, config: AppConfig, loop: asyncio.AbstractEventLoop
) -> bool:
    """Bridge a synchronous call (from the motion-pipeline thread) into the
    async `send_video_note_with_retry()` running on the main event loop.
    """
    future = asyncio.run_coroutine_threadsafe(
        send_video_note_with_retry(application.bot, config.telegram.chat_id, clip_path, config),
        loop,
    )
    try:
        return future.result()
    except Exception:
        logger.exception("Delivery coroutine raised unexpectedly for '%s'", clip_path)
        return False


def _motion_pipeline_thread(
    config: AppConfig,
    cooldown_manager: CooldownManager,
    storage_manager: StorageManager,
    application: Application,
    state: OrchestratorState,
    loop: asyncio.AbstractEventLoop,
    stop_event: threading.Event,
) -> None:
    motion_detector = MotionDetector(config.motion, fps=config.camera.framerate)
    pre_roll_len = max(1, round(config.recording.pre_roll_seconds * config.camera.framerate))
    pre_roll: Deque[np.ndarray] = deque(maxlen=pre_roll_len)
    resolution = (config.camera.resolution[0], config.camera.resolution[1])

    def on_frame(frame: np.ndarray, read_frame: Callable[[], np.ndarray]) -> None:
        process_frame_for_motion(
            frame,
            motion_detector=motion_detector,
            pre_roll=pre_roll,
            cooldown_manager=cooldown_manager,
            storage_manager=storage_manager,
            recorder_factory=lambda: Recorder(config.recording),
            deliver=lambda clip_path: _deliver_sync(application, clip_path, config, loop),
            fps=config.camera.framerate,
            resolution=resolution,
            frame_source=read_frame,
            state=state,
        )

    _drain_frames_until_stopped(
        frame_source_factory=lambda: live_frame_source(config),
        on_frame=on_frame,
        stop_event=stop_event,
    )


# --- Startup / shutdown ------------------------------------------------------


async def _run(
    config: AppConfig,
    cooldown_manager: CooldownManager,
    storage_manager: StorageManager,
    application: Application,
    state: OrchestratorState,
) -> None:
    loop = asyncio.get_running_loop()
    stop_event = threading.Event()

    motion_thread = threading.Thread(
        target=_motion_pipeline_thread,
        args=(config, cooldown_manager, storage_manager, application, state, loop, stop_event),
        name="catcam-motion-pipeline",
        daemon=True,
    )
    motion_thread.start()

    retry_task = asyncio.create_task(
        _retry_queue_loop(config, cooldown_manager, storage_manager, application)
    )

    stop_signal = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_signal.set)
        except NotImplementedError:
            pass  # Not supported on this platform (e.g. Windows) - Ctrl+C still works via asyncio.run.

    async with application:
        await application.start()
        await application.updater.start_polling()
        logger.info("CatCam orchestrator running (camera.type=%s)", config.camera.type)
        try:
            await stop_signal.wait()
        finally:
            logger.info("Shutting down...")
            stop_event.set()
            retry_task.cancel()
            await application.updater.stop()
            await application.stop()
            motion_thread.join(timeout=5.0)


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.logging)
    logger.info("CatCam starting up (camera.type=%s)", config.camera.type)

    cooldown_manager = CooldownManager(config.cooldown)
    storage_manager = StorageManager(config.recording)
    state = OrchestratorState()

    application = build_application(
        config,
        cooldown_manager,
        storage_manager,
        last_motion_at_provider=lambda: state.last_motion_at,
    )

    asyncio.run(_run(config, cooldown_manager, storage_manager, application, state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
