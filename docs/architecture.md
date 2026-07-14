# Architecture

`src/catcam/main.py` (`python -m catcam.main`, wired to `catcam.service` in
task 10) is the single process that wires every component built in tasks
1-8 into one running service.

## Data flow

```
                          ┌─────────────────────────┐
                          │   MediaMTX (task 3)     │
                          │  RTSP/WebRTC/HLS server  │
                          └────────────┬─────────────┘
                                       │ rtsp://127.0.0.1:<rtsp_port>/<path>
                                       ▼
                  ┌───────────────────────────────────────┐
                  │   frame_source.live_frame_source()     │
                  │   (falls back to a direct camera open  │
                  │    only while streaming is stopped)    │
                  └───────────────────┬─────────────────────┘
                                      │ frame (np.ndarray)
                                      ▼
   ┌──────────────────────────────────────────────────────────────┐
   │        motion pipeline - dedicated background thread          │
   │                                                                │
   │   frame ──► MotionDetector.process_frame() (task 4)           │
   │                    │ MotionEvent (sustained motion)            │
   │                    ▼                                          │
   │   notifications_enabled()? ──No──► log "skipped", drop event  │
   │            │ Yes                                              │
   │            ▼                                                  │
   │   Recorder.record_event() (task 5) - pre-roll + live frames   │
   │            │ clip.mp4                                         │
   │            ▼                                                  │
   │   is_in_cooldown()? ──Yes──► delete clip.mp4, log, done        │
   │            │ No                                                │
   │            ▼                                                  │
   │   StorageManager.save_temp() + enforce_quota() (task 5)        │
   │            │                                                  │
   │            ▼                                                  │
   │   deliver(clip) ── bridges via asyncio.run_coroutine_threadsafe │
   └────────────────────────────┼───────────────────────────────────┘
                                │
                                ▼
   ┌──────────────────────────────────────────────────────────────┐
   │      send_video_note_with_retry() - runs on the main asyncio   │
   │      event loop, shared by both callers below                 │
   │                                                                │
   │   convert_to_video_note() (task 6) ──success──► bot.send_video_note
   │            │ failure (still send *something*)                 │
   │            └──────────────────────────────────► bot.send_video │
   └───────────┬─────────────────────────────────────┬─────────────┘
               │ delivered=True                       │ delivered=False
               ▼                                       ▼
   mark_delivered() + CooldownManager        mark_failed() -> storage/pending/
   .record_delivery_success() (task 7)                    │
                                                            │ every 60s
                                              ┌─────────────▼──────────────┐
                                              │  retry-queue asyncio.Task   │
                                              │  drain_retry_queue_once()   │
                                              │  (independent of new events,│
                                              │  survives network outages)  │
                                              └─────────────────────────────┘

   Telegram bot (task 8) polls updates on the same event loop, handling
   /status, /cooldown, /snapshot, /record, etc. concurrently with the above.
```

## Why a background thread for the motion pipeline

`python-telegram-bot` v20 is `asyncio`-native: its update polling only makes
progress while the event loop keeps turning over. But frame reads (OpenCV),
motion analysis, and `Recorder`'s FFmpeg subprocess calls (`recorder.py`,
task 5) are all synchronous, blocking calls with no natural `await` point.
Running them directly on the main event loop would stall Telegram polling
and the retry-queue drain for as long as a recording takes.

So the motion pipeline runs in its own dedicated background thread
(`main._motion_pipeline_thread`), and the *only* place it needs to talk to
the async world - sending a finished clip to Telegram - crosses back into
the event loop via `asyncio.run_coroutine_threadsafe()` (`main._deliver_sync`),
blocking the pipeline thread (not the event loop) until the send completes
or fails. The retry-queue drain, by contrast, has no blocking I/O of its own
(it just awaits `send_video_note_with_retry()`), so it runs as a plain
`asyncio.Task` alongside the bot's polling loop.

## Cooldown-during-recording semantics

