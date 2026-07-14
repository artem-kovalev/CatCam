"""Motion detection.

Wraps OpenCV's MOG2 background subtractor behind a small state machine that
turns a raw per-frame "is there motion right now" signal into a single
`MotionEvent` per sustained motion episode — not one per frame — while
respecting a region of interest and never overlapping an in-progress
recording.

ROI note: `MotionConfig.roi` (task 1's schema, `config.py`) is
`[x, y, width, height]` in pixels, not normalized fractions — this module
follows that already-shipped, already-tested schema rather than normalized
coordinates, since changing it now would ripple into task 1's config schema,
tests, and docs for no functional benefit at this frame-resolution-fixed
stage of the project.
"""

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

from .config import MotionConfig

logger = logging.getLogger("catcam.motion")

# A candidate foreground blob smaller than this fraction of the analyzed
# (ROI or full-frame) area is treated as noise (leaves, IR reflections,
# sensor grain) rather than real motion.
_MIN_CONTOUR_AREA_FRACTION = 0.005

# MOG2's varThreshold: lower = more sensitive to small pixel changes.
# Map config's 0-100 (higher = more sensitive) onto a practical 4-100 range.
_VAR_THRESHOLD_MIN = 4.0
_VAR_THRESHOLD_MAX = 100.0


@dataclass
class MotionEvent:
    """A single confirmed, sustained motion episode."""

    timestamp: float
    # (x, y, width, height) in full-frame pixel coordinates.
    bbox: Tuple[int, int, int, int]
    # Relative size of the triggering blob vs. the analyzed area, 0.0-1.0.
    confidence: float


def _sensitivity_to_var_threshold(sensitivity: int) -> float:
    sensitivity = max(0, min(100, sensitivity))
    span = _VAR_THRESHOLD_MAX - _VAR_THRESHOLD_MIN
    return _VAR_THRESHOLD_MAX - (sensitivity / 100.0) * span


class MotionDetector:
    """Stateful motion detector; call `process_frame()` once per analyzed frame.

    `is_recording_active` is a plain public attribute the caller must set to
    `True` as soon as it starts recording a clip for a returned `MotionEvent`,
    and back to `False` once that recording finishes — this is what prevents
    a second, overlapping `MotionEvent` from firing while a recording for the
    current motion episode is already in progress.
    """

    def __init__(
        self,
        config: MotionConfig,
        fps: float = 15.0,
        classifier: Optional[Callable[[np.ndarray], bool]] = None,
    ):
        self._config = config
        self._classifier = classifier
        self._min_duration_frames = max(1, round(config.min_duration_seconds * fps))
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            detectShadows=False,
            varThreshold=_sensitivity_to_var_threshold(config.sensitivity),
        )
        self._consecutive_motion_frames = 0
        # True once a MotionEvent has fired (or been classifier-rejected) for
        # the current unbroken run of motion frames, so we don't re-evaluate
        # every frame until motion drops out and a fresh episode begins.
        self._decided_for_current_motion = False

        self.is_recording_active = False

    def _roi_crop(self, frame: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int]]:
        if self._config.roi is None:
            return frame, (0, 0)
        x, y, w, h = self._config.roi
        return frame[y : y + h, x : x + w], (x, y)

    def process_frame(self, frame: np.ndarray) -> Optional[MotionEvent]:
        roi_frame, (offset_x, offset_y) = self._roi_crop(frame)
        fg_mask = self._bg_subtractor.apply(roi_frame)

        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        roi_area = roi_frame.shape[0] * roi_frame.shape[1]
        min_area = _MIN_CONTOUR_AREA_FRACTION * roi_area

        best_contour = None
        best_area = 0.0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area >= min_area and area > best_area:
                best_contour = contour
                best_area = area

        if best_contour is None:
            self._consecutive_motion_frames = 0
            self._decided_for_current_motion = False
            return None

        self._consecutive_motion_frames += 1

        if self._consecutive_motion_frames < self._min_duration_frames:
            return None
        if self._decided_for_current_motion:
            return None
        if self.is_recording_active:
            return None

        x, y, w, h = cv2.boundingRect(best_contour)
        bbox = (x + offset_x, y + offset_y, w, h)
        confidence = min(1.0, best_area / roi_area)

        if self._classifier is not None:
            self._decided_for_current_motion = True
            crop = roi_frame[y : y + h, x : x + w]
            if not self._classifier(crop):
                logger.info(
                    "Motion candidate rejected by classifier: bbox=%s confidence=%.3f",
                    bbox,
                    confidence,
                )
                return None
        else:
            self._decided_for_current_motion = True

        event = MotionEvent(timestamp=time.time(), bbox=bbox, confidence=confidence)
        logger.info(
            "Motion event: timestamp=%.3f bbox=%s confidence=%.3f",
            event.timestamp,
            event.bbox,
            event.confidence,
        )
        return event
