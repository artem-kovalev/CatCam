# Task 6 — Telegram video note conversion

## Status: Done

## Goal

Convert a recorded clip into a Telegram-compatible "video note" (circular video message): square aspect ratio, compatible codec/container, within Telegram's size/duration limits.

## Depends on

Task 5 (produces the source clip).

## Spec references

- "Telegram Video Note" (full section).

## Assumptions

- Must verify current Telegram Bot API limits for `sendVideoNote` (historically: video notes must be square, ≤ 1 minute duration, and there are practical file-size considerations for reliable delivery) against the official Telegram Bot API documentation before finalizing FFmpeg parameters — limits are checked live during implementation, not assumed from memory. **Verified 2026-07-14** by fetching `https://core.telegram.org/bots/api` directly: "As of v.4.0, Telegram clients support rounded square MPEG4 videos of up to 1 minute long" (the `sendVideoNote` method's own description — confirms the 60s cap and MPEG4/square requirement exactly as assumed). No `sendVideoNote`-specific file-size limit is stated anywhere on the page; the general multipart/form-data upload ceiling for non-photo file types is 50 MB. This means `VideoNoteConfig.max_size_mb` (default `8`, already shipped by task 1) is an app-chosen conservative default for reliable delivery, not a value forced by a Telegram-imposed video-note size cap — corrected in `docs/configuration.md`'s `video_note` table, which previously mis-described it as "Telegram `sendVideoNote` limit."
- Container/codec: MP4 with H.264 video (`libx264`) + AAC audio (or no audio track, since a cat-monitoring clip likely doesn't need audio — configurable), which is the widely-documented compatible combination for `sendVideoNote`.
- Square crop: center-crop the shorter dimension (typically height) to match width, then scale to a standard video-note resolution (e.g. 384×384) via a single FFmpeg filtergraph (`crop=...,scale=...`), keeping processing to one FFmpeg invocation for efficiency on Pi 4.

## Steps

1. Check official Telegram Bot API docs (`sendVideoNote` method) for current constraints (duration cap, whether streaming/`supports_streaming` matters, file size guidance) before writing the conversion parameters and `docs` text.
2. Implement `src/catcam/video_note.py`:
   - `convert_to_video_note(input_path: Path, output_path: Path, config: VideoNoteConfig) -> Path` building and running an FFmpeg command with automatic crop+scale (`ffmpeg -i in -vf "crop=min(iw\\,ih):min(iw\\,ih),scale=384:384" -c:v libx264 -preset veryfast -an out.mp4`, exact filter/codec/args parameterized by config), plus duration truncation if the source exceeds the Telegram limit.
   - Enforce output file size cap (config); if exceeded after encoding, retry once at reduced resolution/bitrate.
   - Typed exceptions (`VideoNoteConversionError`) wrapping FFmpeg subprocess failures with captured stderr for diagnostics (without leaking secrets — there are none in this path, but keep the pattern consistent).
3. Add `tests/test_video_note.py`: generate a tiny synthetic non-square test video with FFmpeg in the test setup (or use a small checked-in fixture if licensing/size allows a few-KB sample), run conversion, assert output is square (via `ffprobe`/OpenCV frame read), within duration/size limits, and a valid MP4/H.264 stream. Skip gracefully (not fail) if FFmpeg isn't installed in the test environment, with a clear skip reason.
4. Document conversion parameters and Telegram limits (with source/date checked) in `docs/configuration.md` or a dedicated note in `docs/architecture.md`.

## Acceptance criteria

- [x] Output video is square, encoded as MP4/H.264 (or another container/codec pair confirmed compatible with `sendVideoNote` per current docs).
- [x] Output respects Telegram's duration and size limits as currently documented; oversized/overlong sources are truncated/re-encoded, not rejected outright.
- [x] `video_note.py` raises a specific exception on FFmpeg failure rather than crashing the caller.
- [x] `tests/test_video_note.py` passes (or skips cleanly without FFmpeg) and validates squareness/size/duration.
- [ ] A real converted clip sent via `sendVideoNote` (manual verification in task 12) renders as a circular video message in Telegram, not a regular video. — deferred; requires a live bot/chat and is explicitly task 12's job per this task's own acceptance criterion.

## Result

Implemented `src/catcam/video_note.py`:

- `convert_to_video_note(input_path, output_path, config: VideoNoteConfig) -> Path` —
  builds and runs a single FFmpeg invocation: `crop=min(iw\,ih):min(iw\,ih),scale={size_px}:{size_px}`
  for the square center-crop+scale, `-t {max_duration_seconds}` for duration
  truncation, `-an` to strip audio (fixed, not config-exposed — see the
  module docstring), `-c:v {codec} -preset veryfast -crf {crf}` for
  encoding, and `-fs {max_size_mb in bytes}` as a hard output-size backstop
  (same mechanism as `recorder.py`'s clip-size enforcement).
- After encoding, if the output still exceeds `max_size_mb`, one retry runs
  automatically at half the resolution (floored at 128px) and a higher CRF
  (`+10`, capped at FFmpeg's max of `51`) — satisfying "retry once at
  reduced resolution/bitrate" literally (both are reduced together). If
  still oversized after the retry, the clip is returned anyway with a
  logged `ERROR` (not raised) — a caller (task 9) can still choose to send
  a slightly-oversized clip rather than lose the event entirely; this
  tradeoff is documented in `docs/configuration.md`.
- `VideoNoteConversionError` is the single exception type, raised both when
  `ffmpeg` isn't on PATH and when the subprocess exits non-zero (stderr
  tail included) — kept as one exception type per the Steps text's literal
  wording ("raises a specific exception on FFmpeg failure"), rather than
  splitting into a separate not-found error as `recorder.py` did, since
  this module doesn't need callers to distinguish the two failure modes
  differently.

`VideoNoteConfig`/`config.py` needed no schema changes — `size_px`,
`max_duration_seconds`, `max_size_mb`, `codec`, `crf` already existed from
task 1.

**Telegram limits verified live** (not assumed from memory) by fetching
`https://core.telegram.org/bots/api` directly on 2026-07-14 — see the
updated Assumptions bullet above for the exact finding (60s duration cap and
square/MPEG4 requirement confirmed; no video-note-specific size limit
exists, general multipart upload cap is 50 MB). This corrected a
pre-existing, mildly inaccurate `docs/configuration.md` table row
(`max_size_mb`'s note previously said "Telegram `sendVideoNote` limit,"
implying Telegram itself caps it at 8 MB) — fixed in this task's docs edit.

- Created files:
  - `src/catcam/video_note.py`
  - `tests/test_video_note.py`
- Modified files:
  - `docs/configuration.md` (corrected the `max_size_mb` row's note; added a
    "Tuning guidance" block under the `video_note` table — crop/scale/
    duration/audio behavior, the `-fs`+retry size-cap strategy)
  - `tasks/task6.md` (this file: Status, Assumptions verification note,
    acceptance criteria, Result)
  - `tasks/summary.md` (status table)
- Commands executed:
  - Live fetch of `https://core.telegram.org/bots/api` (via `curl`, grepped
    for the `sendVideoNote` section and file-size-limit mentions) — see
    verification note above.
  - `which ffmpeg` → not found on this dev machine (same as task 5) —
    real-encode integration test skips cleanly rather than failing.
  - `python -m pytest tests/test_video_note.py -v` → 6 passed, 1 skipped
    (the real-FFmpeg integration test, skip reason: "ffmpeg is not
    installed in this test environment")
  - `python -m pytest tests/ -v` → 52 passed, 1 skipped (full suite, tasks
    1–6 combined)
- Test results: all mocked tests pass. `tests/test_video_note.py` covers:
  FFmpeg command construction (pure function — crop/scale filter string,
  `-t`, `-an`, `-crf`, `-fs` all present and correctly valued); `ffmpeg`
  missing raises `VideoNoteConversionError` before any subprocess is
  spawned; a non-zero FFmpeg exit raises `VideoNoteConversionError` with
  the stderr tail; a within-cap first encode returns without retrying
  (`subprocess.run` called exactly once); an oversized first encode
  triggers exactly one retry at the expected halved resolution and raised
  CRF, then returns the retry's path; a still-oversized-after-retry result
  is still returned (not raised), with the retry loop bounded to exactly
  one attempt. A `pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)`
  integration test (generates a 640x360 synthetic source via FFmpeg's
  `testsrc` lavfi filter, converts it, and asserts the decoded output frame
  is square via OpenCV) is included but was skipped in this environment —
  no ffmpeg installed here, consistent with task 5's precedent.
- Unresolved questions:
  - **Real FFmpeg conversion is entirely unverified on this dev machine**
    (no `ffmpeg` binary installed) — the crop/scale filtergraph's exact
    behavior on real non-square footage, real CRF-vs-file-size tradeoffs,
    and the `-fs`+retry interaction with a real encoder are only exercised
    via mocks/pure-function assertions here. The included
    `test_real_conversion_produces_square_mp4` will exercise this
    automatically wherever `ffmpeg` is present (e.g. the Pi or a CI runner
    with ffmpeg installed) — deferred to task 12 for on-device
    confirmation, consistent with tasks 2/3/4/5's precedent.
  - **Actual Telegram delivery/rendering as a circular video message** is
    explicitly out of scope for automated testing (needs a live bot, chat,
    and manual visual confirmation) — deferred to task 12's acceptance
    checklist per this task's own last acceptance criterion.
  - **No-audio decision is fixed, not configurable**: if a future need
    arises for audio in video notes, `VideoNoteConfig` would need a new
    field and `_build_ffmpeg_command` would need an `-an`-omitting branch;
    not built now since the spec's assumptions text explicitly floats this
    as optional/unlikely to matter for a cat-monitoring use case.
