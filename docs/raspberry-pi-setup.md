# Raspberry Pi Setup

Setup instructions for a Raspberry Pi 4 running Raspberry Pi OS Bookworm (or
newer). This document grows as later tasks add streaming, deployment, and
Telegram setup steps; this section covers the camera only.

## Camera

### 1. Connect the camera

See `docs/hardware.md` for CSI ribbon-cable orientation and USB/UVC notes.

### 2. Install camera tooling

Raspberry Pi OS Bookworm ships `libcamera` by default, but the CLI tools
(`rpicam-apps`) and V4L2 utilities may need installing:

```bash
sudo apt update
sudo apt install -y rpicam-apps v4l-utils
```

- `rpicam-apps` provides `rpicam-hello`, `rpicam-vid`, `rpicam-still` — the
  current CSI camera tools (Bookworm renamed these from `libcamera-*`; the
  old names still work as symlinks but are considered legacy).
- `v4l-utils` provides `v4l2-ctl`, used for USB/UVC camera detection.

No `raspi-config` camera-interface toggle is needed on Bookworm — CSI cameras
are auto-detected.

### 3. Verify detection

Run the check script from the repo root:

```bash
scripts/check_camera.sh
```

This reports CSI status via `rpicam-hello --list-cameras` and USB status via
`v4l2-ctl --list-devices`, with a clear PASS/FAIL summary, and exits non-zero
if no camera is found — safe to use in `install.sh`/`diagnose.sh` (task 10).

### 4. Set `camera.type` in config

In `config/config.yaml`:

```yaml
camera:
  type: csi   # or "usb"
  device: /dev/video0   # only used when type: usb
  resolution: [1280, 720]
  framerate: 15
```

See `docs/configuration.md` for the full key reference.

## Next steps

- **Streaming**: `docs/streaming.md` (MediaMTX, Tailscale/LAN access).
- **Telegram bot**: `docs/telegram-setup.md`.
- **Full deployment (systemd install, autostart, upgrade/rollback)**:
  `docs/deployment.md`.
- **Day-2 operations**: `docs/operations.md`.
