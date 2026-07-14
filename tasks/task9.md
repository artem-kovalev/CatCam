# Task 9 — Orchestration, logging, health, retry queue

## Status: Done

## Goal

Wire all components into a single running service: camera → motion detection → recording → video-note conversion → cooldown-gated Telegram delivery, with structured logging, log rotation, a retry queue for failed sends, and resilience to temporary network outages.

## Depends on

Tasks 2, 4, 5, 6, 7, 8 (all core components).

## Spec references

- "Non-Functional Requirements" — stability during outages, retry queue, structured logs, log rotation.
- "Event Recording" — preserve unsent files for retry.
- "Delivery Rate Limit" — motion during cooldown is logged, not sent.

## Assumptions

- Single-process, single-threaded-per-concern design using `asyncio` (matches `python-telegram-bot` v20's async model): one task loop for the motion-detection/recording pipeline, one for the bot's update polling, one for the retry-queue drain — all in one `main.py` event loop, avoiding multi-process complexity given Pi 4 has ample headroom for this workload.
- Retry queue = `StorageManager.list_pending()` (task 5) drained periodically (e.g. every 60s) by attempting delivery of each pending file; on success, `mark_delivered` + `CooldownManager.record_delivery_success()`; on failure (network error), left in place for the next cycle — this naturally survives outages without a separate persistent queue implementation.
- Structured logs: JSON-lines format via Python `logging` + a custom `Formatter` (or `python-json-logger` if added as a dependency), one logger per module (`catcam.motion`, `catcam.recorder`, `catcam.telegram`, etc.), rotated via `logging.handlers.RotatingFileHandler` (size-based, e.g. 10MB × 5 backups) configured from `LoggingConfig`.
- `health.py` aggregates: camera availability (via `camera.py`'s `is_available()`), stream process status (task 3's health check), last motion event time, cooldown state, disk usage — exposed both to `/status` (task 8) and to `scripts/diagnose.sh` (task 10).
- ~~One task loop for the motion-detection/recording pipeline, all in one `main.py` event loop.~~ **Refined**: the motion pipeline (frame reads, OpenCV processing, `Recorder`'s blocking FFmpeg subprocess calls) has no natural `await` points, so running it directly on the asyncio event loop would stall Telegram polling and the retry-queue drain for as long as a recording takes. It runs in its own dedicated background thread instead, bridging into the event loop only for the async Telegram send via `asyncio.run_coroutine_threadsafe()`. The retry-queue drain (genuinely just periodic awaits, no blocking calls) *is* a plain `asyncio.Task` as originally described. See `docs/architecture.md`'s "Why a background thread" section.
- Telegram-API-unreachable-at-*startup* (as opposed to during ongoing operation) is out of scope here: `Application.initialize()` calls Telegram's `getMe()` to validate the token, and if that fails (invalid token, DNS down, etc.) the whole process exits rather than retrying indefinitely. This is a deliberate boundary, not an oversight — task 10's systemd unit (`Restart=on-failure`) is the layer responsible for retrying a failed *startup*; `main.py`'s own resilience (background-thread backoff, retry-queue) is for failures *during* an already-running process, which is what the acceptance criteria below actually test.

## Steps

1. Implement `src/catcam/logging_config.py`: `setup_logging(config: LoggingConfig)` configuring root + per-module loggers, JSON formatter, rotating file handler plus console handler; a redaction filter that scrubs any string matching the bot token pattern before it's written (defense in depth alongside never logging the token directly).
2. Implement `src/catcam/health.py`: `get_status() -> HealthStatus` (dataclass) pulling live state from camera, streaming health check, cooldown manager, and `shutil.disk_usage`.
3. Implement `src/catcam/main.py`:
   - Startup: load config (task 1), set up logging, initialize camera, motion detector, recorder, storage manager, cooldown manager, telegram bot.
   - Motion pipeline loop: read frame → `MotionDetector.process_frame` → on `MotionEvent` (and not already recording): if `CooldownManager.notifications_enabled()` is False, log and skip; else record clip (task 5) → convert to video note (task 6) → attempt delivery; on delivery success, `record_delivery_success()`; if in cooldown, still record+log the event (per spec: "may be logged but must not be sent") but skip the send step entirely — clip is discarded or optionally kept per a config flag, defaulting to discard to respect the disk quota.
   - Delivery attempts go through a single `send_video_note_with_retry()` path shared by both the live pipeline and the retry-queue drain, so failures always land in `storage/pending/` uniformly.
   - Background retry-queue task (asyncio task) draining `storage/pending/` on an interval, independent of new motion events, so temporary internet outages don't lose events — they're simply delivered late once connectivity returns.
   - Top-level exception handling per subsystem so one component's failure (e.g. camera unplugged) logs and retries/backs off rather than crashing the whole process.
4. Write `docs/architecture.md`: component diagram (text-based is fine) showing the data flow above, explaining the cooldown-during-recording semantics, retry-queue design, and logging approach.

## Acceptance criteria

- [x] A confirmed motion event outside cooldown results in a delivered video note and a cooldown reset on confirmed Telegram success.
- [x] A confirmed motion event during cooldown is logged but not sent, and does not reset the cooldown timer.
- [x] A failed send (simulated network failure) leaves the clip in `storage/pending/` and a later successful retry delivers it and updates cooldown state.
- [x] Logs are structured (JSON-lines), rotate per configured size/count, and never contain the bot token.
- [x] `/status` (task 8) reflects real-time health data from `health.py`.
- [x] A simulated camera disconnect or FFmpeg failure is logged and does not terminate the whole service.

## Result

Implemented `src/catcam/logging_config.py` (extended), `src/catcam/health.py`,
`src/catcam/frame_source.py`, and `src/catcam/main.py`:

- **Logging** (`logging_config.setup_logging(config)`): configures the
  shared `"catcam"` logger (every module logs through a child, e.g.
  `catcam.motion`) with a JSON-lines formatter (`_JsonFormatter`) on both a
  `RotatingFileHandler` (sized/rotated from `LoggingConfig.max_bytes`/
  `backup_count`) and a console handler, plus a redaction filter
  (`_RedactionFilter`) attached to *both handlers* — not to the logger —
  since a `Logger`'s own filters only run for records originating on that
  exact logger object, not on descendant loggers that merely propagate
  records up to shared handlers (this was caught by a failing test during
  implementation; see Errors below). `propagate = False` keeps records off
  Python's real root logger.
- **`health.py`**: `get_status(config, cooldown_manager, storage_manager,
  last_motion_at=None) -> HealthStatus` — a plain dataclass pulling camera
  availability, MediaMTX stream readiness, cooldown/notifications state,
  and `storage_dir`+`pending_dir` usage vs. quota plus real filesystem free
  space (`shutil.disk_usage`, walking up to the nearest existing ancestor
  directory since `storage_dir` may not exist yet). `format_status()`
  renders it as the text `/status` sends. Task 8's `telegram_bot.py` was
  updated to call `health.get_status()`/`format_status()` directly instead
  of duplicating the camera/stream-check logic it had before — the
  `detector_status_provider: Callable[[], str]` seam from task 8 was
  replaced with a narrower `last_motion_at_provider: Callable[[], Optional[float]]`
  so `health.py` (not each caller) owns the "how long ago" formatting.
- **`frame_source.py`** (new, extracted from task 8's `telegram_bot.py`):
  `live_frame_source(config) -> ContextManager[Callable[[], np.ndarray]]` —
  the RTSP-preferred / direct-camera-fallback frame acquisition logic is
  needed by both `/snapshot`+`/record` (task 8) and the continuous motion
  pipeline (this task), so it was promoted out of `telegram_bot.py` into its
  own module rather than duplicated or reached into as a private import.
  `telegram_bot.py` now imports `FrameSourceError as SnapshotError` and
  `live_frame_source as _live_frame_source` to keep its existing public
  names (and task 8's tests) unchanged.
- **`main.py`**: the orchestrator. Since the motion pipeline is inherently
  blocking (frame reads, OpenCV, `Recorder`'s synchronous FFmpeg subprocess)
  and `python-telegram-bot` needs its event loop to keep turning for update
  polling, the pipeline runs in its own dedicated background thread
  (`_motion_pipeline_thread`), bridging into the event loop only for the
  async Telegram send via `asyncio.run_coroutine_threadsafe()`
  (`_deliver_sync`) — see the superseded-assumption note above and
  `docs/architecture.md`'s "Why a background thread" section for the full
  rationale. The retry-queue drain has no blocking calls of its own, so it
  runs as a plain `asyncio.Task` (`_retry_queue_loop`) alongside the bot's
  manually-driven `Application` lifecycle (`initialize`/`start`/
  `updater.start_polling()` — the documented lower-level alternative to
  `run_polling()` for coexisting with other asyncio code).
  - Per-motion-event decision logic is split into two directly
    unit-testable, dependency-injected functions: `process_frame_for_motion()`
    (gating: skip entirely if notifications disabled; record-but-discard if
    in cooldown; otherwise record-and-deliver) and `handle_motion_event()`
    (the record/discard/deliver/mark_delivered-or-failed mechanics), both
    free of threading/asyncio so tests can inject fake recorders/deliver
    callables directly.
  - `send_video_note_with_retry(bot, chat_id, clip_path, config)` is the
    single delivery path shared by both the live pipeline and the
    retry-queue drain, exactly as the Assumptions specify.
  - `_drain_frames_until_stopped()` is the resilience wrapper: any
    `FrameSourceError` (camera disconnected, MediaMTX unreachable) or other
    unexpected exception is logged and backed off from rather than raised,
    then the frame source is reconnected — verified against a real (missing)
    camera in a manual smoke-test run (see Commands executed below), not
    just mocked in unit tests.
- **Modified**: `telegram_bot.py` (see above); `storage.py` gained a public
  `disk_usage_mb()` method (task 5's already-Done file) so `health.py` and
  `/status` share one implementation of the directory-size summation
  instead of three separate copies.
- Created files:
  - `src/catcam/health.py`
  - `src/catcam/frame_source.py`
  - `src/catcam/main.py`
  - `tests/test_logging_config.py`
  - `tests/test_health.py`
  - `tests/test_main.py`
  - `docs/architecture.md`
- Modified files:
  - `src/catcam/logging_config.py` (added `setup_logging()`, `_JsonFormatter`,
    `_RedactionFilter`; `redact()` unchanged)
  - `src/catcam/storage.py` (added public `disk_usage_mb()`)
  - `src/catcam/telegram_bot.py` (`/status` now delegates to `health.py`;
    `_live_frame_source`/`SnapshotError` now re-exported from
    `frame_source.py`; `build_application()`'s `detector_status_provider`
    param renamed to `last_motion_at_provider`; standalone `main()` now
    calls `setup_logging()` instead of `logging.basicConfig()`)
  - `.env.example` / `docs/configuration.md` (documented
    `CATCAM_STREAM_VIEW_PASSWORD` as shared by task 8 and task 9; added a
    "Tuning guidance" block under the `logging` table)
  - `tasks/task9.md` (this file: Status, Assumptions, acceptance criteria,
    Result)
  - `tasks/summary.md` (status table)
- Commands executed:
  - `python -m pytest tests/test_logging_config.py tests/test_health.py tests/test_main.py -v` → 25/25 passed
  - `python -m pytest tests/ -q` → 113 passed, 1 skipped (full suite, tasks
    1–9 combined; the 1 skip is task 6's pre-existing real-FFmpeg
    integration test, unrelated to this task)
  - Manual smoke test: `python -m catcam.main` with a real (fake-but-
    well-formed) Telegram token and no camera/MediaMTX running on this dev
    machine. Confirmed live (not mocked): JSON-lines log output; the motion
    pipeline trying the RTSP feed, failing, falling back to a direct camera
    open, failing that too (`rpicam-vid not found`), logging an ERROR, and
    backing off — without crashing the process. The process did eventually
    exit, but only because `Application.initialize()` correctly rejected
    the fake token via a real call to Telegram's `getMe()` — expected, not
    a bug (see the startup-resilience Assumption above).
- Test results: all passing. `tests/test_logging_config.py` (8 tests)
  covers: `redact()` on bare/URL-embedded tokens and normal text;
  JSON-lines structure and field content; the token never appearing in the
  log file even when passed straight to `logger.error(...)`; rotation
  handler configured from `LoggingConfig`; and the configured level being
  respected. `tests/test_health.py` (4 tests) covers: `get_status()`
  aggregating camera/stream/cooldown/storage state correctly in both the
  "healthy" and "degraded" (camera missing, stream down, in cooldown,
  notifications disabled) cases, and `format_status()`'s "no motion yet"
  vs. "last motion Ns ago" text. `tests/test_main.py` (17 tests) covers:
  `handle_motion_event()`'s three outcomes (delivered+cooldown-reset,
  cooldown-discard-without-sending, failed-delivery-marks-pending-without-
  cooldown-reset) plus a recording-failure case that logs and returns
  without raising; `process_frame_for_motion()`'s gating (notifications-
  disabled skip, cooldown record-but-discard, no-event no-op);
  `send_video_note_with_retry()`'s success/fallback-to-raw-clip/Telegram-
  error-returns-false cases; `drain_retry_queue_once()` delivering pending
  clips and updating cooldown, and leaving failed ones in place; and
  `_drain_frames_until_stopped()` logging and continuing past a simulated
  `FrameSourceError` rather than raising, plus not calling the frame source
  at all once already stopped.
- Errors and fixes:
  - First draft of the redaction filter was attached via
    `root.addFilter(...)` on the `"catcam"` logger itself. This is a real
    logging-hierarchy gotcha: a `Logger`'s own `filters` list only runs for
    records that *originate* on that exact `Logger` object (inside
    `Logger.handle()`), not for records from descendant loggers that merely
    *propagate* up to it — so nothing logged via `catcam.some_module` was
    ever actually filtered. Caught immediately by
    `test_setup_logging_never_writes_the_bot_token` failing (raw token
    present in the output); fixed by attaching the filter to each *handler*
    instead (handlers' filters run for every record that reaches them,
    regardless of origin), which is the standard correct pattern.
  - After extracting `frame_source.py` out of `telegram_bot.py`, an existing
    task-8 test (`test_status_reports_cooldown_and_notifications_state`)
    broke because it patched `catcam.telegram_bot.check_mediamtx_path`/
    `create_camera`, which no longer exist there post-refactor. Fixed by
    repointing the patches to `catcam.health.check_mediamtx_path`/
    `create_camera`, matching where `/status`'s logic now actually lives.
- Unresolved questions:
  - **Startup-time Telegram/network failure is not retried** — see the
    Assumptions note above; this is deliberately left to task 10's systemd
    `Restart=on-failure` policy rather than built into `main.py` itself.
    Worth revisiting if task 10 doesn't end up configuring that restart
    policy for some reason.
  - **Real hardware/network integration is still unverified**: no camera,
    no MediaMTX, and no real Telegram bot token exist in this development
    environment. The manual smoke test above exercised the resilience path
    for a *missing* camera/stream live, but a full end-to-end run (real
    motion → real recording → real Telegram delivery → real cooldown reset
    across a real restart) is deferred to task 12, consistent with every
    prior hardware-dependent task in this project.
  - **Thread/event-loop shutdown ordering** (`motion_thread.join(timeout=5.0)`
    after `stop_event.set()`) assumes the motion thread notices `stop_event`
    within one frame-read cycle; if `read_frame()` itself blocks
    indefinitely (e.g. a genuinely wedged RTSP connection rather than a
    clean failure), the thread could outlive the 5s join timeout. Since it's
    a daemon thread, process exit still isn't blocked by this, but a clean
    shutdown log message might not appear for that thread in that edge case.
