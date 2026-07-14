# Task 4 — Motion detection

## Status: Done

## Goal

Detect significant motion in the camera feed with configurable sensitivity, region of interest, and minimum duration, while avoiding false positives and overlapping recordings, and maintaining an event log.

## Depends on

Task 2 (camera abstraction).

## Spec references

- "Motion Detection" (full section).
- "Optional additional implementation" — cat/not-cat classification (config-gated, non-required).

## Assumptions

- Baseline algorithm: OpenCV `cv2.createBackgroundSubtractorMOG2` (robust to gradual lighting change, standard choice for this use case) with a fallback simple frame-differencing mode selectable via config for lower-CPU devices — MOG2 is the default given Pi 4 has enough headroom.
- ~~ROI is expressed in config as normalized rectangle coordinates (`x, y, w, h` as fractions of frame size) so it survives resolution changes.~~ **Superseded during implementation:** task 1 (already Done) shipped `MotionConfig.roi` as `[x, y, width, height]` **in pixels**, already tested (`tests/test_config.py`) and documented (`docs/configuration.md`). Changing that to normalized fractions now would mean reopening task 1's already-Done schema/tests/docs for no functional benefit while `camera.resolution` is fixed at config time; `motion.py` follows the existing pixel-based schema instead. If resolution ever becomes runtime-changeable, revisit this.
- "Minimum motion duration" is implemented as: motion must be present in N consecutive analyzed frames (configurable) before an event is confirmed, to filter out single-frame noise (e.g. leaves, IR reflections).
- The optional cat/not-cat classifier, if enabled, runs only after the base motion+duration gate triggers (as a filter on candidate events), using a lightweight model (e.g. MobileNet-based TFLite) — this is explicitly out of scope for the base system to function, and `motion.py` must operate correctly with the classifier disabled (default).
- **Frame source (per task 3's camera-ownership contract, `docs/streaming.md`):** once `catcam-stream.service` is active (the normal always-on state per task 10), MediaMTX is the sole owner of the physical camera. Whatever wires frames into `MotionDetector.process_frame()` (task 9's `main.py`) must read them via `cv2.VideoCapture("rtsp://catcam-viewer:<password>@127.0.0.1:<rtsp_port>/cam")`, not via `create_camera()` — the latter will either raise `CameraBusyError` (USB) or race the driver (CSI) while streaming is running.

## Steps

1. Implement `src/catcam/motion.py`:
   - `MotionDetector` class wrapping the chosen backend, initialized from `MotionConfig` (sensitivity/threshold, ROI, min-duration frame count, frame sample rate).
   - `process_frame(frame) -> MotionEvent | None` returning a structured result (timestamp, bounding box, confidence) only once the min-duration gate is satisfied.
   - Internal state machine preventing a new `MotionEvent` from firing again while a prior event's recording is still in progress (single in-flight event at a time) — expose an `is_recording_active` flag/callback the caller sets.
   - Optional hook point `classifier: Callable[[frame], bool] | None` invoked only if configured; base detector must pass all tests and run with `classifier=None`.
   - Structured event logging (reuses `logging_config` from task 9; for this task, log via the standard `logging` module with a dedicated `catcam.motion` logger so it composes cleanly later).
2. Add unit tests `tests/test_motion.py` using synthetic frames (numpy arrays) — static frames produce no event; a sequence with a moving synthetic blob inside the ROI for ≥ min-duration produces exactly one `MotionEvent`; motion outside the configured ROI produces no event; motion while `is_recording_active=True` does not produce a second event.
3. Document sensitivity/ROI/min-duration tuning guidance and the optional classifier flag in `docs/configuration.md` (extend, don't duplicate, task 1's file).

## Acceptance criteria

- [x] Motion sensitivity, ROI, and minimum duration are all configurable via YAML/`.env` per task 1's config schema.
- [x] A single sustained motion event yields exactly one `MotionEvent`, not one per frame.
- [x] Motion outside the ROI is ignored.
- [x] No second event fires while a recording is already in progress for the current event.
- [x] Optional classifier is fully decoupled — system works with it absent/disabled.
- [x] `tests/test_motion.py` passes without real camera hardware.
- [x] Motion events are logged with enough detail (time, bbox/confidence) to build the required event log.

## Result

Implemented `MotionDetector` in `src/catcam/motion.py`, wrapping OpenCV
`cv2.createBackgroundSubtractorMOG2` (`detectShadows=False`) behind a small
state machine:

- **Sensitivity**: `MotionConfig.sensitivity` (0-100) is linearly mapped onto
  MOG2's `varThreshold` (100 → 4, i.e. higher config sensitivity = lower
  variance threshold = more sensitive).
- **ROI**: `MotionConfig.roi` (`[x, y, w, h]` pixels, or `None` for full
  frame — task 1's existing, already-tested schema; see the superseded
  Assumptions bullet above) crops the frame *before* it reaches the
  subtractor, so out-of-ROI motion is structurally invisible, not
  post-filtered.
