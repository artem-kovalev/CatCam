import numpy as np
import pytest

from catcam.config import MotionConfig
from catcam.motion import MotionDetector

FRAME_SHAPE = (240, 320, 3)
BACKGROUND_VALUE = 60
BLOB_VALUE = 220
# Enough identical frames for MOG2 to fully learn a static background before
# any test starts asserting on real motion.
PRIME_FRAMES = 80


def _background_frame() -> np.ndarray:
    return np.full(FRAME_SHAPE, BACKGROUND_VALUE, dtype=np.uint8)


def _frame_with_blob(x: int, y: int, w: int, h: int) -> np.ndarray:
    frame = _background_frame()
    frame[y : y + h, x : x + w] = BLOB_VALUE
    return frame


def _prime(detector: MotionDetector) -> None:
    background = _background_frame()
    for _ in range(PRIME_FRAMES):
        detector.process_frame(background)


def _make_detector(classifier=None, **config_kwargs) -> MotionDetector:
    config = MotionConfig(sensitivity=50, min_duration_seconds=0.5, roi=None)
    for key, value in config_kwargs.items():
        setattr(config, key, value)
    return MotionDetector(config, fps=10.0, classifier=classifier)


def test_static_frames_produce_no_event():
    detector = _make_detector()
    _prime(detector)

    background = _background_frame()
    for _ in range(20):
        assert detector.process_frame(background) is None


def test_sustained_motion_produces_single_event():
    detector = _make_detector()
    _prime(detector)

    events = []
    for i in range(12):
        frame = _frame_with_blob(x=50 + 2 * i, y=80, w=40, h=40)
        event = detector.process_frame(frame)
        if event is not None:
            events.append(event)

    assert len(events) == 1
    assert events[0].bbox[2] > 0
    assert events[0].bbox[3] > 0
    assert 0.0 < events[0].confidence <= 1.0


def test_motion_outside_roi_produces_no_event():
    detector = _make_detector(roi=[0, 0, 100, 100])
    _prime(detector)

    for i in range(12):
        # Blob stays entirely outside the [0, 0, 100, 100] ROI.
        frame = _frame_with_blob(x=200 + i, y=150, w=40, h=40)
        assert detector.process_frame(frame) is None


def test_no_event_while_recording_active():
    detector = _make_detector()
    _prime(detector)
    detector.is_recording_active = True

    for i in range(12):
        frame = _frame_with_blob(x=50 + 2 * i, y=80, w=40, h=40)
        assert detector.process_frame(frame) is None


def test_classifier_can_reject_candidate_event():
    detector = _make_detector(classifier=lambda crop: False)
    _prime(detector)

    events = []
    for i in range(12):
        frame = _frame_with_blob(x=50 + 2 * i, y=80, w=40, h=40)
        event = detector.process_frame(frame)
        if event is not None:
            events.append(event)

    assert events == []


def test_classifier_can_accept_candidate_event():
    seen_crops = []

    def classifier(crop: np.ndarray) -> bool:
        seen_crops.append(crop)
        return True

    detector = _make_detector(classifier=classifier)
    _prime(detector)

    events = []
    for i in range(12):
        frame = _frame_with_blob(x=50 + 2 * i, y=80, w=40, h=40)
        event = detector.process_frame(frame)
        if event is not None:
            events.append(event)

    assert len(events) == 1
    assert len(seen_crops) == 1
