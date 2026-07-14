# Hardware

CatCam targets a Raspberry Pi 4 with either a CSI ribbon-cable camera module
or a USB/UVC webcam. Only one is needed; `camera.type` in `config/config.yaml`
selects which.

## CSI camera modules (recommended)

Supported: any camera module compatible with Raspberry Pi OS Bookworm's
`libcamera`/`rpicam-apps` stack —

- Raspberry Pi Camera Module 2 (Sony IMX219)
- Raspberry Pi Camera Module 3 (Sony IMX708, standard/wide/NoIR variants)
- Raspberry Pi High Quality (HQ) Camera (Sony IMX477)
- Most third-party CSI modules built on the same sensors (e.g. Arducam),
  provided they ship a Bookworm-compatible libcamera driver/overlay.

### Connecting the ribbon cable

1. Power off the Pi before connecting/disconnecting the camera.
2. On a Raspberry Pi 4, use the **CAM** connector (not the DISPLAY/DSI
   connector) — it's the one nearest the USB-C power port.
3. Gently pull up the plastic tabs on both sides of the connector.
4. Insert the ribbon cable with the **blue backing facing the USB/Ethernet
   ports** (i.e. contacts facing the HDMI ports) and push it in evenly.
5. Push the tabs back down to clamp the cable in place.
6. Power on. Raspberry Pi OS Bookworm auto-detects CSI cameras — no
   `raspi-config` toggle or `/boot/firmware/config.txt` edit is required for
   most official modules.

### Verifying

Run `scripts/check_camera.sh` (added by this task) or directly:

```bash
rpicam-hello --list-cameras
```

A detected camera prints its sensor name and supported modes. If you see "no
cameras available", double-check the ribbon orientation and seating, and that
you're using the CAM connector, not DISPLAY.

## USB/UVC cameras

Any webcam implementing the standard USB Video Class (UVC) specification
works — no special driver needed. CatCam accesses it via V4L2 as
`/dev/video0` (or another index, configurable via `camera.device`).

Notes:

- Plug in before starting `catcam.service`, or restart the service after
  plugging in.
- If multiple `/dev/video*` devices appear (common — some UVC cameras expose
  more than one node), confirm the correct one with `v4l2-ctl --list-devices`
  and set `camera.device` accordingly.
- MJPEG- or YUYV-capable webcams both work; CatCam configures resolution and
  framerate via OpenCV's `VideoCapture`, which negotiates the format
  automatically.

### Verifying

```bash
v4l2-ctl --list-devices
```

Lists each detected USB video device and its associated `/dev/video*` nodes.

## Choosing between CSI and USB

- **CSI** is recommended: lower CPU overhead (hardware encode via
  `rpicam-vid`), no USB bandwidth contention, official Raspberry Pi Camera
  Module ecosystem.
- **USB** is a reasonable fallback if you already own a UVC webcam or need
  cable-length flexibility (CSI ribbon cables are short).