- **Minimum duration**: `MotionConfig.min_duration_seconds` is converted to a
  consecutive-frame count using an `fps` argument passed into
  `MotionDetector.__init__` by the caller (intended to be
  `config.camera.framerate`) — motion must hold for that many consecutive
  analyzed frames before a `MotionEvent` fires. This isn't a new config field:
  reusing `camera.framerate` avoids duplicating frame-rate configuration in
  two sections.
- **Single event per episode**: an internal `_decided_for_current_motion`
  flag latches once a frame satisfies the duration gate, and only resets
  when a later frame reports no motion at all — so one sustained motion
  episode yields exactly one `MotionEvent` regardless of how many more
  frames the motion continues for.
- **No overlapping recordings**: `is_recording_active` is a plain public
  attribute the caller sets to `True`/`False` around its own recording
  lifecycle; while `True`, `process_frame()` never returns an event, even if
  the duration gate would otherwise be satisfied.
- **Noise floor**: candidate contours smaller than 0.5% of the analyzed area
  are discarded (fixed constant, not config-exposed — filters sensor
  grain/small reflections per the Assumptions).
- **Optional classifier**: `classifier: Callable[[np.ndarray], bool] | None`
  constructor argument, invoked once per motion episode (not per frame) with
  the cropped bbox region, only after the base gate is satisfied; returning
  `False` suppresses that episode's event without re-triggering re-evaluation
  every frame. No YAML/`.env` key added for this — out of scope per the
  Assumptions ("explicitly out of scope for the base system"); documented in
  `docs/configuration.md` as an integration point for a later task.
- **Logging**: a dedicated `catcam.motion` logger emits one INFO line per
  fired `MotionEvent` (timestamp, bbox, confidence) and one per
  classifier-rejected candidate.

`MotionConfig`/`config.py` needed no schema changes — `sensitivity`,
`min_duration_seconds`, and `roi` already existed from task 1 and are already
config/`.env`-overridable and validated.

- Created files:
  - `src/catcam/motion.py`
  - `tests/test_motion.py`
- Modified files:
  - `docs/configuration.md` (added a "Tuning guidance" block under the
    `motion` table: sensitivity→varThreshold mapping, ROI crop-before-analysis
    behavior, min-duration→frame-count conversion via `camera.framerate`, the
    fixed noise floor, and the classifier hook contract)
  - `tasks/task4.md` (this file: Status, Assumptions correction, Result)
  - `tasks/summary.md` (status table)
- Commands executed:
  - `python -m pytest tests/test_motion.py -v` — 6/6 passed
  - `python -m pytest tests/ -v` — 29/29 passed (full suite, tasks 1-4 combined)
- Test results: all passing. `tests/test_motion.py` covers: static frames
  produce no event; a sustained synthetic moving blob inside the (full-frame)
  ROI for ≥ min-duration produces exactly one `MotionEvent` with a sane
  bbox/confidence; a sustained blob entirely outside a configured ROI
  produces no event across the whole sequence; no event fires while
  `is_recording_active=True` even though the duration gate is otherwise
  satisfied; a classifier returning `False` suppresses the event; a
  classifier returning `True` still allows exactly one event through and is
  invoked exactly once per episode (not per frame). All tests use synthetic
  numpy frames — no real camera hardware needed, MOG2 primed with 80
  identical background frames before each assertion sequence to avoid
  first-frame convergence noise.
- Unresolved questions:
  - **ROI coordinate system deviation from the original Assumptions text**:
    resolved above by following task 1's already-shipped pixel-based schema
    instead of normalized fractions — see the struck-through Assumptions
    bullet. Not blocking, but flagged in case a later task assumed normalized
    coordinates.
  - **Real-world sensitivity/threshold tuning** (the `varThreshold` mapping,
    the fixed 0.5% noise-floor constant, MOG2's actual behavior under real
    lighting changes, IR-cut filter switching at night, moving shadows from
    trees, etc.) is unverified on this hardware-less dev machine — synthetic
    frames validate the state machine's logic, not real-camera-footage
    detection quality. Deferred to task 12's manual on-device verification,
    consistent with tasks 2/3's precedent.
  - **Frame source wiring**: `MotionDetector.process_frame()` itself is
    frame-source-agnostic (takes a raw `np.ndarray`); actually pulling frames
    from MediaMTX's RTSP output (per task 3's camera-ownership contract) or
    `create_camera()` (streaming stopped / local dev) and calling
    `process_frame()` in a loop is task 9's `main.py` orchestration
    responsibility, not this task's — `motion.py` exposes the pure
    per-frame API task 9 needs and nothing more.
  - **Classifier wiring**: only the hook point (`classifier` constructor arg)
    is implemented per the Assumptions ("explicitly out of scope for the
    base system"); an actual TFLite/MobileNet model, its config keys, and
    loading code are left for whichever future task/iteration enables it.
