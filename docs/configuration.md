# Configuration

CatCam is configured from two files plus the process environment:

1. **`config/config.yaml`** — structural (non-secret) settings. If this file
   doesn't exist yet, `config/config.example.yaml` is used instead (with the
   same keys/defaults) so the app can still start during initial setup.
2. **`.env`** — secrets and environment-specific overrides, loaded via
   `python-dotenv`. Never commit this file (`.gitignore` excludes it).
3. **Real process environment variables** — win over both files. Useful for
   systemd `Environment=`/`EnvironmentFile=` overrides or ad-hoc testing.

**Precedence, low to high:** `config.yaml` defaults → `.env` → real
environment variables. In practice this means: set a value in
`config/config.yaml` for the normal case, override it per-deployment via
`.env` or the shell environment when needed.

Every YAML key can be overridden by an environment variable named
`CATCAM_<SECTION>_<FIELD>` (uppercased), e.g. `CATCAM_COOLDOWN_DEFAULT_MINUTES=30`
overrides `cooldown.default_minutes`. This only applies to scalar fields
(numbers/strings/booleans) — list-typed fields like `motion.roi` or
`camera.resolution` can only be set via YAML.

Invalid or missing **required** values (Telegram secrets, out-of-range
numbers) raise `catcam.config.ConfigError` immediately at startup — there are
no silent defaults for secrets.

## Secrets (`.env`)

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather. Never logged (see `logging_config.redact`). |
| `TELEGRAM_USER_ID` | Yes | Numeric Telegram user id of the sole authorized owner. Must parse as an integer. |
| `TELEGRAM_CHAT_ID` | Yes | Numeric Telegram chat id clips/notifications are sent to. Must parse as an integer. |
| `CATCAM_CONFIG_PATH` | No | Absolute path to `config.yaml` if it lives outside the repo (e.g. `/etc/catcam/config.yaml`). Defaults to `config/config.yaml`, falling back to `config/config.example.yaml`. |
| `CATCAM_STREAM_VIEW_PASSWORD` | No | Password for MediaMTX's `catcam-viewer` account (see `docs/streaming.md`), used by `frame_source.py`'s `live_frame_source()` - shared by `/snapshot`/`/record` (task 8) and the continuous motion pipeline (task 9, `main.py`) - to read frames from the RTSP feed. Must match `MTX_AUTHINTERNALUSERS_1_PASS` in `/etc/catcam/mediamtx.env`. If unset (or the RTSP feed is unreachable), falls back to opening the camera directly - only valid while `catcam-stream.service` is stopped. |

## Structural settings (`config/config.yaml`)

### `camera`

| Key | Default | Valid range / notes |
|---|---|---|
| `type` | `csi` | `csi` or `usb` |
| `device` | `/dev/video0` | Only used when `type: usb` |
| `resolution` | `[1280, 720]` | `[width, height]` in pixels |
| `framerate` | `15` | Frames per second |

### `motion`

| Key | Default | Valid range / notes |
|---|---|---|
| `sensitivity` | `25` | `0`–`100`, higher = more sensitive |
| `min_duration_seconds` | `1.5` | Must be `> 0` |
| `roi` | `null` (full frame) | `[x, y, width, height]` in pixels, or `null` |

**Tuning guidance** (`src/catcam/motion.py`, `MotionDetector`):

- `sensitivity` maps onto OpenCV MOG2's `varThreshold` (lower threshold =
  more sensitive): `0` → least sensitive (`varThreshold≈100`), `100` → most
  sensitive (`varThreshold≈4`). Start at the default `25` and raise it if
  small changes (leaves, IR reflections, sensor noise) aren't triggering
  events you care about; lower it if those are producing false positives.
- `roi` crops the frame *before* it reaches the background subtractor, so
  motion entirely outside it is structurally invisible to the detector, not
  just filtered after the fact. Coordinates are in pixels of the configured
  `camera.resolution`, so re-check `roi` after changing `resolution`.
- `min_duration_seconds` is converted internally to a frame count using the
  camera's actual frame rate (`camera.framerate`, passed to `MotionDetector`
  as its `fps` argument by the caller) — motion must be detected in that many
  *consecutive* analyzed frames before a `MotionEvent` fires. Raise it to
  filter out brief, one-off noise; lower it for faster-triggering detection
  at the cost of more false positives.
