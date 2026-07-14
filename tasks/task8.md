# Task 8 — Telegram bot & authorization

## Status: Done

## Goal

Implement the private Telegram bot: strict owner-only authorization and every required command, each working with or without inline buttons.

## Depends on

Task 1 (config/secrets), Task 5 (recorder for `/record`), Task 2 (camera for `/snapshot`), Task 7 (cooldown for `/cooldown`, `/notifications_on|off`), Task 3 (stream info for `/stream`).

## Spec references

- "Private Telegram Bot" (full section, including minimum commands list).

## Assumptions

- Library: `python-telegram-bot` (current v20+ async API) — actively maintained, well-documented, current best-practice choice for a bot with commands + inline buttons; version/API confirmed against official docs before implementation.
- Authorization: every incoming update is checked against `TELEGRAM_USER_ID` (and the chat is checked against `TELEGRAM_CHAT_ID` where applicable) before any handler logic runs; unauthorized users receive a generic denial (e.g. "Not authorized.") with no hint about bot internals, and the attempt is logged (user id, no token) for audit.
- `/restart_service` restarts the `catcam.service` systemd unit via a tightly-scoped `subprocess.run(["/usr/bin/sudo", "/usr/bin/systemctl", "restart", "catcam.service"], shell=False, check=True)` (no shell string interpolation), with a matching one-line `sudoers.d` rule (documented in task 10) granting the service's running user passwordless rights to exactly that one command — never a generic shell/sudo grant.
- `/record <seconds>` clamps the requested duration to a configured max (e.g. 30s) to avoid abuse/storage exhaustion, even though only the owner can call it.
- ~~`/status` integrates with `health.py` from task 9.~~ **Superseded**: task 9 (orchestration/health) is not yet implemented, and task 8's own "Depends on" list does not include task 9 - only tasks 1/2/3/5/7. `/status` is built entirely from pieces that already exist (`camera.py`'s non-locking `is_available()`, `stream_health.check_mediamtx_path()`, `CooldownManager`, and a directory-size scan of `storage_dir`/`pending_dir`), plus an optional injectable `detector_status_provider: Callable[[], str] | None` seam (same pattern as `motion.py`'s `classifier` and `recorder.py`'s `frame_source`) that task 9's orchestrator can wire in later to report live motion-detector state. Until then, `/status` reports the detector line as "not tracked here."
- ~~`/snapshot` grabs one frame via `camera.py`.~~ **Superseded**: per task 3's already-Done camera-ownership contract (`docs/streaming.md`), calling `create_camera()` directly while `catcam-stream.service` is active either raises `CameraBusyError` (USB) or races the driver (CSI) - the same conflict task 5 resolved for the recorder. `/snapshot` and `/record` instead read from the MediaMTX RTSP feed via a new `CATCAM_STREAM_VIEW_PASSWORD` secret (documented in `docs/configuration.md`/`docs/telegram-setup.md`), falling back to a direct `create_camera_frame_source()` open only when that feed isn't reachable (streaming stopped / local dev) - mirroring task 5's own frame-source resolution.

## Steps

1. Implement `src/catcam/telegram_bot.py`:
   - Authorization decorator/middleware applied to every command handler, checking `update.effective_user.id == TELEGRAM_USER_ID` and, where relevant, `update.effective_chat.id == TELEGRAM_CHAT_ID`.
   - Handlers: `/start`, `/status` (queries camera/stream/detector/cooldown/disk status — integrates with `health.py` from task 9), `/cooldown` (no args → show current; with `<minutes>` → validate via `CooldownManager.set_interval_minutes`, reply with confirmation or validation error), `/notifications_on`, `/notifications_off`, `/snapshot` (grabs one frame via `camera.py`, sends as photo), `/record <seconds>` (invokes `Recorder`, clamps duration, sends resulting clip — as a regular video here since it's manual, not the automatic circular video-note flow, unless the spec's video-note requirement is meant to apply here too; default to also converting to video note for consistency, since spec says clip conversion applies to "the clip" generally), `/stream` (returns the Tailscale-based access instructions/link from task 3, never a public URL), `/help` (lists all commands), `/restart_service` (allowlisted subprocess call as above).
   - Optional inline-button keyboard mirroring the same commands (e.g. buttons for `/status`, `/snapshot`, notifications on/off), but every command must remain fully usable by typing it — buttons call the same handler functions, not separate logic.
   - Ensure no handler ever logs `TELEGRAM_BOT_TOKEN` or full update objects containing it; use the redaction helper from task 1.
