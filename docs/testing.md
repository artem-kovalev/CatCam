# Testing

## Running the suite locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest   # or: pip install -e ".[dev]"

pytest tests/
```

`pyproject.toml`'s `[tool.pytest.ini_options]` sets `pythonpath = ["src"]` and
`testpaths = ["tests"]`, so `pytest` (bare, from the repo root) is sufficient
— no `PYTHONPATH` export needed for local test runs.

For a single module: `pytest tests/test_recorder.py -v`.

## What's unit-tested vs. what needs real hardware

The suite (`tests/test_*.py`, one file per `src/catcam/*.py` module) is
entirely hardware-independent — no test opens a real camera, spawns a real
`ffmpeg` process against real video, or talks to the real Telegram API.
Every external boundary is mocked or substituted:

| Boundary | How it's substituted in tests |
|---|---|
| Camera device (`camera.py`) | `cv2.VideoCapture` mocked; `CameraLock` tested against real (but temporary) lock files in `tmp_path`, not a real device. |
| FFmpeg subprocess (`recorder.py`, `video_note.py`, `stream_publisher.py`) | `subprocess.Popen`/`subprocess.run` mocked; command-construction functions (`_build_ffmpeg_command`, etc.) are pure functions tested directly for correct argv, independent of any real process. |
| MediaMTX / RTSP feed (`frame_source.py`, `stream_health.py`) | `cv2.VideoCapture`/HTTP calls to the MediaMTX control API mocked. |
| Telegram Bot API (`telegram_bot.py`, `main.py`) | `python-telegram-bot`'s `Application`/`Bot` objects replaced with fakes/mocks (see `tests/test_main.py`'s `_FakeBot`, `_FakeAppConfig`) — no real bot token or network call. |
| Filesystem (`storage.py`, `cooldown.py`, config loading) | Real filesystem operations, but always against pytest's `tmp_path` fixture — never the repo's own `storage/`/`config/` directories. |

This means the suite verifies **logic correctness** (state machines, error
handling, command construction, authorization checks, redaction, config
validation) fast and deterministically, but does **not** verify:

- That a real CSI/USB camera is actually detected and produces valid frames
  on your specific hardware.
- That a real `ffmpeg` binary on Raspberry Pi OS actually encodes a valid,
  playable clip within the configured size/duration caps.
- That MediaMTX actually serves WebRTC/HLS/RTSP correctly over a real
  Tailscale/LAN connection.
- That a real Telegram bot token actually authenticates and that
  `sendVideoNote` actually renders as a circular video message on a real
  device.
- That `systemd` actually restarts/auto-starts the services correctly across
  a real reboot, or that the sudoers rule actually grants exactly the
  intended privilege on a real Raspberry Pi OS install.

Those are covered by `scripts/check_camera.sh`/`scripts/diagnose.sh` (for
the infrastructure-level checks) and the manual end-to-end procedure below
(for the full pipeline), and are explicitly deferred to `tasks/task12.md`'s
on-device sign-off — every task's own Result section notes which of its
acceptance criteria could only be verified this way, rather than silently
skipping them.

## Manual end-to-end test procedure

Run this after a full `docs/deployment.md` install, with a camera connected
and `.env`/`config.yaml`/`mediamtx.env` all filled in for real:

1. **Confirm the baseline is healthy**:
   ```bash
   scripts/diagnose.sh
   ```
   Every section should `PASS` (or `SKIP` only for `catcam-publisher.service`
   if you're on a CSI camera).

2. **Trigger motion in front of the camera.** Within roughly
   `motion.min_duration_seconds` (default 1.5s) of sustained motion, expect:
   - A Telegram message from the bot containing a circular video note.
   - The clip's visible content actually shows the motion that triggered it
     (confirms pre-roll is working, not just the post-trigger tail).
   - `/status` reports the storage usage increased.

3. **Trigger a second motion event immediately after.** Expect **no** second
   delivery — the cooldown (`/cooldown` with no args to check the current
   interval) should be active. Confirm via `/status` or by noting no new
   message arrives.

4. **Set a short cooldown and confirm it persists across a restart:**
   ```
   /cooldown 5
   ```
   ```bash
   sudo systemctl restart catcam.service
   ```
   ```
   /cooldown
   ```
   Expect the reply to still show `5 minutes` — confirms
   `storage/state/cooldown.json` persistence (`cooldown.py`) survived the
   restart, not just an in-memory value.

5. **Trigger motion again after the 5-minute cooldown has elapsed.** Expect
   a new delivery — confirms the cooldown actually expires and re-arms, not
   just that it was set.

6. **Exercise the bot commands directly**: `/snapshot` (photo arrives),
   `/record 5` (a 5-second clip arrives as a video note), `/stream` (returns
   connect instructions), `/notifications_off` then trigger motion (no
   delivery), `/notifications_on` (deliveries resume).

7. **Confirm authorization** (see `docs/security.md`): from a second
   Telegram account, send any command — expect a generic `"Not authorized."`
   reply and no state change (e.g. that account's `/cooldown 999` must not
   actually change the real cooldown interval; verify with `/cooldown` from
   the real owner account afterward).

8. **Confirm `/restart_service`** actually restarts the process (check
   `systemctl status catcam.service`'s "Active since" timestamp before/after).

9. **Reboot the whole Pi** (`sudo systemctl reboot`) and confirm both
   `catcam.service` and `catcam-stream.service` come back up unattended
   (`systemctl is-active`/`is-enabled` both units) with no manual
   intervention — see `docs/deployment.md`'s "Verifying autostart survives a
   reboot".

If every step above passes, the full pipeline — camera → motion detection →
recording → video note conversion → Telegram delivery → cooldown →
persistence → bot commands → authorization → service management — is
confirmed working end-to-end on real hardware.
