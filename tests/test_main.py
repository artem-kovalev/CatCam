import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from catcam.config import (
    CooldownConfig,
    RecordingConfig,
    VideoNoteConfig,
)
from catcam.cooldown import CooldownManager
from catcam.frame_source import FrameSourceError
from catcam.main import (
    OrchestratorState,
    _drain_frames_until_stopped,
    drain_retry_queue_once,
    handle_motion_event,
    process_frame_for_motion,
    send_video_note_with_retry,
)
from catcam.recorder import RecordingFailedError
from catcam.storage import StorageManager
from catcam.video_note import VideoNoteConversionError


def _recording_config(tmp_path) -> RecordingConfig:
    return RecordingConfig(
        storage_dir=str(tmp_path / "recordings"),
        pending_dir=str(tmp_path / "pending"),
    )


def _cooldown_manager(tmp_path) -> CooldownManager:
    return CooldownManager(CooldownConfig(state_file=str(tmp_path / "cooldown.json")))


class _FakeAppConfig:
    """Minimal stand-in exposing only what send_video_note_with_retry needs."""

    def __init__(self):
        self.video_note = VideoNoteConfig()
        self.telegram = MagicMock(chat_id=555)


class _FakeBot:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent_video_notes = []
        self.sent_videos = []

    async def send_video_note(self, chat_id, video_note):
        if self.fail:
            raise RuntimeError("network error")
        self.sent_video_notes.append(chat_id)

    async def send_video(self, chat_id, video):
        if self.fail:
            raise RuntimeError("network error")
        self.sent_videos.append(chat_id)


# --- handle_motion_event -----------------------------------------------------


def _fake_recorder(clip_path):
    recorder = MagicMock()
    recorder.record_event.return_value = clip_path
    return recorder


