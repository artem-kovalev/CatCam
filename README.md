# CatCam

A private cat-monitoring system for a Raspberry Pi 4: watches a camera for
motion, records a short clip, and delivers it as a circular Telegram video
note to a single authorized owner — with live video reachable remotely only
over Tailscale (or a plain LAN), never exposed on the public internet.

> Status: core build complete (tasks 1–10 of `tasks/summary.md`). Security
> audit and documentation completion (task 11) is this document's own task;
> end-to-end hardware sign-off (task 12) is the one remaining step. See
> `tasks/summary.md` for the full status table.

## Features

- CSI (libcamera) or USB (V4L2) camera support, auto-detected/configured via
  `config/config.yaml`
- Motion detection with configurable sensitivity, region of interest, and
  minimum duration before a clip is triggered
- Automatic clip recording with pre-roll, so the clip shows what triggered
  it, not just the tail of the motion
- Clips converted to circular Telegram video notes (with an automatic
  size/resolution retry if the first encode is too large), falling back to a
  regular video if conversion fails
- Per-delivery cooldown (configurable, persisted across restarts) to avoid
  notification spam from sustained motion
- A private, owner-only Telegram bot — every command rejects anyone but the
  configured `TELEGRAM_USER_ID`/`TELEGRAM_CHAT_ID` before running:
  `/status`, `/cooldown [minutes]`, `/notifications_on|off`, `/snapshot`,
  `/record <seconds>`, `/stream`, `/restart_service`, `/help`
- Secure remote video access via MediaMTX (WebRTC primary, HLS fallback,
  RTSP for VLC) over Tailscale or the plain LAN — password + IP-allowlist
  gated, never a forwarded public port
- A failed-delivery retry queue and a disk-quota enforcer, so a transient
  Telegram outage or a burst of motion events doesn't lose clips or fill the
  SD card
- systemd-based deployment (`scripts/install.sh`/`uninstall.sh`) with
  autostart on boot; Docker Compose documented as an unverified alternative

## Documentation

- [Configuration](docs/configuration.md) — every config key, source, default, and valid range
- [Hardware setup](docs/hardware.md) — supported camera modules, wiring, USB/UVC notes
- [Raspberry Pi setup](docs/raspberry-pi-setup.md) — camera detection and OS-level setup
- [Streaming](docs/streaming.md) — MediaMTX/Tailscale setup, camera-ownership contract, network exposure model, viewing/troubleshooting
- [Telegram bot setup](docs/telegram-setup.md) — creating the bot, finding your ids, populating `.env`, the full command reference
- [Architecture](docs/architecture.md) — how the pieces fit together: frame loop, motion → recording → delivery pipeline, logging, health, retry queue
- [Deployment](docs/deployment.md) — systemd install/upgrade/rollback/uninstall, plus the documented-only Docker Compose alternative
- [Operations](docs/operations.md) — day-2: status checks, updating, log rotation, config changes, backups
- [Security](docs/security.md) — threat model, secret storage, token rotation, `/restart_service` privilege scoping, keeping the host updated
- [Troubleshooting](docs/troubleshooting.md) — symptom → cause → fix table, log locations
- [Testing](docs/testing.md) — running `pytest`, what's unit-tested vs. hardware-dependent, the manual end-to-end test procedure

## Quick start

Full install/deploy instructions (the recommended path for anything beyond
local development) live in `docs/deployment.md`:

```bash
git clone <this-repo-url> catcam-src
cd catcam-src
sudo apt update
sudo apt install -y rpicam-apps v4l-utils ffmpeg python3-venv rsync wget
sudo scripts/install.sh
# then edit /etc/catcam/.env, /etc/catcam/mediamtx.env, /opt/catcam/config/config.yaml
sudo systemctl restart catcam.service catcam-stream.service
scripts/diagnose.sh
```

For local development instead:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env               # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, TELEGRAM_CHAT_ID
cp config/config.example.yaml config/config.yaml

pytest tests/
```

## Repository layout

See `AGENT_PROMPT_EN.md` for the full specification and `tasks/` for the
task-by-task implementation breakdown and status.