2. Add `tests/test_authorization.py`: requests from `TELEGRAM_USER_ID` are allowed through to handler logic; requests from any other user id are rejected before handler logic executes (assert the underlying action — e.g. recorder/cooldown mutation — did not occur) and produce a generic, non-revealing reply; verify the same for a mismatched chat id if that check is enabled.
3. Write `docs/telegram-setup.md`: creating a bot via BotFather, obtaining `TELEGRAM_BOT_TOKEN`, finding your `TELEGRAM_USER_ID`/`TELEGRAM_CHAT_ID` (e.g. via `@userinfobot` or the bot's own `/start` logging its caller's id once during setup), populating `.env`, restarting the service to pick up new secrets.

## Acceptance criteria

- [x] Every command listed in the spec is implemented and functional via typed text command, independent of any inline buttons.
- [x] A message from a non-owner user id (or non-owner chat, if configured) is rejected before any state-changing logic runs, with a generic denial message.
- [x] `TELEGRAM_BOT_TOKEN` never appears in logs.
- [x] `/restart_service` cannot execute arbitrary shell input — it is a fixed, allowlisted `systemctl` invocation with no user-supplied string reaching a shell.
- [x] `/cooldown <minutes>` rejects out-of-range values with a clear message and does not change persisted state.
- [x] `tests/test_authorization.py` passes.
- [x] `docs/telegram-setup.md` walks through bot creation and ID discovery with no unexplained steps.

## Result

Implemented `src/catcam/telegram_bot.py`:

- `_authorized_only` is a decorator applied to every command handler and to
  the inline-button dispatcher (`on_button`) alike — it checks
  `update.effective_user.id == config.telegram.user_id` and (if the chat is
  known) `update.effective_chat.id == config.telegram.chat_id` *before*
  calling the wrapped handler, replying with a fixed, non-revealing
  `"Not authorized."` and logging only the numeric ids (never a token) on
  rejection.
- Handlers implemented as plain `_*_impl` functions (undecorated), each
  wrapped once via `_authorized_only` for `CommandHandler` registration:
  `/start`, `/help`, `/status`, `/cooldown [minutes]`, `/notifications_on`,
  `/notifications_off`, `/snapshot`, `/record <seconds>`, `/stream`,
  `/restart_service`. Inline buttons (`on_button`, itself
  `_authorized_only`-wrapped) dispatch to the *same* `_*_impl` functions via
  a small `_BUTTON_HANDLERS` lookup table for the read-only,
  non-destructive commands only (`status`, `snapshot`, `notifications_on`,
  `notifications_off`, `stream`) — `/record`, `/cooldown`, and
  `/restart_service` are deliberately typed-command-only so an accidental
  tap can't trigger a recording, a config change, or a service restart.
- `/status` reports camera availability (`create_camera(...).is_available()`
  — a non-locking check, safe to call regardless of streaming state),
  MediaMTX stream readiness (`stream_health.check_mediamtx_path()`),
  cooldown interval/state, notifications enabled/disabled, and combined
  `storage_dir`+`pending_dir` usage vs. `disk_quota_mb`. The motion-detector
  line uses an optional injectable `detector_status_provider` (defaults to a
  placeholder string) — see the superseded-assumption note above.