- Blobs smaller than 0.5% of the analyzed (ROI or full-frame) area are always
  discarded as noise — this isn't config-exposed since it's a fixed noise
  floor, not a sensitivity knob.
- **Optional cat/not-cat classifier**: `MotionDetector`'s constructor accepts
  a `classifier: Callable[[np.ndarray], bool] | None` argument. When set, it
  is invoked once per motion episode (not per frame) with the cropped
  bounding-box region, only after the sensitivity/ROI/min-duration gate has
  already been satisfied; returning `False` suppresses that episode's
  `MotionEvent`. There is no YAML/`.env` key for this yet — wiring an actual
  model (e.g. a MobileNet-based TFLite classifier) in is left to whichever
  later task adds it; `motion.py` runs correctly with `classifier=None`
  (the default).

### `recording`

| Key | Default | Valid range / notes |
|---|---|---|
| `clip_duration_seconds` | `10` | Must be `> 0` |
| `pre_roll_seconds` | `2` | Seconds of buffer captured before motion was confirmed |
| `max_clip_size_mb` | `50` | Hard cap per clip |
| `storage_dir` | `storage/recordings` | Recorded clips awaiting/after delivery |
| `pending_dir` | `storage/pending` | Clips awaiting retry delivery |
| `disk_quota_mb` | `2048` | Must be `> 0`; enforced by `storage.py` (task 5) |

**Tuning guidance** (`src/catcam/storage.py`, `src/catcam/recorder.py`):

- In-progress recordings are written to a `tmp` directory derived at runtime
  as the sibling of `storage_dir` (e.g. `storage/recordings` ->
  `storage/tmp`) — there is no separate `tmp_dir` config key; move
  `storage_dir` to relocate it.
- `max_clip_size_mb` is enforced via FFmpeg's `-fs` flag (a hard output-size
  cap): FFmpeg stops muxing once the limit is reached rather than producing
  an oversized file. `Recorder` logs a warning when this truncates a clip
  short of its configured `clip_duration_seconds`; adaptive bitrate/
  resolution re-encoding on overflow is not implemented — see
  `tasks/task5.md`'s Result for the tradeoff.
- `disk_quota_mb` bounds the combined size of `storage_dir` + `pending_dir`
  (summed via directory walk), not whole-filesystem free space — a full disk
  elsewhere on the machine isn't what this quota tracks. `enforce_quota()`
  deletes the oldest files in `pending_dir` first until back under quota,
  logging each deletion.

### `video_note`

| Key | Default | Valid range / notes |
|---|---|---|
| `size_px` | `384` | Output square side length in pixels; must be `> 0` |
| `max_duration_seconds` | `60` | Telegram `sendVideoNote` hard limit ("up to 1 minute long", verified live against `core.telegram.org/bots/api#sendvideonote` on 2026-07-14) |
| `max_size_mb` | `8` | A conservative app-chosen default for reliable delivery over slow connections — not a `sendVideoNote`-specific limit. Telegram's Bot API docs state no video-note-specific size cap; the general multipart/form-data upload ceiling for non-photo files is 50 MB (verified same source/date). |
| `codec` | `libx264` | FFmpeg video codec |
| `crf` | `28` | FFmpeg constant rate factor (quality/size tradeoff) |

**Tuning guidance** (`src/catcam/video_note.py`, `convert_to_video_note()`):

- Conversion is a single FFmpeg invocation: center-crop the shorter dimension
  to a square, scale to `size_px`, truncate to `max_duration_seconds` (`-t`),
  strip audio (`-an`, fixed — not config-exposed, since a cat-monitoring clip
  has no useful audio track and dropping it keeps the filtergraph simple).