def test_handle_motion_event_delivers_and_resets_cooldown_when_not_in_cooldown(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake clip")
    storage_manager = StorageManager(_recording_config(tmp_path))
    cooldown_manager = _cooldown_manager(tmp_path)

    delivered_paths = []

    def deliver(path):
        delivered_paths.append(path)
        return True

    handle_motion_event(
        in_cooldown=False,
        recorder=_fake_recorder(clip_path),
        storage_manager=storage_manager,
        cooldown_manager=cooldown_manager,
        deliver=deliver,
        pre_roll_frames=[],
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        fps=10.0,
        resolution=(320, 240),
    )

    assert len(delivered_paths) == 1
    assert cooldown_manager.is_in_cooldown() is True
    # Delivered clip should have been removed by mark_delivered.
    assert not delivered_paths[0].exists()


def test_handle_motion_event_during_cooldown_discards_without_sending(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake clip")
    storage_manager = StorageManager(_recording_config(tmp_path))
    cooldown_manager = _cooldown_manager(tmp_path)

    deliver = MagicMock(return_value=True)

    handle_motion_event(
        in_cooldown=True,
        recorder=_fake_recorder(clip_path),
        storage_manager=storage_manager,
        cooldown_manager=cooldown_manager,
        deliver=deliver,
        pre_roll_frames=[],
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        fps=10.0,
        resolution=(320, 240),
    )

    deliver.assert_not_called()
    assert cooldown_manager.is_in_cooldown() is False
    assert not clip_path.exists()  # discarded, not queued to pending
    assert storage_manager.list_pending() == []


def test_handle_motion_event_failed_delivery_marks_failed_without_resetting_cooldown(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"fake clip")
    storage_manager = StorageManager(_recording_config(tmp_path))
    cooldown_manager = _cooldown_manager(tmp_path)

    handle_motion_event(
        in_cooldown=False,
        recorder=_fake_recorder(clip_path),
        storage_manager=storage_manager,
        cooldown_manager=cooldown_manager,
        deliver=lambda path: False,
        pre_roll_frames=[],
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        fps=10.0,
        resolution=(320, 240),
    )

    assert cooldown_manager.is_in_cooldown() is False
    pending = storage_manager.list_pending()
    assert len(pending) == 1
    assert pending[0].name == "clip.mp4"


def test_handle_motion_event_recording_failure_is_logged_and_does_not_raise(tmp_path):
    storage_manager = StorageManager(_recording_config(tmp_path))
    cooldown_manager = _cooldown_manager(tmp_path)
    recorder = MagicMock()
    recorder.record_event.side_effect = RecordingFailedError("ffmpeg exploded")
    deliver = MagicMock()

    handle_motion_event(
        in_cooldown=False,
        recorder=recorder,
        storage_manager=storage_manager,
        cooldown_manager=cooldown_manager,
        deliver=deliver,
        pre_roll_frames=[],
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        fps=10.0,
        resolution=(320, 240),
    )

    deliver.assert_not_called()
    assert cooldown_manager.is_in_cooldown() is False


# --- process_frame_for_motion (gating logic) --------------------------------


class _FakeMotionEvent:
    def __init__(self, timestamp):
        self.timestamp = timestamp


def _make_state():
    return OrchestratorState()


def test_process_frame_skips_entirely_when_notifications_disabled(tmp_path):
    cooldown_manager = _cooldown_manager(tmp_path)
    cooldown_manager.disable_notifications()
    storage_manager = StorageManager(_recording_config(tmp_path))

    motion_detector = MagicMock()
    motion_detector.process_frame.return_value = _FakeMotionEvent(123.0)
    recorder_factory = MagicMock()
    deliver = MagicMock()
    state = _make_state()

    event = process_frame_for_motion(
        np.zeros((2, 2, 3), dtype=np.uint8),
        motion_detector=motion_detector,
        pre_roll=__import__("collections").deque(maxlen=5),
        cooldown_manager=cooldown_manager,
        storage_manager=storage_manager,
        recorder_factory=recorder_factory,
        deliver=deliver,
        fps=10.0,
        resolution=(320, 240),
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        state=state,
    )

    assert event is not None
    recorder_factory.assert_not_called()
    deliver.assert_not_called()
    assert state.last_motion_at == 123.0


def test_process_frame_records_but_does_not_deliver_during_cooldown(tmp_path):
    cooldown_manager = _cooldown_manager(tmp_path)
    cooldown_manager.record_delivery_success()  # now in cooldown
    storage_manager = StorageManager(_recording_config(tmp_path))

    clip_path = tmp_path / "evt.mp4"
    clip_path.write_bytes(b"data")
    recorder = MagicMock()
    recorder.record_event.return_value = clip_path
    recorder_factory = MagicMock(return_value=recorder)
    deliver = MagicMock()
    state = _make_state()

    motion_detector = MagicMock()
    motion_detector.process_frame.return_value = _FakeMotionEvent(456.0)

    from collections import deque

    process_frame_for_motion(
        np.zeros((2, 2, 3), dtype=np.uint8),
        motion_detector=motion_detector,
        pre_roll=deque(maxlen=5),
        cooldown_manager=cooldown_manager,
        storage_manager=storage_manager,
        recorder_factory=recorder_factory,
        deliver=deliver,
        fps=10.0,
        resolution=(320, 240),
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        state=state,
    )

    recorder.record_event.assert_called_once()
    deliver.assert_not_called()
    assert not clip_path.exists()


def test_process_frame_returns_none_when_no_event(tmp_path):
    cooldown_manager = _cooldown_manager(tmp_path)
    storage_manager = StorageManager(_recording_config(tmp_path))
    motion_detector = MagicMock()
    motion_detector.process_frame.return_value = None
    state = _make_state()

    from collections import deque

    event = process_frame_for_motion(
        np.zeros((2, 2, 3), dtype=np.uint8),
        motion_detector=motion_detector,
        pre_roll=deque(maxlen=5),
        cooldown_manager=cooldown_manager,
        storage_manager=storage_manager,
        recorder_factory=MagicMock(),
        deliver=MagicMock(),
        fps=10.0,
        resolution=(320, 240),
        frame_source=lambda: np.zeros((2, 2, 3), dtype=np.uint8),
        state=state,
    )

    assert event is None
    assert state.last_motion_at is None


# --- send_video_note_with_retry ---------------------------------------------


def test_send_video_note_with_retry_success(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"data")
    bot = _FakeBot()
    config = _FakeAppConfig()

    def _fake_convert(input_path, output_path, video_note_config):
        output_path.write_bytes(b"note")
        return output_path

    with patch("catcam.main.convert_to_video_note", side_effect=_fake_convert):
        delivered = asyncio.run(send_video_note_with_retry(bot, 555, clip_path, config))

    assert delivered is True
    assert bot.sent_video_notes == [555]
    # The temporary note file should be cleaned up afterward.
    assert not clip_path.with_name("clip_note.mp4").exists()


def test_send_video_note_with_retry_falls_back_to_raw_clip_on_conversion_failure(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"data")
    bot = _FakeBot()
    config = _FakeAppConfig()

    with patch(
        "catcam.main.convert_to_video_note",
        side_effect=VideoNoteConversionError("ffmpeg missing"),
    ):
        delivered = asyncio.run(send_video_note_with_retry(bot, 555, clip_path, config))

    assert delivered is True
    assert bot.sent_videos == [555]
    assert bot.sent_video_notes == []


def test_send_video_note_with_retry_returns_false_on_telegram_error(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"data")
    bot = _FakeBot(fail=True)
    config = _FakeAppConfig()

    with patch(
        "catcam.main.convert_to_video_note",
        side_effect=VideoNoteConversionError("ffmpeg missing"),
    ):
        delivered = asyncio.run(send_video_note_with_retry(bot, 555, clip_path, config))

    assert delivered is False


# --- retry queue -------------------------------------------------------------


def test_drain_retry_queue_once_delivers_pending_and_updates_cooldown(tmp_path):
    storage_manager = StorageManager(_recording_config(tmp_path))
    cooldown_manager = _cooldown_manager(tmp_path)

    pending_dir = tmp_path / "pending"
    pending_dir.mkdir(parents=True)
    clip_path = pending_dir / "old_clip.mp4"
    clip_path.write_bytes(b"data")

    async def deliver(path):
        return True

    asyncio.run(drain_retry_queue_once(storage_manager, cooldown_manager, deliver))

    assert storage_manager.list_pending() == []
    assert cooldown_manager.is_in_cooldown() is True


def test_drain_retry_queue_once_leaves_failed_deliveries_pending(tmp_path):
    storage_manager = StorageManager(_recording_config(tmp_path))
    cooldown_manager = _cooldown_manager(tmp_path)

    pending_dir = tmp_path / "pending"
    pending_dir.mkdir(parents=True)
    clip_path = pending_dir / "old_clip.mp4"
    clip_path.write_bytes(b"data")

    async def deliver(path):
        return False

    asyncio.run(drain_retry_queue_once(storage_manager, cooldown_manager, deliver))

    assert len(storage_manager.list_pending()) == 1
    assert cooldown_manager.is_in_cooldown() is False


# --- frame-source outage resilience -----------------------------------------


def test_drain_frames_logs_and_continues_after_simulated_camera_disconnect():
    stop_event = threading.Event()
    call_count = {"n": 0}

    def frame_source_factory():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise FrameSourceError("camera disconnected")

        class _Ctx:
            def __enter__(self_inner):
                # Setting stop_event here means the inner while-loop's
                # condition is already False by the time it would call
                # read_frame() - simulating "reconnected, then asked to stop".
                stop_event.set()
                return MagicMock()

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()

    sleeps = []

    _drain_frames_until_stopped(
        frame_source_factory=frame_source_factory,
        on_frame=lambda frame, read_frame: None,
        stop_event=stop_event,
        backoff_seconds=0.01,
        sleep=sleeps.append,
    )

    # The first (simulated disconnect) attempt must not have raised/crashed;
    # it should have backed off exactly once before the second attempt set
    # stop_event.
    assert sleeps == [0.01]
    assert call_count["n"] == 2


def test_drain_frames_stops_immediately_when_stop_event_already_set():
    stop_event = threading.Event()
    stop_event.set()
    factory = MagicMock()

    _drain_frames_until_stopped(
        frame_source_factory=factory,
        on_frame=MagicMock(),
        stop_event=stop_event,
    )

    factory.assert_not_called()
