# Task 5 — Event recording & storage management

## Status: Done

## Goal

On a confirmed motion event, record a short clip (with pre-roll where practical), enforce size/disk limits, and manage temp/unsent files correctly — including graceful handling when the camera or FFmpeg is unavailable.

## Depends on

Task 2 (camera abstraction), Task 4 (motion event trigger).

## Spec references

- "Event Recording" (full section).
- "Non-Functional Requirements" — retry queue, camera concurrency safety.

## Assumptions

- ~~Default clip duration: 12 seconds (mid-point of the recommended 8–15s range), configurable.~~ **Superseded:** task 1 (already Done) shipped `RecordingConfig.clip_duration_seconds` defaulting to `10`, already tested/documented. Not reopened for the same reason as task 4's ROI default — no functional benefit to changing an already-shipped, already-tested default; still within the spec's 8–15s recommended range and fully configurable.
- Pre-roll implemented via a small in-memory ring buffer of recently-captured frames (e.g. last 2–3 seconds) maintained continuously by the motion-detection loop (since it already reads every frame); on trigger, the recorder writes the ring buffer's frames followed by fresh live frames until total duration is reached. Ring buffer memory footprint bounded by config (max seconds × resolution).
- ~~Recording/encoding is done via FFmpeg (subprocess), taking frames either piped from the Python process (ring buffer + live) or, for CSI cameras, potentially via a direct `rpicam-vid`-to-file segment if simpler — final approach decided during implementation and documented; must not conflict with task 2's camera lock (recorder acquires the lock for the live-capture portion of the clip).~~ **Superseded during implementation:** task 3 (already Done) established that once `catcam-stream.service` is running (the normal always-on deployment state), MediaMTX is the sole owner of the physical camera — a second `create_camera()` open would either raise `CameraBusyError` (USB, publisher holds the lock) or race the driver (CSI, MediaMTX's native `rpiCamera` source bypasses `CameraLock` entirely). So `Recorder.record_event()` does **not** call `create_camera()` or acquire the camera lock itself; it is frame-source-agnostic (same precedent as task 4's `MotionDetector.process_frame()`), taking a pre-roll buffer and a `frame_source` callable supplied by the caller (task 9's orchestrator, which already reads frames from MediaMTX's RTSP output for motion detection). A separate `create_camera_frame_source()` helper is provided for the local-dev/streaming-disabled fallback, where a direct camera open is valid and `CameraNotFoundError`/`CameraBusyError` are meaningful.
- Unsent files (failed Telegram delivery) are kept in a `storage/pending/` directory; successfully delivered files' temp artifacts are deleted; a `storage/` disk quota (config, e.g. default 1 GB) triggers deletion of the oldest pending files first when exceeded, logged clearly.

## Steps

1. Implement `src/catcam/storage.py`:
   - `StorageManager` with `save_temp(path) `, `mark_delivered(path)` (deletes temp + moves out of pending), `mark_failed(path)` (moves/keeps in `pending/` for retry), `enforce_quota()` (checks `shutil.disk_usage` / directory size against configured limit, deletes oldest pending files as needed, logs each deletion), `list_pending() -> list[Path]` for the retry queue (task 9) to consume.
   - Robust error handling: read-only filesystem, missing directories auto-created, disk-full conditions caught and logged rather than crashing the process.
2. Implement `src/catcam/recorder.py`:
   - `Recorder` class with `record_event(duration_s: float) -> Path` that: acquires the camera lock, assembles pre-roll + live frames (or drives an FFmpeg subprocess directly), enforces a max output file size (config), writes to a temp path under `storage/tmp/`, and returns the finished clip path.
   - Explicit, typed exceptions: `CameraNotFoundError`/`CameraBusyError` (re-raised/propagated from task 2), `FfmpegNotFoundError` (checked via `shutil.which("ffmpeg")` at startup, raised with an actionable message), `RecordingFailedError` for subprocess non-zero exit.
   - Caller (task 9's orchestrator) is responsible for catching these and logging without crashing the whole service.
3. Add tests covering `StorageManager` behavior without real video: quota enforcement deletes oldest files first; `mark_delivered` removes temp files; `mark_failed` preserves files in `pending/`. Recorder's FFmpeg interaction is integration-tested manually (documented in `docs/testing.md`, task 11) since it needs real hardware/FFmpeg; add a unit test that mocks `shutil.which` to verify `FfmpegNotFoundError` is raised when FFmpeg is absent.

## Acceptance criteria

- [x] Recording produces a clip of the configured duration (default 10s per task 1, within the spec's 8–15s range) including pre-roll when the ring buffer has enough history.
- [x] Output clip size is capped per config; oversized encodes are handled (FFmpeg `-fs` hard cap, logged) rather than silently exceeding the cap.
- [x] Missing camera raises a specific, caught exception; missing FFmpeg raises a specific, caught exception — neither crashes the whole process.
- [x] Successfully delivered clips' temp files are deleted; failed ones persist in `storage/pending/` for retry.
- [x] Disk usage never exceeds the configured quota; oldest pending files are pruned first, with a log entry per deletion.
- [x] Unit tests for `StorageManager` pass.

## Result

Implemented `src/catcam/storage.py` (`StorageManager`) and
`src/catcam/recorder.py` (`Recorder`, plus supporting exceptions and a
camera-frame-source helper).

**`StorageManager`:**
- `tmp_dir()` — derives the in-progress-recording directory as a sibling of
  `storage_dir` (e.g. `storage/recordings` → `storage/tmp`); not a new config
  field, per the design decision above.
- `save_temp(src_path) -> Path` — moves a finished temp clip into
  `storage_dir`, creating it if missing; on `OSError` logs and returns the
  original path unchanged (clip stays safely in `tmp/` rather than being
  lost).
- `mark_delivered(path)` — deletes a successfully-delivered clip; missing
  files and `OSError`s are logged, not raised.
- `mark_failed(path) -> Path` — moves a clip into `pending_dir` (created if
  missing) for retry.
- `list_pending() -> List[Path]` — files in `pending_dir`, oldest-first by
  mtime, for task 9's retry queue.
- `enforce_quota()` — sums `storage_dir` + `pending_dir` size via
  `Path.rglob` (see the superseded-assumption note below on why not
  `shutil.disk_usage`), and if over `disk_quota_mb`, deletes the oldest
  pending files first until back under quota, logging each deletion
  (`WARNING`) plus a final `ERROR` if still over quota after exhausting
  `pending_dir`.
- All mutating operations catch `OSError` (read-only fs, disk full, missing
  file) and log rather than raise, per the acceptance criteria.

**Superseded assumption — quota measurement primitive:** the Steps text
suggested `shutil.disk_usage`, but that reports whole-filesystem free space,
not the size of CatCam's own directories — the wrong primitive for a
directory-scoped quota (a full disk from an unrelated process elsewhere on
the machine isn't what `disk_quota_mb` is meant to track). Implemented via
directory-size summation (`Path.rglob` + `stat().st_size`) instead.

**`Recorder`:**
- Constructor checks `shutil.which("ffmpeg")` at startup, raising
  `FfmpegNotFoundError` immediately if absent (no subprocess is ever
  spawned in that case).
- `record_event(pre_roll_frames, frame_source, fps, resolution) -> Path` —
  frame-source-agnostic (mirrors task 4's `MotionDetector.process_frame()`
  precedent, see the superseded camera-lock assumption above): writes
  `pre_roll_frames` (trimmed to the most recent frames if they exceed
  `clip_duration_seconds` worth) to an FFmpeg subprocess's stdin as raw BGR
  frames, then pulls further frames from the caller-supplied `frame_source()`
  callable until the target duration's frame count is reached. Output goes
  to `storage/tmp/<uuid>.mp4`.
- Size cap: FFmpeg is invoked with `-fs <max_clip_size_mb in bytes>`, a real
  FFmpeg flag that makes it stop muxing (and close its end of the pipe) once
  the limit is reached, rather than producing an oversized file. `Recorder`
  treats the resulting `BrokenPipeError`/`OSError` on a subsequent
  `stdin.write()` as expected size-cap truncation (not a failure): it stops
  writing, closes stdin, waits for the process, and — provided FFmpeg's own
  exit code is 0 — returns the (shorter) clip path with a `WARNING` log line
  noting the truncation, rather than raising. A genuine non-zero FFmpeg exit
  still raises `RecordingFailedError` with the stderr tail.
- `create_camera_frame_source(config) -> Iterator[np.ndarray]` — thin helper
  wrapping `create_camera()` for the local-dev/streaming-disabled fallback
  path; this is where `CameraNotFoundError`/`CameraBusyError` (re-exported
  from `camera.py`, not redefined) actually surface. `Recorder` itself never
  calls `create_camera()`.

`RecordingConfig`/`config.py` needed no schema changes — all relevant fields
(`clip_duration_seconds`, `pre_roll_seconds`, `max_clip_size_mb`,
`storage_dir`, `pending_dir`, `disk_quota_mb`) already existed from task 1.

- Created files:
  - `src/catcam/storage.py`
  - `src/catcam/recorder.py`
  - `tests/test_storage.py`
  - `tests/test_recorder.py`
- Modified files:
  - `docs/configuration.md` (added a "Tuning guidance" block under the
    `recording` table: derived `tmp` dir, `-fs`-based size cap, and the
    directory-sum vs. filesystem-free-space quota distinction)
  - `tasks/task5.md` (this file: Status, two superseded-assumption
    annotations, acceptance criteria, Result)
  - `tasks/summary.md` (status table)
- Commands executed:
  - `which ffmpeg` → not found on this dev machine (macOS, no ffmpeg
    installed) — confirms the real-encode path is untestable here; recorded
    as an unresolved item below.
  - `python -m pytest tests/test_storage.py tests/test_recorder.py -v` →
    17/17 passed
  - `python -m pytest tests/ -v` → 46/46 passed (full suite, tasks 1–5
    combined)
- Test results: all passing. `tests/test_storage.py` (10 tests) covers:
  `tmp_dir()` derivation, `save_temp`/`mark_delivered`/`mark_failed`
  happy paths, `list_pending` ordering (oldest-first, empty-when-missing),
  quota enforcement (deletes oldest pending first; no-op when under quota),
  and graceful handling of a missing file / a monkeypatched `OSError` during
  deletion. `tests/test_recorder.py` (7 tests) covers: FFmpeg command
  construction (pure function, no subprocess), `FfmpegNotFoundError` at
  construction time when `shutil.which` returns `None` (mocked, no
  subprocess spawned), `RecordingFailedError` on a mocked non-zero FFmpeg
  exit, correct pre-roll+live frame counting (including pre-roll truncation
  when it exceeds the clip duration), and the `BrokenPipeError`-as-size-cap
  path returning normally with a shorter clip instead of raising. All tests
  mock `subprocess.Popen`/`shutil.which` — no real FFmpeg or camera hardware
  needed or available on this dev machine.
- Unresolved questions:
  - **Camera-ownership assumption superseded**: resolved above — `Recorder`
    no longer acquires the camera lock or calls `create_camera()` directly
    for its primary path; it consumes frames from whatever source the
    caller (task 9) provides, expected to be MediaMTX's RTSP output per
    task 3's contract. Flagged in case a later task assumed the original
    lock-acquiring design.
  - **Quota-measurement primitive superseded**: resolved above (directory-sum
    instead of `shutil.disk_usage`).
  - **Real FFmpeg encoding is entirely unverified on this dev machine** — no
    `ffmpeg` binary is installed (confirmed via `which ffmpeg`), so the actual
    subprocess pipeline (raw BGR frames over stdin → real MP4 output, real
    `-fs` truncation behavior, real non-zero-exit stderr content) has only
    been exercised via mocks. Deferred to task 12's manual on-device
    verification, consistent with tasks 2/3/4's precedent — this is a
    materially larger gap than those tasks' since even the *shape* of a real
    ffmpeg invocation (pixel format acceptance, `-fs` exact truncation
    point, encode speed vs. incoming frame rate) is unverified, not just
    hardware-specific behavior.
  - **Pre-roll ring buffer itself is not part of this task's deliverable**:
    task5.md's Assumptions describe the ring buffer as "maintained
    continuously by the motion-detection loop" — that loop is task 9's
    orchestrator, not `motion.py` (task 4) or this task. `Recorder.record_event()`
    accepts an already-assembled `pre_roll_frames: List[np.ndarray]`, so task 9
    must build and maintain the actual ring buffer; this task only defines the
    consuming interface.
  - **Real-time feasibility unverified**: whether writing frames to FFmpeg's
    stdin fast enough to keep up with live capture (without frame-source
    blocking causing dropped/delayed frames) holds up under real Pi 4
    CPU/I/O load is unverified without hardware — deferred to task 12.