- `max_size_mb` is enforced two ways: an FFmpeg `-fs` hard byte cap on every
  attempt (same mechanism as `recorder.py`'s clip-size enforcement, so the
  output can never silently exceed the cap even in the worst case), plus a
  size check after encoding — if the first pass is still over `max_size_mb`,
  one retry runs at half the resolution (floor `128px`) and a higher
  (more-compressed) CRF (`+10`, capped at FFmpeg's max `51`). If the retry is
  still oversized, the clip is returned anyway with a logged `ERROR` rather
  than raised — the caller (task 9) decides whether to send a still-oversized
  clip or drop it, since a slightly-oversized clip is more useful than none.

### `cooldown`

| Key | Default | Valid range / notes |
|---|---|---|
| `default_minutes` | `60` | Must be within `[min_minutes, max_minutes]` |
| `min_minutes` | `1` | Must be within `1`–`1440` |
| `max_minutes` | `1440` | Must be within `1`–`1440`, and `>= min_minutes` |
| `state_file` | `storage/state/cooldown.json` | Persisted across restarts |

**Tuning guidance** (`src/catcam/cooldown.py`, `CooldownManager`):

- The cooldown timer resets only on a *confirmed successful* delivery
  (`record_delivery_success()`), never on a mere send attempt — a failed
  send that's retried later by task 9's retry queue does not start the
  clock early.
- State (`last_delivery_at`, `interval_minutes`, `notifications_enabled`) is
  persisted to `state_file` as JSON via an atomic temp-file-then-`os.replace`
  write, so a Raspberry Pi power loss mid-write can't corrupt the file.
- `notifications_enabled` is a separate, independent gate from the cooldown
  timer, not folded into `is_in_cooldown()` — a caller (task 9) must check
  both and OR them together, since "disabled" should suppress delivery
  regardless of whether the cooldown interval has actually elapsed.
- `set_interval_minutes()` validates against `[min_minutes, max_minutes]`
  and rejects non-integers (including `bool`, which is technically an `int`
  subclass in Python); a rejected value leaves previously-persisted state
  untouched.

### `streaming`

| Key | Default | Valid range / notes |
|---|---|---|
| `bind_address` | `0.0.0.0` | Informational only (builds connect-URLs for docs/health output) — MediaMTX's actual listener addresses/auth are configured in `deploy/mediamtx/mediamtx.yml`, not here. See `docs/streaming.md` for why each protocol is bound wide and gated by MediaMTX's own auth instead of a narrow interface bind. |
| `webrtc_port` | `8889` | MediaMTX WebRTC port |
| `hls_port` | `8888` | MediaMTX HLS port |
| `rtsp_port` | `8554` | MediaMTX RTSP port |
| `path` | `cam` | MediaMTX path name serving the camera feed (`rtsp://<host>:<rtsp_port>/<path>`, etc.) |

### `logging`

| Key | Default | Valid range / notes |
|---|---|---|
| `level` | `INFO` | Standard Python logging level name |
| `file` | `storage/logs/catcam.log` | Log file path |
| `max_bytes` | `10485760` (10 MB) | Rotation size threshold |
| `backup_count` | `5` | Number of rotated backups kept |

**Tuning guidance** (`src/catcam/logging_config.py`, `setup_logging()`):

- Every module logs through a child of the `"catcam"` logger; `main.py`
  calls `setup_logging()` once at startup, which configures one rotating
  file handler (`logging.handlers.RotatingFileHandler`) and one console
  handler, both writing JSON-lines records (`{"timestamp", "level",
  "logger", "message", ...}`) so log lines are machine-parseable.
- A redaction filter (`logging_config._RedactionFilter`) is attached to
  both handlers - not to the logger - and scrubs any Telegram-bot-token-
  shaped substring from every record before it's written, regardless of
  which module produced it. This is defense-in-depth: CatCam's own code
  never logs `TELEGRAM_BOT_TOKEN` directly (see `docs/telegram-setup.md`).
- Raising `level` above `INFO` (e.g. `WARNING`) silences routine per-motion-
  event logging but keeps errors/warnings (recording failures, delivery
  failures, camera disconnects) visible - useful once the system has been
  running stably and per-event logs are just noise.

## Loading configuration in code

```python
from catcam.config import load_config, ConfigError

try:
    config = load_config()
except ConfigError as exc:
    raise SystemExit(f"Invalid configuration: {exc}")
```

`load_config(env_path=None, yaml_path=None)` accepts explicit paths for
testing; both default to the standard discovery rules described above.