- `/snapshot` and `/record <seconds>` both go through `_live_frame_source()`,
  which prefers reading from the MediaMTX RTSP feed (via a new
  `CATCAM_STREAM_VIEW_PASSWORD` secret) and falls back to
  `create_camera_frame_source()` (task 5) only when that feed is
  unreachable. `/record` clamps to `_MAX_MANUAL_RECORD_SECONDS = 30` (a
  fixed constant, not config-exposed — same precedent as `motion.py`'s noise
  floor and `video_note.py`'s fixed audio stripping), builds a one-off
  `RecordingConfig` via `dataclasses.replace()` with the clamped duration
  (no changes needed to task 5's already-Done `Recorder` class), records via
  `Recorder.record_event()`, converts to a video note via task 6's
  `convert_to_video_note()`, and falls back to sending the raw clip if
  conversion fails. Cooldown is intentionally **not** checked for this
  manual command — the cooldown gates *automatic* motion-triggered delivery
  (task 9's future orchestrator), not an explicit owner-initiated request.
- `/restart_service` runs a fixed `["/usr/bin/sudo", "/usr/bin/systemctl",
  "restart", "catcam.service"]` list via `subprocess.run(..., shell=False,
  check=True)` — no user input reaches this call at all (the command takes
  no arguments), so there is no injection surface by construction. The
  matching `sudoers.d` rule is deferred to task 10 (deployment), as the
  Assumptions text specifies.
- An `Application`-level error handler (`_on_error`) logs
  `redact(str(context.error))` using task 1's `logging_config.redact` helper
  — the one place an unexpected exception message could theoretically
  contain a token-shaped string.
- `build_application(config, cooldown_manager, storage_manager,
  detector_status_provider=None)` wires `bot_data` and registers every
  handler; `main()` (`python -m catcam.telegram_bot`) loads config, builds
  `CooldownManager`/`StorageManager`, and calls `run_polling()`.

- Created files:
  - `src/catcam/telegram_bot.py`
  - `tests/test_authorization.py`
  - `tests/test_telegram_bot.py`
  - `docs/telegram-setup.md`
- Modified files:
  - `docs/configuration.md` (added `CATCAM_STREAM_VIEW_PASSWORD` to the
    Secrets table)
  - `tasks/task8.md` (this file: Status, Assumptions, acceptance criteria,
    Result)
  - `tasks/summary.md` (status table)
- Environment note: the repo's `.venv` had been created with Homebrew's
  Python 3.14, under which `python-telegram-bot` 20.8's `Application`
  construction raises `AttributeError` (`Updater.__init__` assigning to a
  slot that doesn't exist under 3.14 — a library/interpreter incompatibility,
  not a CatCam bug). Recreated `.venv` with Python 3.12 (already available
  via `brew`) and reinstalled from `pyproject.toml`/`requirements.txt`; the
  project's own `requires-python = ">=3.11"` already excluded 3.14 as a
  supported version, so this brings the dev environment in line with what
  was already declared, not a new constraint.
- Commands executed:
  - `rm -rf .venv && python3.12 -m venv .venv && pip install -e ".[dev]" -r requirements.txt`
  - `python -m pytest tests/test_authorization.py tests/test_telegram_bot.py -v` → 21/21 passed
  - `python -m pytest tests/ -v` → 88 passed, 1 skipped (full suite, tasks
    1–8 combined; the 1 skip is task 6's pre-existing real-FFmpeg
    integration test, unrelated to this task)
- Test results: all passing. `tests/test_authorization.py` (9 tests) covers:
  an authorized command reaching and mutating state; an unauthorized command
  (wrong user id) rejected before any mutation with the generic denial
  message; a matching-user/mismatched-chat request also rejected; the same
  authorized/unauthorized split for a state-changing `/cooldown` call; the
  same split for inline-button dispatch (`on_button`); a token-shaped string
  never appearing in captured logs across both a rejected attempt and a
  failing `/restart_service` call; and `/restart_service`'s command list
  being exactly the fixed, allowlisted argv with `shell=False`/`check=True`.
  `tests/test_telegram_bot.py` (13 tests) covers: `/record`'s clamping
  helper; `/cooldown`'s out-of-range/non-integer/no-args behavior and that
  state is left untouched on rejection; `/help` listing every command;
  `/stream` never emitting `streaming.bind_address` (`0.0.0.0`) as if it
  were a real connect URL; `/snapshot`/`/record` surfacing a clear error
  when no frame source is reachable or `ffmpeg` is missing, without ever
  constructing a `Recorder` on bad input; `/status`'s camera/stream/
  cooldown/notifications reporting with mocked camera/stream-health calls;
  and `build_application()` registering exactly the ten expected commands.
- Unresolved questions:
  - **Real Telegram delivery** (actual bot-to-Telegram-server interaction,
    inline button rendering, `reply_video_note`/`reply_photo` behavior
    against the live Bot API) is unverified here — everything above is
    tested against fakes/mocks, consistent with every prior task's hardware/
    network-dependent verification being deferred to task 12's manual
    checklist.
  - **`/snapshot`/`/record`'s RTSP-vs-direct-camera fallback** has not been
    exercised against a real MediaMTX instance or a real camera in this
    environment (no camera, no MediaMTX running here) — same deferral as
    task 5's/task 3's own unresolved-hardware notes.
  - **The `CATCAM_STREAM_VIEW_PASSWORD`/`MTX_AUTHINTERNALUSERS_1_PASS`
    pairing** is documented but not wired into any deployment automation yet
    — task 10 (deployment) should ensure both secrets are provisioned
    together, since a mismatch would silently push `/snapshot`/`/record`
    onto the direct-camera-open fallback (which itself only works while
    streaming is stopped) rather than failing loudly.
  - **Third-party library logging**: `python-telegram-bot`'s own internal
    HTTP client may log request details at `DEBUG` level independent of
    CatCam's code; the "`TELEGRAM_BOT_TOKEN` never appears in logs"
    acceptance criterion is verified for CatCam's own logging (nothing in
    `telegram_bot.py` ever logs the token, and the one place an arbitrary
    exception message is logged goes through `redact()`), not for
    third-party library internals at non-default log levels.
