# Troubleshooting

**Always start here:**

```bash
scripts/diagnose.sh
```

It runs every check in one pass — camera, FFmpeg, `catcam-stream.service`,
`catcam-publisher.service`, MediaMTX readiness, `catcam.service`, storage/disk
quota, `.env` presence, and a tail of recent logs — and exits non-zero if
anything failed. Most symptoms below map directly onto one of its sections;
read its output before digging further by hand.

**Log location:** `storage/logs/catcam.log` (local dev) or
`/opt/catcam/storage/logs/catcam.log` (deployed), JSON-lines, one object per
record (`timestamp`, `level`, `logger`, `message`, and `exception` if
present). Also mirrored to `journalctl -u catcam.service` /
`-u catcam-stream.service` when running under systemd. Every record has
anything token-shaped redacted before it's written (see `docs/security.md`),
so it's always safe to paste log output when asking for help.

```bash
tail -n 50 storage/logs/catcam.log
journalctl -u catcam.service --since "1 hour ago"
```

## Symptom → cause → fix

| Symptom | Likely cause | Fix |
|---|---|---|
| Camera not detected | Cable/orientation wrong (CSI), or USB camera not enumerating; wrong `camera.type` in `config.yaml` | Run `scripts/check_camera.sh` directly for CSI (`rpicam-hello --list-cameras`) / USB (`v4l2-ctl --list-devices`) detail; check ribbon-cable orientation in `docs/hardware.md`; confirm `config/config.yaml`'s `camera.type`/`camera.device` match your actual hardware. |
| FFmpeg missing | Not installed, or not on `PATH` for the `catcam` user | `sudo apt install -y ffmpeg`; `scripts/diagnose.sh`'s "FFmpeg" section checks `command -v ffmpeg`; recording (`recorder.py`) and video-note conversion (`video_note.py`) both raise a clear `FfmpegNotFoundError`/`VideoNoteConversionError` at the point of use rather than failing silently. |
| Stream unreachable (WebRTC/HLS/RTSP all fail) | `catcam-stream.service` not running, or you're not on the Tailscale/LAN network the ports are bound to | `systemctl status catcam-stream.service`; `journalctl -u catcam-stream.service`; confirm `tailscale status` shows the Pi online if connecting remotely. See `docs/streaming.md`'s own troubleshooting table for protocol-specific detail (e.g. WebRTC needing UDP port `8189` reachable, not just `8889`). |
| Stream unreachable (USB camera specifically) | `catcam-publisher.service` down, or `camera.type` mismatch | `systemctl status catcam-publisher.service`; confirm `config.yaml`'s `camera.type: usb`; confirm `mediamtx.yml`'s `paths.cam` block is the `source: publisher` variant, not `rpiCamera` (see `docs/streaming.md`). |
| Bot not responding at all (no reply, not even "Not authorized") | `catcam.service` not running, or invalid `TELEGRAM_BOT_TOKEN` at startup | `systemctl status catcam.service`; `journalctl -u catcam.service` for a startup-time `ConfigError` or `InvalidToken` from `python-telegram-bot`; confirm `/etc/catcam/.env` exists and has all three `TELEGRAM_*` values (`scripts/diagnose.sh`'s ".env" section only checks existence, not validity — a present-but-wrong token still needs a log check). |
| "Not authorized" replies from your own account | `TELEGRAM_USER_ID`/`TELEGRAM_CHAT_ID` in `.env` don't match your real Telegram ids | Use `docs/telegram-setup.md` §2's rejection-log method: temporarily set both to `0`, restart, send `/start`, read the real `user_id=`/`chat_id=` back from the log, put those in `.env`, restart again. |
| Complaints from someone else that the bot "doesn't work" | Expected — they are not the configured owner | This is by design, not a bug: every command is rejected for anyone but `TELEGRAM_USER_ID`/`TELEGRAM_CHAT_ID` (see `docs/security.md`). There is no secondary "read-only" or "guest" tier. |
| Cooldown doesn't seem to reset / stays "in cooldown" indefinitely | The timer only resets on a *confirmed successful delivery*, not on a motion event or a send attempt (see `cooldown.py`'s module docstring) — a stuck retry queue means deliveries never confirm | Check `storage/pending/` for stuck clips (retried automatically every 60 seconds by `main.py`'s retry-queue loop); check the log for repeated delivery failures (network issue, invalid token, Telegram API error); `/cooldown` with no args shows the current interval and in-cooldown/ready state. |
| `/cooldown 5` doesn't stick after a restart | Edited `storage/state/cooldown.json` by hand and broke its format, or the file isn't writable by the `catcam` user | `CooldownManager` logs and falls back to defaults on a corrupt/unreadable state file (`cooldown.py`'s `_load()`) rather than crashing — check the log for a "Could not read cooldown state file" error; confirm `storage/state/` is owned by `catcam` and writable. |
| Disk full / disk quota warnings | `recording.disk_quota_mb` set too low for your motion frequency, or oldest pending clips already deleted and `storage_dir` itself is still over quota | `scripts/diagnose.sh`'s "Storage / disk quota" section (`python -m catcam.health`) reports current usage vs. quota and vs. a fixed 500 MB free-space floor; `StorageManager.enforce_quota()` deletes oldest `storage/pending/` clips first automatically, but logs an error (not a crash) if `storage_dir` alone still exceeds quota — in that case, raise `disk_quota_mb` in `config.yaml`, or manually clear old clips from `storage/recordings/`. |
| Video note doesn't render as a circular "video message" in Telegram (arrives as a regular video instead) | `video_note.py`'s conversion failed and `telegram_bot.py`/`main.py` fell back to sending the raw clip | Check the log for a `VideoNoteConversionError` (ffmpeg missing, or the conversion subprocess exited non-zero — the log includes ffmpeg's stderr tail); confirm ffmpeg is installed (see the FFmpeg row above); note Telegram itself also requires the video to be square and ≤ 1 minute to render as a video note at all — `convert_to_video_note()` already enforces both, so a successful conversion should always render circular. |
| Video note conversion "succeeds" but the file is still over `video_note.max_size_mb` | Source clip too dense to compress enough within one retry pass | Logged loudly (`logger.error("... still %.1f MB after retry ...")`) rather than raised — the caller (task 9) still sends it, since an oversized clip is more useful than none; if this happens often, lower `video_note.size_px` or raise `crf` in `config.yaml` for smaller baseline output. |
| "Restart failed" reply from `/restart_service` | Sudoers rule missing or invalid | `sudo visudo -c -f /etc/sudoers.d/catcam`; re-run `sudo scripts/install.sh`, which re-validates and reinstalls the rule (`deploy/sudoers.d/catcam`) if it's missing or broken. |
| `install.sh`/`uninstall.sh` refuses to run | Not run as root | Both scripts explicitly check `id -u` and exit early with a clear message — re-run with `sudo`. |

## Still stuck?

Re-run `scripts/diagnose.sh` and include its full output (safe to share — no
secrets are ever printed by it) along with the last ~50 lines of
`storage/logs/catcam.log` from around when the problem occurred.