Per the spec, motion detected while a cooldown is active "may be logged but
must not be sent" - the event still gets a clip recorded (so the owner
could, in principle, notice a gap in `/status`'s "last motion" time even
without a delivered clip), but that clip is deleted immediately rather than
queued to `storage/pending/`, since discarding it was always the outcome -
queuing it would only cost disk quota for no future benefit. This is
different from a *failed delivery* (network down, Telegram API error),
which does get queued to `storage/pending/` because that clip was supposed
to be sent and should still go out once delivery is possible again.

The cooldown timer itself only ever resets on `record_delivery_success()`
(task 7), which is only ever called after a *confirmed* Telegram send -
never on a send attempt, and never for a cooldown-discarded clip.

## Retry queue

`storage/pending/` (task 5's `StorageManager`) *is* the retry queue - there
is no separate persistent queue data structure. Every 60 seconds,
`drain_retry_queue_once()` lists pending files (oldest first) and attempts
delivery of each through the exact same `send_video_note_with_retry()` path
the live pipeline uses. A successful retry calls `mark_delivered()` +
`record_delivery_success()`, exactly as a live delivery would; a failure
just leaves the file in place for the next interval. This means a multi-hour
internet or Telegram-API outage doesn't lose events - they're delivered late
once connectivity returns - at the cost of not re-checking cooldown state
before a retry (a retry's "should this be sent" decision was already made
when the clip was first recorded; see `tasks/task9.md`'s Assumptions).

## Logging

Every module logs through a child of the `"catcam"` logger (e.g.
`catcam.motion`, `catcam.recorder`, `catcam.telegram_bot`). `main.py` calls
`logging_config.setup_logging(config.logging)` once at startup, which:

- Attaches a JSON-lines formatter (`_JsonFormatter`) to both a rotating file
  handler (`logging.handlers.RotatingFileHandler`, sized/rotated per
  `LoggingConfig.max_bytes`/`backup_count`) and a console handler.
- Attaches a redaction filter (`_RedactionFilter`) to *both* handlers -
  deliberately per-handler rather than per-logger, since a `Logger`'s own
  filters only run for records originating on that exact logger object, not
  on the many descendant loggers that propagate records up to these shared
  handlers.
- Sets `propagate = False` on the `"catcam"` logger so records aren't also
  duplicated onto Python's real root logger.

The redaction filter is defense-in-depth: nothing in CatCam's own code ever
logs `TELEGRAM_BOT_TOKEN` directly, but the filter scrubs any token-shaped
substring from every record regardless of source, in case an exception
message or a third-party library string ever contains one.

## Resilience

Every subsystem the pipeline touches is wrapped so its failure is logged
and backed off from, not allowed to crash the whole process:

- `_drain_frames_until_stopped()` catches `FrameSourceError` (camera
  disconnected / MediaMTX unreachable) and any other unexpected exception,
  logs it, sleeps for a backoff interval, and reconnects - the motion
  pipeline thread never dies from a transient hardware/network hiccup.
- `handle_motion_event()`/`process_frame_for_motion()` catch
  `FfmpegNotFoundError`/`RecordingFailedError` around the recording step and
  log-and-return rather than propagating - a single bad recording doesn't
  take down the pipeline thread.
- `send_video_note_with_retry()` catches every exception from the Telegram
  send call and from video-note conversion, always returning a plain
  `bool` rather than raising - both the live pipeline and the retry queue
  treat "delivered" uniformly.
- The retry-queue `asyncio.Task` wraps each drain iteration in its own
  try/except so one bad iteration doesn't cancel the periodic loop.

## Startup / shutdown

`main.main()`: load config -> `setup_logging()` -> build `CooldownManager`/
`StorageManager` -> `telegram_bot.build_application()` (wiring a
`last_motion_at_provider` closure so `/status` can report real motion
activity via `health.py`) -> `asyncio.run(_run(...))`.

`_run()` starts the motion-pipeline thread, schedules the retry-queue task,
manually drives the `Application` lifecycle (`initialize`/`start`/
`updater.start_polling()` - the lower-level, documented alternative to
`Application.run_polling()` for when other asyncio code needs to run
alongside it), and waits on a `SIGTERM`/`SIGINT`-triggered `asyncio.Event`
before tearing everything back down in reverse order.
